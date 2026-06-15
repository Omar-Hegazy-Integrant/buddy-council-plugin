# /bc:setup — Configure Buddy-Council Data Sources

You are the Buddy-Council setup assistant. Walk the user through configuring their data sources and credentials.

## Step 1: Requirements Source

Ask the user:

> Which tool are you using for hosting requirements?
>
> 1. **Jama** (not yet supported — authentication in progress)
> 2. **Excel sheet** (Jama export file)

If they choose **Excel**:

- Ask for the absolute file path to the Excel file
- Verify the file exists using the Read tool
- Confirm it looks like a Jama export (check for columns like ID, Description, Item Type, Folder structure)
- Run the **column-mapping wizard** (Step 1a) so the parser knows which column means what
- Then run the **GitHub enrichment wizard** (Step 1b) if the user mapped a GitHub doc URL column

### Step 1a: Column-mapping wizard (Excel only)

Read the Excel file's header row to detect actual column names. Use a Python one-liner via Bash:

```bash
python3 - <<'PYEOF'
import pandas as pd, json, sys, os
df = pd.read_excel(os.environ['BC_EXCEL_PATH'], skiprows=int(os.environ.get('BC_SKIP_ROWS', '3')))
print(json.dumps([str(c) for c in df.columns]))
PYEOF
```

Show the user the detected column headers and prompt them to map each canonical field. Use smart defaults from header names (case-insensitive substring match — e.g., a header named "ID" defaults to `id`, "Linked to Github" defaults to `github_url`):

```
Reading <excel_path>...
Found these columns: [ID, Name, Description, Rationale, Item Type, Status, Jira ID, Linked to Github, Tags, Configuration]

Map each canonical field to a column (Enter to accept the guess, leave blank to skip):
  Requirement ID column [ID]: _
  Title column [Name]: _
  Description column [Description]: _
  Status column [Status]: _
  Item Type column [Item Type]: _
  GitHub doc URL column [Linked to Github]: _

Feature grouping:
  1. Hierarchical — rows under a "Folder" row belong to that folder (current)
  2. By column — one column literally names each requirement's feature
  3. None
  Choose [1]: _
```

If the user picks strategy 2, ask for the feature column name. Persist the mapping in `column_mapping` and the strategy under `feature_inference` in `.buddy-council/sources.json`.

Also offer to set `item_type_filter` — show a brief one-liner: *"Only include rows whose Item Type matches one of these (comma-separated, blank for no filter):"*. Common picks: `Functional Requirement, Requirement`.

### Step 1b: GitHub enrichment wizard

**Only run this step if the user mapped a `github_url` column in Step 1a.** Otherwise skip silently and proceed to Step 2.

Detect available GitHub access strategies in parallel:

- **CLI**: run `gh --version` and `gh auth status` (capture exit codes). If both succeed, CLI is available; capture the authenticated user from `gh auth status`.
- **MCP**: check whether `mcp__github__get_file_contents` is a callable tool in this session.

Tell the user what was detected and offer a choice, defaulting to CLI when both are available:

```
A GitHub doc URL column was configured. Setting up GitHub access...
  Detected: gh CLI v2.x (authenticated as <user>) [ok]
  Detected: github-mcp-server <available | not installed>

Strategy [cli]: _
```

**If neither is available**: warn-and-continue. Write `enrichment.enabled: false` to config, tell the user enrichment is disabled (links will be detected but not fetched at runtime), and offer install hints:
- For CLI: `brew install gh && gh auth login` (macOS) or visit https://cli.github.com/
- For MCP: visit https://github.com/github/github-mcp-server for installation

**If CLI is chosen**: nothing more to configure — `gh auth login` already handles auth. Write `enrichment.strategy: "cli"`.

**If MCP is chosen**: ask the user for a GitHub Personal Access Token with `repo` scope (read access). Write the token to:
- `~/.buddy-council/secrets.json` under `"github": { "token": "<PAT>" }` (chmod 600)
- `.mcp.json` `mcpServers.github.env.GITHUB_TOKEN` (see Step 4c)

Tell the user they'll need to restart Claude Code or toggle `/mcp` to activate the new server.

#### Smoke test

After the strategy is configured, pull one example URL from the sheet and try fetching it end-to-end. Use the column you just mapped:

