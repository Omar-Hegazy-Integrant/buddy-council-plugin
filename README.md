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
| **Jira** | Requirements | Planned |
| **Jira** | Ticket creation | Supported (via MCP server) |
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

These descriptions are the `description:` frontmatter in `commands/*.md` — the same text both CLIs show in their `/` menus. Keep the table and the frontmatter in sync when either changes.

## Prerequisites

- [Claude Code](https://claude.ai/code) **or** [Copilot CLI](https://docs.github.com/en/copilot)
- [uv](https://docs.astral.sh/uv/) — runs the MCP servers **and** the Excel parser; it provisions a compatible Python and all dependencies automatically, so no host Python installation or `pip install` is needed
- A TestRail account with API access (for test cases)
- An Excel export from Jama (for requirements), or direct Jama API access (when available)
- Optional: the [GitHub CLI](https://cli.github.com/) (`gh`), authenticated — for requirement-doc enrichment when your sheet links GitHub docs

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

### Copilot CLI

**Step 1: Add the marketplace**

```
copilot plugin marketplace add https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin
```

**Step 2: Install the plugin**

```
copilot plugin install bc
```

### Copilot CLI — fewer permission prompts

The plugin's bundled hooks run on **both runtimes**: Claude Code loads `hooks/hooks.json`, Copilot CLI (1.0.7x and later) loads the plugin-root `hooks.json` — same scripts, same behavior. On either CLI they auto-approve the plugin's curated read-only operations (the Excel parser, `jq`, `gh api` reads, the TestRail connection test, read-only MCP fetches) and writes to the plugin's own generated files (`.buddy-council/` config and progress log, secrets, the plugin's `.mcp.json`). Write operations like Jira ticket creation always prompt.

If your Copilot version still prompts for MCP reads (MCP tool naming in hooks varies by version), pre-approve them at launch with `--allow-tool`:

```bash
copilot --allow-tool='testrail(testrail_get_projects),testrail(testrail_get_suites),testrail(testrail_get_sections),testrail(testrail_get_cases),testrail(testrail_get_cases_by_refs),testrail(testrail_get_case)'
```

Append the read-only tools for any other sources you configured:

- **Jira** (ticket validation): `jira(jira_get_projects),jira(jira_get_issue_types),jira(jira_get_issue)`
- **GitHub MCP** (doc enrichment): `github(get_file_contents)`

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
3. **Jira (optional)** — only needed for real ticket creation from `/bc:validate` (dry-run works without it).
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

## Usage

### Detect Contradictions

```
/bc:contradiction                        # Analyze all requirements and test cases
/bc:contradiction CWA-REQ-85             # Analyze a specific requirement
/bc:contradiction "Patient Monitoring"   # Analyze a specific feature
```

The agent fetches requirements and test cases, normalizes and cross-links them, then analyzes for 7 types of contradictions:

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

### Validate and Create Tickets

```
/bc:validate "Add login button to checkout page"                    # Validate and create Jira ticket
/bc:validate "Add real-time heart rate monitor" --dry-run          # Test without creating real ticket
```

The agent validates the ticket description against existing requirements, detects contradictions, asks questions to fill gaps, generates a draft, and creates the Jira ticket. Use `--dry-run` to test the workflow and save a draft markdown file without creating a real ticket.

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
- **MCP Servers** — wrap external APIs with structured tool interfaces. Each is a `uv` project with a committed `uv.lock`, so every machine resolves the identical dependency set; after changing a server's `pyproject.toml`, re-run `uv lock --directory mcp-servers/<name>` and commit the updated lock
- **Hooks** — dual-manifest: `hooks/hooks.json` (Claude Code) and the plugin-root `hooks.json` (Copilot CLI) register the same four runtime-agnostic scripts

Agents never call providers directly — they go through router skills, which read the config and delegate to the correct provider. This means adding a new platform (e.g., Jira, Qase) only requires adding a `providers/<name>/` folder and updating the router.

See [docs/architecture.md](docs/architecture.md) for the full architecture documentation.

## Security

- Credentials are **never** committed to git
- `.buddy-council/sources.json` contains only provider names and non-secret settings
- Secrets live in a single file, `~/.buddy-council/secrets.json` (user home, `chmod 600`) — the MCP servers read it directly
- `.mcp.json` is gitignored and holds **no secrets** — only non-secret env (base URLs) and `BC_SECRETS_FILE`, the path to the secrets file. (Exception: the external GitHub MCP server requires its token in env.)
- The bundled MCP servers are **read-only** with one exception: Jira ticket creation (`jira_create_issue`), which always prompts before running
- Bundled hooks run on **both runtimes** (Claude Code loads `hooks/hooks.json`; Copilot CLI loads the plugin-root `hooks.json` — same scripts). If a Copilot version still prompts for MCP reads, the `--allow-tool` recipe under [Installation](#copilot-cli--fewer-permission-prompts) covers the gap:
  - a PreToolUse hook hard-blocks destructive Bash commands (`rm -rf`, `kill`, `git push --force`, etc.)
  - the same hook **auto-approves** the plugin's curated read-only operations (the Excel parser, `gh api` reads, `jq`, the TestRail connection test, and read-only MCP fetches)
  - a second PreToolUse hook auto-approves writes **only** to the plugin's own generated files (`.buddy-council/` config and progress log, `~/.buddy-council/secrets.json`, the plugin's own `.mcp.json`) — all other writes still prompt
  - a PostToolUse hook writes the redacted tool log described under [Debugging a bc run](#debugging-a-bc-run) — metadata only, credentials masked, never file contents or responses

## Adding a New Provider

1. Create `providers/<name>/fetch.md` with fetch instructions
2. Update the router skill (`skills/fetch-requirements/SKILL.md` or `skills/fetch-test-cases/SKILL.md`)
3. Update `/bc:setup` to offer the new provider as an option
4. Optionally add an MCP server in `mcp-servers/<name>/`

No changes to agents or analysis skills required.

## License

MIT
