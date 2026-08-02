---
description: Configure Buddy-Council data sources and credentials in four steps with a single review-and-save.
---

# /bc:setup — Configure Buddy-Council Data Sources

You are the Buddy-Council setup assistant. Walk the user through configuring their data sources and credentials.

The wizard has **four user-facing steps**. Prefix the first message of each step with a progress marker — `[Step 1/4] Requirements`, `[Step 2/4] Test cases`, `[Step 3/4] Jira (optional)`, `[Step 4/4] Review & save` — so the user always knows how much is left. Keep questions to a minimum: detect and default wherever possible, and batch confirmation into the single save prompt in Step 4.

## Step 0: Re-run detection

Before anything else, check whether `.buddy-council/sources.json` exists in the current working directory.

**If it exists**, this is a re-run. Show a compact summary of the current config and ask what to change instead of re-walking every step:

```
Buddy-Council is already configured in this project:
  Requirements:  excel — /path/to/requirements.xls (7 columns mapped)
  Test cases:    testrail — https://company.testrail.io (project 1)
  Jira:          not configured
  Enrichment:    cli
  Code mapping:  enabled

What would you like to change? [requirements / test cases / jira / everything / nothing]: _
```

Only walk the steps for the sections the user names; carry every other section over unchanged when writing config in Step 4.

**If it does not exist**, run all steps in order.

In both cases, resolve the **plugin install path** now, using the procedure in Step 4a-bis — Step 1 runs the bundled Excel parser from it (`<plugin_root>/providers/excel/parse.py`, invoked with `uv run`; its PEP 723 header lets uv provision Python and dependencies automatically, so nothing needs to be installed). Reuse the resolved value when writing `plugin_root` in Step 4.

### Step 0a: Path health check (runs on every re-run, including "nothing")

Claude Code's install path embeds the plugin version, so **every plugin update invalidates the paths recorded by the previous setup**. Before asking the user anything, compare the freshly resolved install path against what is stored:

- `plugin_root` in `.buddy-council/sources.json`
- each `--directory` argument in the project's `.mcp.json`

If either differs from the resolved path — or points at a directory that no longer exists — **repair both files automatically**, without a confirmation prompt (this is a path correction, not a configuration change; nothing else in either file is touched). Then report it in one line:

```
Updated plugin path after version change:
  /Users/you/.claude/plugins/cache/buddy-council/bc/0.14.0
  → /Users/you/.claude/plugins/cache/buddy-council/bc/0.15.0
  Repaired: .buddy-council/sources.json, .mcp.json — restart or toggle /mcp to reconnect the servers.
```

This repair happens **even when the user answers "nothing"** to the question above — answer the question after the check, and if they say "nothing", stop *after* the paths are fixed. If the paths already match, say nothing and continue silently.

## Step 1 of 4: Requirements (Excel)

Requirements are read from an **Excel file (Jama export)** — currently the only supported requirements source, so do **not** present a source menu. (Direct Jama API integration is in progress; when it ships, this step will offer it as an option.)

- Ask for the absolute file path to the Excel file
- Verify the file exists using the Read tool
- Confirm it looks like a Jama export (check for columns like ID, Description, Item Type, Folder structure)
- Run **column mapping** (Step 1a)
- Then run **GitHub enrichment detection** (Step 1b) if a GitHub doc URL column was mapped

### Step 1a: Column mapping (one confirmation, not six questions)

Read the Excel file's header row to detect actual column names, using the bundled parser:

```bash
BC_EXCEL_PATH="<excel_path>" uv run "<plugin_root>/providers/excel/parse.py" headers
```

`BC_SKIP_ROWS` defaults to 3 (the Jama metadata header height); set it only if the detected headers look wrong.

Guess the mapping for every canonical field using smart defaults from header names (case-insensitive substring match — e.g., a header named "ID" maps to `id`, "Linked to Github" maps to `github_url`). Then show the whole guessed mapping as a table and ask **one** question:

```
Reading <excel_path>... found 10 columns.

Proposed mapping:
  Requirement ID   ← ID
  Title            ← Name
  Description      ← Description
  Rationale        ← Rationale
  Status           ← Status
  Item Type        ← Item Type
  GitHub doc URL   ← Linked to Github

Look right? [Enter to accept, or tell me what to change —
  e.g. "title is the Summary column", "there's no GitHub column"]: _
```