```bash
python3 - <<'PYEOF'
import pandas as pd, os, re
df = pd.read_excel(os.environ['BC_EXCEL_PATH'], skiprows=int(os.environ.get('BC_SKIP_ROWS', '3')))
col = os.environ['BC_GITHUB_URL_COL']
for v in df[col].dropna().astype(str):
    for u in re.split(r'[\s,;]+', v):
        if u.startswith('https://github.com/'):
            print(u); raise SystemExit
PYEOF
```

If a URL is found, fetch it via the chosen strategy (CLI: `gh api repos/.../contents/...` and base64-decode; MCP: call `mcp__github__get_file_contents`). Show the user the first 200 chars of the decoded content as a preview:

```
Smoke test: fetching <first URL from sheet>...
  Fetched 4,231 chars from <url>
  Preview: "# Patient Monitoring Architecture\n\nThe patient monitoring..."

Save config? [y]: _
```

If the smoke test fails (auth, repo not accessible, network), offer to retry, switch strategies, or save the config with enrichment disabled.

If they choose **Jama**:

- Inform them that Jama integration is in progress and suggest using the Excel export as a temporary fallback
- If they still want Jama, collect: base URL, username, API key
- Store credentials in `~/.buddy-council/secrets.json` under the `jama` key

## Step 2: Test Cases Source

Ask the user:

> Which tool are you using for hosting test cases?
>
> 1. **TestRail** (supported)

For **TestRail**:

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

## Step 2.5: Code Mapping (auto-detected project)

The `/bc:onboarding` command can optionally map each feature to the code that implements it, between the demo and assessment phases. This only fires when `/bc:onboarding` is run from inside a codebase. Setup auto-detects whether the current working directory looks like a code project and configures accordingly.

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

If at least one marker is present, report it and confirm:

```
Detected: this looks like a <language> project (saw <marker>).
Code mapping will run during /bc:onboarding to show where each feature
lives in the codebase.

Enable code mapping during onboarding? [y]: _
```

If none of the markers are present, set `project.enabled: false` without prompting (the user is running setup from a docs-only directory; code mapping wouldn't have anything to map).

Configure two more knobs:

#### Requirement ID pattern — infer from the sheet, then confirm

Do **not** ask the user to type the ID pattern blind. Infer it from the actual values in the column they mapped to `id` in Step 1a. Sample that column (set `BC_ID_COLUMN` to the mapped column name and `BC_SKIP_ROWS` to the value used in Step 1a):

```bash
python3 - <<'PYEOF'
import pandas as pd, os, json
df = pd.read_excel(os.environ['BC_EXCEL_PATH'], skiprows=int(os.environ.get('BC_SKIP_ROWS', '3')))
col = os.environ['BC_ID_COLUMN']
vals = [s for s in (str(v).strip() for v in df[col].dropna()) if s and s.lower() not in ('nan', 'none')][:20]
print(json.dumps(vals))
PYEOF
```

From the sample values, derive a grep regex: escape the literal prefix and generalize the numeric part to `\d+` (e.g. samples `CWA-REQ-85, CWA-REQ-86, CWA-REQ-92` → `CWA-REQ-\d+`). If the samples contain more than one distinct prefix, produce one pattern per prefix. Present it for confirmation, defaulting to accept:

```
Detected requirement ID pattern from your sheet: CWA-REQ-\d+
  (from samples: CWA-REQ-85, CWA-REQ-86, CWA-REQ-92)
Use this, or enter your own?
  [Enter to accept · or type one or more regexes, comma-separated]: _
```

Accept → use the inferred pattern(s); otherwise use what the user types. Always also append a test-case ID pattern for code references (default `TC-\d+`; adjust if the team uses a different convention). If sampling fails (no `id` column mapped, unreadable sheet), fall back to asking with the default `CWA-REQ-\d+, TC-\d+`.

#### Ignore directories

Then offer the ignore-dirs knob (Enter to accept defaults):

```
Extra directories to ignore (comma-separated, in addition to defaults
  node_modules, dist, .next, build, vendor, __pycache__, .venv, target)
  []: _
```

Persist as `project: { enabled, ignore_dirs, id_patterns }` in `.buddy-council/sources.json` (see Step 4a).

## Step 3: Jira (Optional — for Ticket Creation)

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

## Step 4: Write Configuration

### 4a: Write source config

Config lives at `.buddy-council/sources.json` **in the user's project root** (the current working directory) — not the plugin folder, and not home. Both Claude Code and Copilot CLI find it relative to the project, with no plugin-path token. Config is therefore **per-project**: each project the user runs the plugin in gets its own.

Ensure the directory exists, then make sure `.buddy-council/` is git-excluded via the repo-local `.git/info/exclude` exactly as the onboarding log does (follow `manage-progress-log` Operation 1 step 3 — no `.gitignore` edit):

```bash
mkdir -p .buddy-council
```

**Migration:** if an older `config/sources.json` exists inside the plugin directory (from before this move), copy its contents into `.buddy-council/sources.json` and tell the user it was migrated.

Write `.buddy-council/sources.json` with the selected providers and non-secret settings. Include the new column-mapping, enrichment, and project blocks as collected in Steps 1a, 1b, and 2.5:

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
      "status": "Status",
      "item_type": "Item Type",
      "github_url": "Linked to Github"
    },
    "feature_inference": {
      "strategy": "hierarchical_folder",
      "folder_item_type": "Folder"
    },
    "item_type_filter": ["Functional Requirement", "Requirement"],
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

