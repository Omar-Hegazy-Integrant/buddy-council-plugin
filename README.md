# Buddy-Council

A multi-agent plugin for detecting contradictions, coverage gaps, and alignment issues between **requirements** and **test cases**. Works with both **Claude Code** and **Copilot CLI**.

Data is fetched live from external systems via MCP — no RAG, no embeddings, no vector storage.

## Supported Sources

| Source | Type | Status |
|--------|------|--------|
| **TestRail** | Test cases | Supported (via MCP server) |
| **Excel** (Jama export) | Requirements | Supported (temporary Jama fallback) |
| **GitHub** | Requirement doc enrichment | Supported (`gh` CLI or external GitHub MCP server) |
| **Jama** | Requirements | Planned (auth in progress) |
| **Jira** | Dev board (in-flight work) | Supported (required — feeds contradiction and coverage analysis) |
| **Jira** | Ticket creation | Supported (via the `sooperset/mcp-atlassian` server, run with `uvx`) |
| **Jira** | Requirements | Planned |
| **Qase** | Test cases | Planned |

## Available Commands

| Command | Description |
|---------|-------------|
| `/bc:setup` | Configure Buddy-Council data sources and credentials in four steps with a single review-and-save |
| `/bc:contradiction` | Detect contradictions, inconsistencies, and alignment gaps between requirements and test cases |
| `/bc:coverage` | Find untested requirements, orphan test cases, and coverage gaps |
| `/bc:validate` | Validate a ticket description against requirements and test cases, then draft and create a Jira ticket |
| `/bc:ask` | Ask a natural-language question about requirements and test cases — routes to the right analysis or answers directly |
| `/bc:onboarding` | Walk a new team member through the product feature-by-feature with paced demos, optional code mapping, and an assessment. Resumes across sessions |
| `/bc:codemap "<feature>"` | Map a feature to where it lives in the current codebase: files, communication flow, and per-requirement locations |
| `/bc:vnv-sprint-prep` | V&V (Validation and Verification) sprint preparation — check cross-platform parity on the dev board, clone sprint stories to the V&V board, validate them against requirements and test cases, and drive them through scenario review to ready-for-test-case-creation |

These descriptions are the `description:` frontmatter in `commands/*.md` — the same text both CLIs show in their `/` menus. Keep the table and the frontmatter in sync when either changes.

## Prerequisites