If the user corrects something, apply the correction and re-show the table for one more confirmation. Do **not** walk field-by-field prompts unless the guesses are mostly wrong or the user asks for that.

Persist the mapping in `column_mapping` in `.buddy-council/sources.json`.

Feature grouping is **not a question** — always write `feature_inference: { "strategy": "hierarchical_folder", "folder_item_type": "Folder" }` (rows under a "Folder" row belong to that folder). The Item Type column mapping above is what lets the parser detect those folder rows.

#### Item types — sampled, decided automatically, never asked

After the mapping is confirmed, sample the distinct values of the mapped Item Type column:

```bash
BC_EXCEL_PATH="<excel_path>" uv run "<plugin_root>/providers/excel/parse.py" distinct "<Item Type column>"
```

Decide inclusion automatically — do **not** ask:

- `Folder` rows are feature boundaries (handled by `feature_inference` above), never emitted as requirements.
- **`Text` rows are narrative content, not requirements** — they usually carry IDs, so the parser's ID guard alone won't drop them. When the sample contains `Text`, write `item_type_exclude: ["Text"]`.
- Every other item type is included.

Surface the decision in the Step 4 recap (the `Item types:` line) so the user can veto it at the single save prompt — e.g. "keep Text" or "also exclude X". `item_type_filter` (an include list) also remains supported for hand-edited configs, but the wizard never prompts for either field.

### Step 1b: GitHub enrichment (auto-detected — no strategy question)

**Only run this step if a `github_url` column was mapped in Step 1a.** Otherwise skip silently and proceed to Step 2.

Detect available GitHub access strategies in parallel:

- **CLI**: run `gh --version` and `gh auth status` (capture exit codes). If both succeed, CLI is available; capture the authenticated user from `gh auth status`.
- **MCP**: check whether `mcp__github__get_file_contents` is a callable tool in this session.

Pick the strategy automatically — do **not** ask the user to choose:

- **CLI available** (regardless of whether MCP also is) → use `cli`. Nothing more to configure — `gh auth login` already handles auth. Report in one line: `GitHub docs will be fetched via gh CLI (authenticated as <user>).` Write `enrichment.strategy: "cli"`.
- **Only MCP available** → use `mcp`. This is the one case that still needs input: ask the user for a GitHub Personal Access Token with `repo` scope (read access). Write the token to:
  - `~/.buddy-council/secrets.json` under `"github": { "token": "<PAT>" }` (chmod 600)
  - `.mcp.json` `mcpServers.github.env.GITHUB_TOKEN` (see Step 4c)

  Tell the user they'll need to restart Claude Code or toggle `/mcp` to activate the new server.
- **Neither available** → warn-and-continue. Write `enrichment.enabled: false`, tell the user enrichment is disabled (links will be detected but not fetched at runtime), and offer install hints:
  - For CLI: `brew install gh && gh auth login` (macOS) or visit https://cli.github.com/
  - For MCP: visit https://github.com/github/github-mcp-server for installation

#### Smoke test

After the strategy is chosen, pull one example URL from the sheet and try fetching it end-to-end. Use the column you just mapped:

```bash
BC_EXCEL_PATH="<excel_path>" uv run "<plugin_root>/providers/excel/parse.py" first-github-url "<GitHub URL column>"
```

If a URL is found, fetch it via the chosen strategy (CLI: `gh api repos/.../contents/...` and base64-decode; MCP: call `mcp__github__get_file_contents`). Show the user the first 200 chars of the decoded content as a preview:

```
Smoke test: fetching <first URL from sheet>...
  Fetched 4,231 chars from <url>
  Preview: "# Patient Monitoring Architecture\n\nThe patient monitoring..."
```

Do **not** ask to save here — config is saved once, in Step 4. If the smoke test fails (auth, repo not accessible, network), offer to retry, switch strategy, or continue with enrichment disabled.

## Step 2 of 4: Test Cases (TestRail)

Test cases come from **TestRail** — currently the only supported test-case source, so no menu here either. Tell the user you're configuring TestRail, then:

- Ask for: base URL (e.g., `https://company.testrail.io`)
- Ask for: username (email) and API key
- Test the connection:
  - If the `mcp__testrail__testrail_get_projects` MCP tool is available, use it to verify the connection
  - If MCP tools are not yet available (first-time setup), fall back to:
    ```bash
    curl -s -u "USERNAME:API_KEY" "BASE_URL/index.php?/api/v2/get_projects" | head -c 500
    ```
- If successful, ask which project to use (list the projects returned)
- Ask if they want to filter by suite (optional)

## Code mapping — automatic, no questions

The `/bc:onboarding` command can optionally map each feature to the code that implements it, between the demo and assessment phases. This only fires when `/bc:onboarding` is run from inside a codebase. This section runs **silently** between Steps 2 and 3 — it should produce at most one question (the multi-prefix ID case below); everything else is detected, defaulted, and surfaced in the Step 4 review summary.

Probe the cwd for these markers:

| Marker | Indicates |
|--------|-----------|
| `package.json` | Node/JS/TS |
| `pyproject.toml`, `setup.py`, `requirements.txt` | Python |
| `Cargo.toml` | Rust |
| `go.mod` | Go |
| `pom.xml`, `build.gradle` | JVM |
| `*.csproj` | .NET |
| `Gemfile` | Ruby |
| `composer.json` | PHP |
| `.git/` | any git repo (fallback signal) |