If Jira was not configured, omit the `"jira"` section. If `github_url` column was not mapped or no GitHub strategy is available, set `requirements.enrichment.enabled: false` and omit `strategy`. If the cwd is not a code project, set `project.enabled: false`.

### 4a-bis: Record the plugin install path (`plugin_root`)

The MCP servers and the Excel parser live **inside the plugin's install directory**, which must be referenced by an absolute path that works on the current machine — `${CLAUDE_PLUGIN_ROOT}` only resolves under Claude Code, not Copilot CLI. Determine the absolute install path once and store it as a top-level `plugin_root` in `.buddy-council/sources.json`:

1. If `${CLAUDE_PLUGIN_ROOT}` resolves to an existing directory that contains `mcp-servers/` → use it (Claude Code).
2. Otherwise auto-detect: search likely install locations for a directory containing `mcp-servers/testrail-server/server.py` (e.g. `~/.copilot/plugins/*/`, `~/.config/github-copilot/**/`). If exactly one matches → use it.
3. Otherwise, ask the user for the absolute path to the installed plugin.

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

## Step 5: Validate

- Confirm `.buddy-council/sources.json` was written
- Confirm `~/.buddy-council/secrets.json` was written
- Confirm `.mcp.json` was written (base URLs + `BC_SECRETS_FILE`, no secrets)
- If Excel was configured, confirm the file is readable
- Tell the user:
  1. Restart Claude Code or toggle the MCP servers with `/mcp` for connections to activate
  2. Then run `/bc:contradiction` to detect contradictions or `/bc:validate` to create tickets

## Step 6: Reduce permission prompts (Copilot CLI)

Claude Code auto-approves the plugin's read-only operations via the bundled hook — no action needed there. **Copilot CLI** has no shippable hook, so print a ready-to-paste `--allow-tool` launch recipe tailored to what was just configured, and tell the user to launch Copilot with it (writes like Jira creation still prompt):

- Always include the TestRail read tools and safe shell:
  `testrail(testrail_get_projects),testrail(testrail_get_suites),testrail(testrail_get_sections),testrail(testrail_get_cases),testrail(testrail_get_cases_by_refs),testrail(testrail_get_case),shell(jq:*),shell(gh api:*)`
- If Jira was configured, also add: `jira(jira_get_projects),jira(jira_get_issue_types),jira(jira_get_issue)`
- If GitHub enrichment uses the `mcp` strategy, also add: `github(get_file_contents)`

Present it as a single command, e.g.:

```bash
copilot --allow-tool='<comma-separated list from above>'
```

Also tell the user: the Excel parser and the TestRail connection test run once per analysis — when Copilot first prompts for them, choose **"always allow"** for the directory. This step is informational only — do **not** edit any Copilot config files (Copilot manages `~/.copilot/config.json` itself; there is no documented per-tool allowlist file to write).

## Important

- NEVER write credentials into `.buddy-council/sources.json` — that file is user-specific and contains no secrets
- ALWAYS write credentials ONLY to `~/.buddy-council/secrets.json` — it is the single source of truth. `.mcp.json` gets non-secret env (`*_BASE_URL`) plus `BC_SECRETS_FILE`, never tokens (the external GitHub MCP server is the sole exception)
- `.mcp.json` is gitignored; under this design it carries no secrets (except the GitHub MCP token)
- If `~/.buddy-council/secrets.json` already exists, merge new entries without overwriting existing ones
- If `.mcp.json` already exists, merge new server configs without overwriting other servers
- Jira configuration is **optional** — users can run `/bc:validate --dry-run` without configuring Jira