- [Claude Code](https://claude.ai/code) **or** [Copilot CLI](https://docs.github.com/en/copilot)
- [uv](https://docs.astral.sh/uv/) — runs the MCP servers **and** the Excel parser; it provisions a compatible Python and all dependencies automatically, so no host Python installation or `pip install` is needed
- A TestRail account with API access (for test cases)
- An Excel export from Jama (for requirements), or direct Jama API access (when available)
- Optional: the [GitHub CLI](https://cli.github.com/) (`gh`), authenticated — for requirement-doc enrichment when your sheet links GitHub docs
- An Atlassian Cloud account with access to your team's Jira dev board, plus an [API token](https://id.atlassian.com/manage-profile/security/api-tokens). `/bc:setup` requires the board — it feeds in-flight work into contradiction and coverage analysis, and is where `/bc:validate` files tickets. No site-admin action is needed. If the token isn't ready, setup records the board as *pending* and finishes — the requirements-vs-test-cases commands work without it

## Installation

### Claude Code

**Step 1: Add the marketplace**

```
/plugin marketplace add https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin
```

**Step 2: Install the plugin**

```
/plugin install bc
```

**Step 3: Reload**

```
/reload-plugins
```

**Step 4: Run `/bc:setup`**

Jira and Confluence run through the `sooperset/mcp-atlassian` server, which `/bc:setup` configures for you —
it collects your Atlassian API token, writes it to `~/.buddy-council/atlassian.env`, and registers the
server. It launches with `uvx`, so there is nothing extra to install: `uv` is already required for the
TestRail server and the Excel parser.

There is **no authorization step**: the server uses the API token, not browser OAuth. If you used version
0.18.x or earlier, don't go looking for the `/mcp` → Authenticate prompt — it's gone, and any leftover
`atlassian` entry pointing at `mcp.atlassian.com` is removed automatically.

### Copilot CLI

**Step 1: Add the marketplace**

```
copilot plugin marketplace add https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin
```

**Step 2: Install the plugin**

```
copilot plugin install bc
```

**Step 3: Run `/bc:setup`, then fully restart Copilot** — exit the session and relaunch, so it picks up the MCP servers. Toggling is not enough on Copilot.

> **Jira setup is identical on both runtimes.** The Atlassian server is a local `uvx` process configured by `/bc:setup`, which writes it into `.mcp.json` for Claude Code and `~/.copilot/mcp-config.json` for Copilot CLI. It is not declared in the plugin manifest, because the `--env-file` path is absolute and differs per machine — which also sidesteps Copilot not merging plugin-declared MCP servers ([#2709](https://github.com/github/copilot-cli/issues/2709)).

> **Where Copilot puts the plugin.** A marketplace install lands in `~/.copilot/installed-plugins/<marketplace>/<plugin>/`. Installing straight from the repo URL instead lands in `~/.copilot/installed-plugins/_direct/<owner>--<repo>/` — e.g. `_direct/Omar-Hegazy-Integrant--buddy-council-plugin/`. **The owner segment is expected**: it is the GitHub account the plugin was published from, the same for everyone, not a leftover from another user's machine. `/bc:setup` handles both layouts.

### Copilot CLI — fewer permission prompts

The plugin's bundled hooks run on **both runtimes**: Claude Code loads `hooks/hooks.json`, Copilot CLI (1.0.7x and later) loads the plugin-root `hooks.json` — same scripts, same behavior. On either CLI they auto-approve the plugin's curated read-only operations (the Excel parser, `jq`, `gh api` reads, the TestRail connection test, read-only MCP fetches) and writes to the plugin's own generated files (`.buddy-council/` config and progress log, secrets, the plugin's `.mcp.json`). Write operations like Jira ticket creation always prompt.

If your Copilot version still prompts for MCP reads (MCP tool naming in hooks varies by version), pre-approve them at launch with `--allow-tool`:

```bash
copilot --allow-tool='testrail(testrail_get_projects),testrail(testrail_get_suites),testrail(testrail_get_sections),testrail(testrail_get_cases),testrail(testrail_get_cases_by_refs),testrail(testrail_get_case)'
```

Append the read-only tools for any other sources you configured:

- **Jira** (board reads, ticket validation, V&V sprint prep): `atlassian(jira_get_issue),atlassian(jira_search),atlassian(jira_get_all_projects),atlassian(jira_get_project_issue_types),atlassian(jira_search_fields),atlassian(jira_get_agile_boards),atlassian(jira_get_board_issues),atlassian(jira_get_sprints_from_board),atlassian(jira_get_sprint_issues),atlassian(jira_get_user_profile)`
- **GitHub MCP** (doc enrichment): `github(get_file_contents)`

**Never add the Jira write tools** — `jira_create_issue`, `jira_update_issue`, `jira_add_comment`, `jira_create_issue_link`, `jira_add_issues_to_sprint`, `jira_transition_issue`. They are left out on purpose so ticket creation, label changes, links, and comments on the dev team's stories always prompt.

On Copilot versions that predate plugin hooks, also append the shell and file entries the hooks would otherwise cover: `shell(jq:*),shell(gh api:*),write(.buddy-council/sources.json),write(.buddy-council/secrets.json),write(.buddy-council/onboarding-progress.json)` — and choose **"always allow"** when the Excel parser or the TestRail connection test first prompts. `/bc:setup` prints this recipe tailored to your configuration.

### Development & release flow (maintainers)

There are always **two copies** of the plugin on a machine:

- **Installed copy** (production) — what `/bc:` commands normally run. Claude Code keeps it under `~/.claude/plugins/…`, Copilot CLI under `~/.copilot/installed-plugins/…`. Both are snapshots of GitHub `main`, refreshed only when someone explicitly updates.
- **Working clone** (development) — this repository. Edits here are invisible to installed copies until released.

**Branches:** day-to-day work happens on `dev` (or feature branches). `main` is release-only — whatever lands on `main` is what the team installs.

**Run the development version.** Both CLIs can load the working clone for a single session with the same flag:

```bash
git clone https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin.git

# Claude Code
claude --plugin-dir /path/to/buddy-council-plugin

# Copilot CLI
copilot --plugin-dir /path/to/buddy-council-plugin
```

Edit → start a new session with the flag → test `/bc:…` commands. No commit, push, or version bump is needed while iterating; the MCP servers and the Excel parser resolve their own dependencies via `uv` on first launch. If the plugin is *also* installed on your machine, disable or uninstall the installed `bc` first (`/plugin` in Claude Code; `copilot plugin uninstall bc`) so the dev and installed copies don't both answer `/bc:` commands. A normal session without the flag runs the installed copy — that's your production reference.

### Debugging a bc run

Three layers of visibility, in order of reach:

1. **In-transcript trace (both platforms).** Every analysis command follows a mandatory Data Contract: it prints `Fetch: requirements → N` / `Fetch: test cases → M` (plus `Enrichment: fetched K of N` when GitHub enrichment is configured) before analyzing, and stops to ask before continuing if any configured source failed. If you don't see these lines in a run, the run violated the contract — that itself is the bug to report. The Excel parser additionally prints a one-line `Summary:` to stderr on every invocation.
2. **Tool log (both platforms).** A bundled PostToolUse hook appends every tool call — timestamp, tool name, redacted target — to `.buddy-council/logs/tool-log-<date>.jsonl` in the project. It is active only in projects containing `.buddy-council/`, and never logs file contents, tool responses, or credentials. Read it to see exactly which tools ran, in what order — and which never ran. Under Copilot each line additionally carries a `result` status (`success` or a failure kind), and failed calls are logged too.
3. **Native session logs (Copilot CLI).** For deeper Copilot internals beyond the tool log, launch with `copilot --log-level debug` (logs land in `~/.copilot/logs/`, or set `--log-dir`).

#### TestRail (or Jira) tools missing under Copilot CLI

The two CLIs read **different MCP config files**, and Copilot ignores the project `.mcp.json` completely:

| Runtime | Config file | Notes |
|---|---|---|
| Claude Code | `.mcp.json` in the project/plugin root | `mcpServers.<name>.{command,args,env}` |
| Copilot CLI | `~/.copilot/mcp-config.json` | also needs `type: "local"` and `tools: ["*"]` per server |

The `atlassian` server follows the same table: `/bc:setup` writes it into both files. If Jira tools are missing, check that `command` is an **absolute** `uvx` path — a bare `"uvx"` fails because the spawned server does not inherit your shell's `PATH`.

`/bc:setup` writes both. If you set up with an older version and Copilot reports the MCP tools as unavailable, re-run `/bc:setup` — its path health check creates the missing Copilot config and repairs stale paths, then fully restart Copilot. Two details that cause silent failures if hand-editing: `command` must be the **absolute** path to `uv` (`command -v uv`) because the spawned server doesn't inherit your shell `PATH`, and `BC_SECRETS_FILE` must be a fully expanded path (`/Users/you/...`, not `~/...`).

**Release to the team:**

1. Bump `version` in all four manifests, keeping them identical: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.plugin/plugin.json`, `.plugin/marketplace.json`.
2. Merge `dev` → `main` (bumping first keeps the two branches identical afterwards).
3. Push `main`.
4. Everyone refreshes their installed copy — no other action needed:
   - Claude Code: `/plugin marketplace update buddy-council`
   - Copilot CLI: `copilot plugin update bc`

Claude Code's install path contains the version (`~/.claude/plugins/cache/<marketplace>/bc/<version>/`), so every update moves the plugin. Nobody needs to re-run `/bc:setup` for that: commands resolve the install path from the runtime first, and `/bc:setup` repairs a drifted `plugin_root` and `.mcp.json` automatically if it is ever run again. Restarting the session (or toggling `/mcp`) is still needed for the MCP servers to reconnect.

## Setup

After installation, run the setup command to configure your data sources:

```
/bc:setup
```

The wizard runs in four steps with a single review-and-save confirmation at the end:

1. **Requirements (Excel)** — point it at your Jama export. The column mapping is auto-guessed and confirmed in one question; item types are sampled automatically (narrative `Text` rows are excluded even though they carry IDs); GitHub doc enrichment is auto-configured when the sheet has a GitHub URL column (`gh` CLI preferred, MCP fallback).
2. **Test cases (TestRail)** — base URL, credentials, project; the connection is verified before moving on.
3. **Jira & Confluence (required)** — collects your Atlassian site, email and API token, then lists your boards so you can pick the dev board instead of hunting for a URL. Confluence settings are derived from the same account. Credentials go to `~/.buddy-council/atlassian.env`. If the token isn't ready, the answers are saved as *pending*, setup finishes, and it re-verifies on the next run — `/bc:contradiction` and `/bc:coverage` keep working meanwhile, only `/bc:validate` waits.
   The same step then offers the **V&V board** plus its platform field and scenario reviewer, for `/bc:vnv-sprint-prep`. That part *is* skippable — the command can collect it itself on first run. A V&V board in the same project as the dev board is refused, because clones would land back on the dev board.
4. **Review & save** — one recap of everything collected (including the auto-detected code-mapping settings and requirement-ID patterns), one confirmation, then all files are written: `.buddy-council/sources.json` (per-project config), `~/.buddy-council/secrets.json` (credentials, `chmod 600`, never committed), and `.mcp.json` (no secrets).

Re-running `/bc:setup` shows the current configuration and changes only what you ask. After setup, restart your CLI tool or toggle the MCP server for it to take effect.

### Manual Configuration

If you prefer to configure manually instead of using `/bc:setup`:

**1. Create `.buddy-council/sources.json`** (no secrets in this file):

```json
{
  "requirements": {
    "provider": "excel",
    "excel_path": "/absolute/path/to/requirements.xls",
    "column_mapping": {
      "id": "ID",
      "title": "Name",
      "description": "Description",
      "rationale": "Rationale",
      "item_type": "Item Type",
      "github_url": "Linked to Github"
    },
    "item_type_exclude": ["Text"]
  },
  "test_cases": {
    "provider": "testrail",
    "base_url": "https://your-instance.testrail.io",
    "project_id": 1,
    "suite_id": null
  },
  "plugin_root": "/absolute/path/to/installed/plugin"
}
```

This is the minimal shape — the full schema (feature inference, enrichment, code-mapping settings) is documented in [CLAUDE.md](CLAUDE.md) under "Configuration Schema Additions". Without `column_mapping` the parser falls back to legacy positional mode.

**2. Create `~/.buddy-council/secrets.json`**:

```json
{
  "testrail": {
    "username": "user@company.com",
    "api_key": "your-testrail-api-key"
  }
}
```

```bash
chmod 600 ~/.buddy-council/secrets.json
```

**3. Create `.mcp.json`** in the plugin root (copy from `.mcp.example.json`). It holds **no secrets** — only the non-secret base URL and `BC_SECRETS_FILE`, a pointer to the secrets file; the server reads credentials from `~/.buddy-council/secrets.json`:

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uv",
      "args": ["run", "--directory", "mcp-servers/testrail-server", "mcp", "run", "server.py"],
      "env": {
        "TESTRAIL_BASE_URL": "https://your-instance.testrail.io",
        "BC_SECRETS_FILE": "~/.buddy-council/secrets.json"
      }
    }
  }
}
```

Add an `atlassian` entry here too — it is an ordinary stdio server, and both runtimes need it:

```json
{
  "mcpServers": {
    "atlassian": {
      "command": "uvx",
      "args": ["mcp-atlassian@0.23.1", "--env-file", "/Users/you/.buddy-council/atlassian.env", "--transport", "stdio"]
    }
  }
}
```

The `--env-file` path must be **absolute** — a leading `~` is not expanded. The version is pinned on purpose: the server changed its default `TOOLSETS` in 0.22.0, so floating to latest could silently change which tools exist. Create the env file with your Atlassian credentials (`chmod 600`):

```bash
JIRA_URL=https://yourorg.atlassian.net
JIRA_USERNAME=you@company.com
JIRA_API_TOKEN=your-api-token
CONFLUENCE_URL=https://yourorg.atlassian.net/wiki
CONFLUENCE_USERNAME=you@company.com
CONFLUENCE_API_TOKEN=your-api-token
TOOLSETS=default,jira_agile,jira_links,jira_projects,jira_users
ENABLED_TOOLS=jira_get_issue,jira_search,jira_create_issue,jira_update_issue,jira_add_comment,jira_get_transitions,jira_transition_issue,jira_search_fields,jira_get_agile_boards,jira_get_board_issues,jira_get_sprints_from_board,jira_get_sprint_issues,jira_add_issues_to_sprint,jira_get_link_types,jira_create_issue_link,jira_get_all_projects,jira_get_project_issue_types,jira_get_create_fields,jira_get_project_fields,jira_get_user_profile,jira_search_assignable_users,confluence_search,confluence_get_page,confluence_get_page_children,confluence_get_comments
```

**`TOOLSETS` is not optional.** `jira_agile`, `jira_links`, `jira_projects` and `jira_users` are not in the server's default set, and an omitted toolset fails *silently* — the tools just don't appear. Values must be unquoted; the env-file parser is literal, so quotes become part of the token.

**`ENABLED_TOOLS` is a safety boundary, not a convenience.** It intersects with `TOOLSETS`, and the list above is exactly what the plugin calls. Omitting it hands the agent every tool in those toolsets — including `jira_delete_issue` and `confluence_delete_page`, both of which live in *core* toolsets. They would still prompt before running, but there is no reason to expose them.

**4. Jira dev board (required).** Add a `jira` block to `.buddy-council/sources.json` — no credentials, just which board to read and file into:

```json
{
  "jira": {
    "base_url": "https://yourorg.atlassian.net",
    "project_key": "PROJ",
    "default_issue_type": "Story",
    "board": {
      "url": "https://yourorg.atlassian.net/jira/software/projects/PROJ/boards/42",
      "id": 42
    },
    "pending": false
  }
}
```

`board.id` is the integer from the board URL and is passed straight to the Agile API. There is no `cloud_id` — the server is bound to one site by `JIRA_URL`; drop it if an older config still has one. Set `pending: true` if your token isn't ready yet; analysis commands will skip the board with a visible line and `/bc:validate` will refuse real ticket creation until `/bc:setup` clears it.

**5. V&V workflow (only for `/bc:vnv-sprint-prep`).** Three more keys inside the same `jira` block:

```json
{
  "jira": {
    "vnv_board": {
      "url": "https://yourorg.atlassian.net/jira/software/projects/VV/boards/77",
      "id": 77,
      "project_key": "VV"
    },
    "platform": {
      "field_name": "OS",
      "field_id": "customfield_10050",
      "values": { "ios": ["iOS"], "android": ["Android"] },
      "require_parity": ["ios", "android"],
      "title_prefix_fallback": true
    },
    "vnv_workflow": {
      "reviewer": { "account_id": "5b10a2844c20165700ede21g", "display_name": "Jane Doe" },
      "approval_phrases": ["approved", "lgtm", "looks good"],
      "labels": {
        "pending_validation": "pending-validation",
        "pending_questions": "pending-questions",
        "pending_scenario_validation": "pending-scenario-validation",
        "ready_for_test_cases": "ready-for-test-case-creation"
      }
    }
  }
}
```

- **`vnv_board.project_key` must differ from `jira.project_key`** — clones go into this project, so pointing it at the dev project would put them on the dev board.
- **`platform`** drives the cross-platform parity check. `field_id` is a cache: leave it out and it's discovered from `field_name` on first run. `title_prefix_fallback` is used only when a story's `OS` field is empty, and any result derived from it is reported as lower-confidence.
- **`vnv_workflow.labels`** are the four pipeline states. Rename them freely — the workflow reads this config, never hardcoded strings. `reviewer.account_id` can be omitted and resolved from `display_name` via `jira_get_user_profile` (which needs the `jira_users` toolset).

On Copilot CLI, the same servers go in `~/.copilot/mcp-config.json`, with `type: "local"` and a `tools` allowlist added:

```json
{
  "mcpServers": {
    "atlassian": {
      "type": "local",
      "command": "/opt/homebrew/bin/uvx",
      "args": ["mcp-atlassian@0.23.1", "--env-file", "/Users/you/.buddy-council/atlassian.env", "--transport", "stdio"],
      "tools": ["*"]
    }
  }
}
```

Use the absolute path from `command -v uvx` — a bare `"uvx"` fails because the spawned server doesn't inherit your shell's `PATH`.

## Usage

### Detect Contradictions

```
/bc:contradiction                        # Analyze all requirements and test cases
/bc:contradiction CWA-REQ-85             # Analyze a specific requirement
/bc:contradiction "Patient Monitoring"   # Analyze a specific feature
```

The agent fetches requirements, test cases, and the in-flight issues on your Jira dev board, normalizes and cross-links them, then analyzes for 7 types of contradictions:

| Type | Severity |
|------|----------|
| Direct conflicts | CRITICAL |
| Behavioral conflicts | HIGH |
| Test vs requirement conflicts | HIGH |
| Scope overlaps | MEDIUM |
| Cross-feature tensions | MEDIUM |
| Temporal/state conflicts | MEDIUM |
| Missing alignment | LOW |

### Check Coverage

```
/bc:coverage                             # Full coverage analysis
/bc:coverage "Login"                     # Coverage for a specific feature
```

Alongside the usual untested-requirements and orphan-test-case findings, the report carries a **Delivery risk** section built from your dev board: work in flight whose requirement has no test case, and board issues that match no requirement at all. These are listed separately and deliberately kept out of the headline coverage percentage, which stays requirements-vs-tests so it remains comparable across runs.

### Validate and Create Tickets

```
/bc:validate "Add login button to checkout page"                    # Validate and create Jira ticket
/bc:validate "Add real-time heart rate monitor" --dry-run          # Test without creating real ticket
```

The agent validates the ticket description against existing requirements, detects contradictions, asks questions to fill gaps, generates a draft, and creates the ticket on your dev board — into the active sprint when there is one, otherwise the backlog (it says which). Use `--dry-run` to test the workflow and save a draft markdown file without creating a real ticket.

### V&V Sprint Preparation

For the Validation & Verification team. Takes a sprint from the dev board through to scenarios ready for
test-case writing.

```
/bc:vnv-sprint-prep                              # Dry run — show the full plan, write nothing
/bc:vnv-sprint-prep --apply                      # Execute, confirming once per phase
/bc:vnv-sprint-prep --board <vnv-board-url>      # Record/override the V&V board
/bc:vnv-sprint-prep PROJ-123                     # One story and its platform counterpart
```

Six phases: confirm the V&V board → fetch the current sprint and check that every iOS story has a linked
Android counterpart (via the `OS` field, falling back to title prefixes) → clone the stories onto the V&V
board → validate each clone against requirements and test cases → write high-level scenarios into the ticket
→ mark ready for test-case creation once the reviewer approves.

It is **resumable and that's the normal way to use it**. Re-run it and it re-reads the dev team's replies to
questions it raised, folds in the answers, re-validates, and advances any ticket whose blocker has cleared —
plus checks the V&V tickets for reviewer approval. State is tracked by Jira labels
(`pending-validation` → `pending-questions` → `pending-scenario-validation` → `ready-for-test-case-creation`,
exactly one at a time — each transition removes the previous), with
`.buddy-council/vnv-progress.json` remembering the history behind each one. If someone changes a label in
Jira, Jira wins.

**Writes are opt-in.** A plain run creates nothing, comments nowhere, and relabels nothing — it prints the
plan. `--apply` executes it, confirming once per phase. This is deliberate: the workflow comments on stories
the dev team owns.

One limit inherited from the Atlassian MCP server, which the workflow states rather than works around: it
cannot **upload attachments** — the download tools are read-only, and `jira_update_issue`'s `attachments`
parameter takes paths on the server's own filesystem — so scenarios are written into the V&V ticket's
description. Issue links *are* supported, so clones get a real Jira link back to their dev story, plus a
`src-<DEV-KEY>` label that makes re-runs idempotent.

### Ask Questions

```
/bc:ask "Why does TC-1234 contradict REQ-85?"
/bc:ask "What requirements have no tests?"
/bc:ask "What does CWA-REQ-85 do?"
```

Routes automatically to the right agent based on intent.

### Onboard a New Team Member

```
/bc:onboarding                # start or resume the walkthrough
/bc:onboarding status         # where am I?
/bc:onboarding feature "BGM"  # jump to one feature
```

Walks through the product feature by feature — paced demos with do/don't pairs from real test cases, an assessment per feature, and (when run inside a codebase) a code-mapping phase showing where each feature lives. Progress persists in `.buddy-council/onboarding-progress.json` and resumes across sessions.

### Map a Feature to Code

```
/bc:codemap "Patient Monitoring"
```

Same code mapping as the onboarding phase, standalone: the feature's files, communication flow, and per-requirement locations in the current repo.

> **Data Contract:** every analysis command fetches every configured source — requirements and test cases always, plus GitHub doc enrichment when configured — and prints visible `Fetch:`/`Readiness:`/`Enrichment:` lines before analyzing. If any source fails, it stops and asks whether to continue with partial data. See [Debugging a bc run](#debugging-a-bc-run).

## Architecture

```
Command → Agent → Skills (fetch → normalize → analyze) → Report
```

- **Commands** — user-facing entry points
- **Agents** — orchestrate the analysis workflow end-to-end
- **Skills** — reusable capabilities (fetching, normalization, analysis)
- **Providers** — platform-specific data fetching (TestRail, Excel, Jama, GitHub)
- **MCP Servers** — wrap external APIs with structured tool interfaces. The **vendored** ones under `mcp-servers/` (TestRail, Jama) are `uv` projects with a committed `uv.lock`, so every machine resolves the identical dependency set; after changing a server's `pyproject.toml`, re-run `uv lock --directory mcp-servers/<name>` and commit the updated lock. Jira/Confluence and GitHub are **not** vendored — they use third-party servers (`sooperset/mcp-atlassian`, run with `uvx` at a pinned version, and `github-mcp-server`)
- **Hooks** — dual-manifest: `hooks/hooks.json` (Claude Code) and the plugin-root `hooks.json` (Copilot CLI) register the same four runtime-agnostic scripts

Agents never call providers directly — they go through router skills, which read the config and delegate to the correct provider. This means adding a new platform (e.g., Jira, Qase) only requires adding a `providers/<name>/` folder and updating the router.

See [docs/architecture.md](docs/architecture.md) for the full architecture documentation.

## Security

- Credentials are **never** committed to git
- `.buddy-council/sources.json` contains only provider names and non-secret settings
- Secrets live under `~/.buddy-council/` (user home, `chmod 600`), in two files because the servers read two formats: `secrets.json` for TestRail and GitHub, and `atlassian.env` for the Atlassian server, which takes environment variables and cannot read JSON
- `.mcp.json` is gitignored and holds **no secrets** — only non-secret env (base URLs) and `BC_SECRETS_FILE`, the path to the secrets file. (Exception: the external GitHub MCP server requires its token in env.)
- The vendored MCP servers (TestRail, Jama) are **read-only**. Every write path in the plugin goes to Jira via the Atlassian server — `jira_create_issue` (from `/bc:validate` and `/bc:vnv-sprint-prep`), plus `jira_add_comment`, `jira_update_issue`, `jira_create_issue_link` and `jira_add_issues_to_sprint` (from `/bc:vnv-sprint-prep`). All of them **always prompt**: they are deliberately excluded from the auto-approve hook and from every `--allow-tool` recipe. Do not add them
- **`/bc:vnv-sprint-prep` is dry-run by default** because it writes to tickets other teams own. A plain run creates nothing, comments nowhere, and relabels nothing; `--apply` executes after one batch confirmation per phase
- Jira access **does** hold a stored credential as of 0.19.0: an Atlassian API token in `~/.buddy-council/atlassian.env` (`chmod 600`), read by the Atlassian server via `--env-file`. It never appears in `sources.json` or in either MCP config — those reference the file by path
- Bundled hooks run on **both runtimes** (Claude Code loads `hooks/hooks.json`; Copilot CLI loads the plugin-root `hooks.json` — same scripts). If a Copilot version still prompts for MCP reads, the `--allow-tool` recipe under [Installation](#copilot-cli--fewer-permission-prompts) covers the gap:
  - a PreToolUse hook hard-blocks destructive Bash commands (`rm -rf`, `kill`, `git push --force`, etc.)
  - the same hook **auto-approves** the plugin's curated read-only operations (the Excel parser, `gh api` reads, `jq`, the TestRail connection test, and read-only MCP fetches)
  - a second PreToolUse hook auto-approves writes **only** to the plugin's own generated files (`.buddy-council/` config and progress log, `~/.buddy-council/secrets.json`, the plugin's own `.mcp.json`) — all other writes still prompt
  - a PostToolUse hook writes the redacted tool log described under [Debugging a bc run](#debugging-a-bc-run) — metadata only, credentials masked, never file contents or responses

## Adding a New Provider

1. Create `providers/<name>/fetch.md` with fetch instructions
2. Update the router skill (`skills/fetch-requirements/SKILL.md`, `skills/fetch-test-cases/SKILL.md`, or `skills/fetch-board-issues/SKILL.md`)
3. Update `/bc:setup` to offer the new provider as an option
4. Optionally add an MCP server in `mcp-servers/<name>/`

No changes to agents or analysis skills required.

## License

MIT