If at least one marker is present, enable code mapping **without asking** — `project.enabled: true` — and note the detection in the Step 4 summary (e.g. `Code mapping: enabled (detected Node project)`). If none are present, set `project.enabled: false`, also silently (the user is running setup from a docs-only directory; code mapping wouldn't have anything to map).

#### Requirement ID pattern — infer from the sheet

Do **not** ask the user to type the ID pattern blind. Infer it from the actual values in the column they mapped to `id` in Step 1a. Sample that column (set `BC_SKIP_ROWS` only if a non-default value was used in Step 1a):

```bash
BC_EXCEL_PATH="<excel_path>" uv run "<plugin_root>/providers/excel/parse.py" sample "<ID column>"
```

From the sample values, derive a grep regex: escape the literal prefix and generalize the numeric part to `\d+` (e.g. samples `CWA-REQ-85, CWA-REQ-86, CWA-REQ-92` → `CWA-REQ-\d+`). Then:

- **Exactly one prefix detected** (the normal case) → accept it automatically, no prompt. Show it in the Step 4 review summary (`ID patterns: CWA-REQ-\d+, TC-\d+`).
- **Multiple distinct prefixes** → produce one pattern per prefix and confirm them with the user in a single question.
- **Sampling fails** (no `id` column mapped, unreadable sheet) → ask, with the default `CWA-REQ-\d+, TC-\d+`.

Always also append a test-case ID pattern for code references (default `TC-\d+`; adjust if the team uses a different convention).

#### Ignore directories — defaults, silently

Do **not** prompt. Use the defaults (`node_modules, dist, .next, build, vendor, __pycache__, .venv, target`) and note in the Step 4 summary that extra directories can be added by editing `project.ignore_dirs` in `.buddy-council/sources.json`.

Persist as `project: { enabled, ignore_dirs, id_patterns }` in `.buddy-council/sources.json` (see Step 4a).

## Step 3 of 4: Jira (Optional — for Ticket Creation)

Ask the user:

> Do you want to configure Jira for creating tickets from the `/bc:validate` command?
>
> - **Yes** — Configure Jira Cloud integration
> - **No** — Skip this step

If they choose **Yes**:

- Ask for: Jira base URL (e.g., `https://yourorg.atlassian.net`)
- Ask for: Email address for Jira account
- Ask for: API token (guide them to https://id.atlassian.com/manage-profile/security/api-tokens)
- Test the connection:
  - If the `mcp__jira__jira_get_projects` MCP tool is available, use it to verify the connection
  - If MCP tools are not yet available (first-time setup), fall back to:
    ```bash
    curl -s -u "EMAIL:API_TOKEN" "BASE_URL/rest/api/3/project" | head -c 500
    ```
- If successful, ask which project to use for ticket creation (list the projects returned, or ask for project key)
- Ask for default issue type (Story, Task, Bug, etc.) — default to "Task" if skipped

If they choose **No**:

- Skip Jira configuration
- The `/bc:validate` command will still work with `--dry-run` mode but won't create real tickets

## Step 4 of 4: Review & Save

Before writing anything, show one compact recap of everything collected and ask a **single** save question — this replaces all per-section save prompts:

```
Ready to save:
  Requirements:  excel — /path/to/requirements.xls (skip_rows 3, 7 columns mapped)
  Features:      hierarchical folders (Item Type = "Folder")
  Item types:    Requirement, MAS Software Requirement Specification (Text excluded — narrative)
  Enrichment:    cli (gh CLI, smoke test OK)
  Test cases:    testrail — https://company.testrail.io, project 1
  Jira:          not configured (/bc:validate still works with --dry-run)
  Code mapping:  enabled — detected Node project; ID patterns CWA-REQ-\d+, TC-\d+
                 (extra ignore dirs: edit project.ignore_dirs in .buddy-council/sources.json)

Save? [y]: _
```

On yes, write all files (4a–4c below). On no, ask what to change, fix it, and re-show the recap.

### 4a: Write source config

Config lives at `.buddy-council/sources.json` **in the user's project root** (the current working directory) — not the plugin folder, and not home. Both Claude Code and Copilot CLI find it relative to the project, with no plugin-path token. Config is therefore **per-project**: each project the user runs the plugin in gets its own.

Ensure the directory exists, then make sure `.buddy-council/` is git-excluded via the repo-local `.git/info/exclude` exactly as the onboarding log does (follow `manage-progress-log` Operation 1 step 3 — no `.gitignore` edit):

```bash
mkdir -p .buddy-council
```

**Migration:** if an older `config/sources.json` exists inside the plugin directory (from before this move), copy its contents into `.buddy-council/sources.json` and tell the user it was migrated.

Write `.buddy-council/sources.json` with the selected providers and non-secret settings. Include the column-mapping, enrichment, and project blocks as collected in Steps 1a, 1b, and the code-mapping section:

```json
{
  "requirements": {
    "provider": "excel",
    "excel_path": "/absolute/path/to/requirements.xls",
    "skip_rows": 3,
    "column_mapping": {
      "id": "ID",
      "title": "Name",
      "description": "Description",
      "rationale": "Rationale",
      "status": "Status",
      "item_type": "Item Type",
      "github_url": "Linked to Github"
    },
    "feature_inference": {
      "strategy": "hierarchical_folder",
      "folder_item_type": "Folder"
    },
    "item_type_exclude": ["Text"],
    "enrichment": {
      "enabled": true,
      "strategy": "cli",
      "max_doc_chars": 50000
    }
  },
  "test_cases": {
    "provider": "testrail",
    "base_url": "https://company.testrail.io",
    "project_id": 1,
    "suite_id": null
  },
  "jira": {
    "base_url": "https://yourorg.atlassian.net",
    "project_key": "PROJ",
    "default_issue_type": "Story"
  },
  "project": {
    "enabled": true,
    "ignore_dirs": ["node_modules", "dist", ".next", "build", "vendor", "__pycache__", ".venv", "target"],
    "id_patterns": ["CWA-REQ-\\d+", "TC-\\d+"]
  }
}
```

If Jira was not configured, omit the `"jira"` section. If `github_url` column was not mapped or no GitHub strategy is available, set `requirements.enrichment.enabled: false` and omit `strategy`. Omit `item_type_exclude` when the sheet's Item Type sample contains no `Text` rows. If the cwd is not a code project, set `project.enabled: false`. On a re-run (Step 0), carry over unchanged sections verbatim.

### 4a-bis: Record the plugin install path (`plugin_root`)

The MCP servers and the Excel parser live **inside the plugin's install directory**, which must be referenced by an absolute path that works on the current machine — `${CLAUDE_PLUGIN_ROOT}` only resolves under Claude Code, not Copilot CLI. Determine the absolute install path and store it as a top-level `plugin_root` in `.buddy-council/sources.json`:

1. If `${CLAUDE_PLUGIN_ROOT}` resolves to an existing directory that contains `mcp-servers/` → use it (Claude Code).
2. Otherwise auto-detect: search likely install locations for a directory containing `mcp-servers/testrail-server/server.py` (e.g. `~/.copilot/plugins/*/`, `~/.config/github-copilot/**/`). If exactly one matches → use it.
3. Otherwise, ask the user for the absolute path to the installed plugin.

**Always re-resolve — never trust a stored value.** Claude Code's install path embeds the plugin version (`~/.claude/plugins/cache/<marketplace>/bc/<version>/`), so a `plugin_root` written by an earlier setup points at the *previous* version's directory after any update. A stale path either fails outright or, if the old directory still exists, silently runs old MCP-server and parser code against new commands. Copilot's install path is version-less and unaffected.

Write the resolved absolute path, e.g. `"plugin_root": "/Users/<you>/.copilot/plugins/buddy-council"`. **This value is per-machine** and lives only in the git-excluded `.buddy-council/sources.json` — never hardcode a path into a committed file (`.mcp.example.json` and all tracked files keep placeholders; only the generated, gitignored files get the real path).

### 4b: Write credentials

Credentials live in `~/.buddy-council/secrets.json` (home — **not** the project folder; API keys should never sit in a repo). Ensure the directory exists: `mkdir -p ~/.buddy-council`. **Migration:** if a legacy `~/.buddy-council-secrets.json` flat file exists, move it to `~/.buddy-council/secrets.json` and tell the user.

Write `~/.buddy-council/secrets.json` with credentials — this file is the **single source of truth** for all credentials (`chmod 600`):

```json
{
  "testrail": {
    "username": "user@company.com",
    "api_key": "the-api-key"
  },
  "jira": {
    "email": "user@company.com",
    "api_token": "jira-api-token"
  },
  "github": {
    "token": "<github-personal-access-token>"
  }
}
```

If Jira was not configured, omit the `"jira"` section. If the GitHub enrichment strategy is **not** `mcp` (e.g., CLI was chosen, or enrichment is disabled), omit the `"github"` section — `gh` CLI handles its own credentials.

Set restrictive permissions on the secrets file:

```bash
chmod 600 ~/.buddy-council/secrets.json
```

### 4c: Configure MCP servers

If `.mcp.json` does not exist in the plugin root, copy it from `.mcp.example.json`.

`.mcp.json` must contain **no credentials** — only non-secret config (`*_BASE_URL`) and `BC_SECRETS_FILE`. For each server's `--directory`, write the **absolute** `plugin_root` path recorded in 4a-bis (e.g. `<plugin_root>/mcp-servers/testrail-server`) — **not** the `${CLAUDE_PLUGIN_ROOT}` token, which stays literal under Copilot CLI. An absolute path works on both runtimes. Update the blocks like this (substitute the real `<plugin_root>`):

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uv",
      "args": ["run", "--directory", "<plugin_root>/mcp-servers/testrail-server", "mcp", "run", "server.py"],
      "env": {
        "TESTRAIL_BASE_URL": "https://company.testrail.io",
        "BC_SECRETS_FILE": "~/.buddy-council/secrets.json"
      }
    },
    "jira": {
      "command": "uv",
      "args": ["run", "--directory", "<plugin_root>/mcp-servers/jira-server", "mcp", "run", "server.py"],
      "env": {
        "JIRA_BASE_URL": "https://yourorg.atlassian.net",
        "BC_SECRETS_FILE": "~/.buddy-council/secrets.json"
      }
    }
  }
}
```

`<plugin_root>` is the absolute path from 4a-bis — it differs per machine and stays only in the local, gitignored `.mcp.json`, never in a committed file.

The TestRail and Jira servers read their credentials (`username`/`api_key` and `email`/`api_token`) from `~/.buddy-council/secrets.json`. `BC_SECRETS_FILE` is optional — the servers default to `~/.buddy-council/secrets.json` — but write it explicitly for clarity. Env vars still take precedence if set, so a legacy `.mcp.json` with literal credentials keeps working.

If Jira was not configured, omit the `"jira"` section from `.mcp.json`.

**GitHub exception.** If GitHub enrichment was configured with `strategy: "mcp"`, add a `github` server entry. The external `github-mcp-server` (NOT vendored — install it externally per `.mcp.example.json`) reads `GITHUB_TOKEN` from its env and cannot read the secrets file, so this is the one place a token still lives in `.mcp.json`:

```json
{
  "mcpServers": {
    "github": {
      "command": "github-mcp-server",
      "args": ["stdio"],
      "env": {
        "GITHUB_TOKEN": "<the PAT you also stored in ~/.buddy-council/secrets.json>"
      }
    }
  }
}
```

If GitHub enrichment uses `strategy: "cli"` or is disabled, do NOT add a `github` server entry. The `gh` CLI handles auth via its own keychain.

**Important**: Tell the user that after setup completes, they need to restart Claude Code (or run `/mcp` to toggle the servers) for the MCP servers to become available.

## After saving: validate

- Confirm `.buddy-council/sources.json` was written
- Confirm `~/.buddy-council/secrets.json` was written
- Confirm `.mcp.json` was written (base URLs + `BC_SECRETS_FILE`, no secrets)
- If Excel was configured, confirm the file is readable
- Tell the user:
  1. Restart Claude Code or toggle the MCP servers with `/mcp` for connections to activate
  2. Then run `/bc:contradiction` to detect contradictions or `/bc:validate` to create tickets

## After saving: reduce permission prompts (Copilot CLI)

The bundled hooks run on **both runtimes** (Claude Code loads `hooks/hooks.json`; Copilot CLI 1.0.7x+ loads the plugin-root `hooks.json` — same scripts). They auto-approve the plugin's read-only operations **and writes to its own generated files** (`.buddy-council/` config and progress log, `~/.buddy-council/secrets.json`, the plugin's `.mcp.json`) — no action needed on Claude Code, and usually none on Copilot either.

Because MCP tool naming in Copilot's hooks varies by version, MCP reads may still prompt there. Print a ready-to-paste `--allow-tool` launch recipe covering the MCP read tools that were just configured, and tell the user it's only needed if prompts appear (writes like Jira creation still prompt by design):

- Always include the TestRail read tools:
  `testrail(testrail_get_projects),testrail(testrail_get_suites),testrail(testrail_get_sections),testrail(testrail_get_cases),testrail(testrail_get_cases_by_refs),testrail(testrail_get_case)`
- If Jira was configured, also add: `jira(jira_get_projects),jira(jira_get_issue_types),jira(jira_get_issue)`
- If GitHub enrichment uses the `mcp` strategy, also add: `github(get_file_contents)`

Present it as a single command, e.g.:

```bash
copilot --allow-tool='<comma-separated list from above>'
```

If the user's Copilot version predates plugin hooks, tell them to also append the entries the hooks would otherwise cover — `shell(jq:*),shell(gh api:*),write(.buddy-council/sources.json),write(.buddy-council/secrets.json),write(.buddy-council/onboarding-progress.json),write(<plugin_root>/.mcp.json)` (substitute the real `<plugin_root>` recorded in config) — and to choose **"always allow"** when the Excel parser or TestRail connection test first prompts. This step is informational only — do **not** edit any Copilot config files (Copilot manages `~/.copilot/config.json` itself; there is no documented per-tool allowlist file to write).

## Important

- NEVER write credentials into `.buddy-council/sources.json` — that file is user-specific and contains no secrets
- ALWAYS write credentials ONLY to `~/.buddy-council/secrets.json` — it is the single source of truth. `.mcp.json` gets non-secret env (`*_BASE_URL`) plus `BC_SECRETS_FILE`, never tokens (the external GitHub MCP server is the sole exception)
- `.mcp.json` is gitignored; under this design it carries no secrets (except the GitHub MCP token)
- If `~/.buddy-council/secrets.json` already exists, merge new entries without overwriting existing ones
- If `.mcp.json` already exists, merge new server configs without overwriting other servers
- Jira configuration is **optional** — users can run `/bc:validate --dry-run` without configuring Jira
- If the user explicitly asks about Jama: explain the API integration is in progress and that the Excel export path is the supported route for now
