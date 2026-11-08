# Buddy-Council

A multi-agent plugin for detecting contradictions, coverage gaps, and alignment issues between **requirements** and **test cases**. Installs in **Copilot CLI**, **Claude Code**, **VS Code**, **Codex**, and **Cursor** — see [Installation](#installation).

Data is fetched live from external systems via MCP — no RAG, no embeddings, no vector storage.

## Supported Sources

| Source | Type | Status |
|--------|------|--------|
| **TestRail** | Test cases | Supported (via MCP server) |
| **Jama** | Requirements | Supported (live, via the plugin's read-only Jama MCP server — features are Jama folders) |
| **GitHub** | Requirement doc enrichment | Supported (`gh` CLI or external GitHub MCP server) |
| **Jira** | Dev board (in-flight work) | Supported (required — feeds contradiction and coverage analysis) |
| **Jira** | Ticket creation | Supported (via `mcp-atlassian` — Cloud **and** Server/Data Center) |
| **Jira** | Requirements | Planned |
| **Qase** | Test cases | Planned |

## Available Commands

| Command | Description |
|---------|-------------|
| `/bc:setup` | Configure Buddy-Council data sources and credentials in four steps with a single review-and-save, and install the automation tools when it finds a test project |
| `/bc:contradiction` | Detect contradictions, inconsistencies, and alignment gaps between requirements and test cases |
| `/bc:coverage` | Find untested requirements, orphan test cases, and coverage gaps |
| `/bc:validate` | Validate a ticket description against requirements and test cases, then draft and create a Jira ticket |
| `/bc:ask` | Ask a natural-language question about requirements and test cases — routes to the right analysis, answers directly, finds where something you describe is already specified, maps scenarios you already have into TestRail test cases, or edits the test cases you name |
| `/bc:onboarding` | Walk a new team member through the product feature-by-feature with paced demos, optional code mapping, and an assessment. Resumes across sessions |
| `/bc:codemap "<feature>"` | Map a feature to where it lives in the current codebase: files, communication flow, and per-requirement locations |
| `/bc:vnv-sprint-prep` | V&V (Validation and Verification) sprint preparation — check cross-platform parity on the dev board, clone sprint stories to the V&V board, validate them against requirements and test cases, drive them through scenario review, and write the approved scenarios into TestRail as test cases |

These descriptions are the `description:` frontmatter in `skills/<command>/SKILL.md` — the same text the assistants show in their `/` menus. Keep the table and the frontmatter in sync when either changes.

## Prerequisites

- One of the AI coding assistants in the [install table](#installation)
- [uv](https://docs.astral.sh/uv/) — runs the plugin's connectors (TestRail, Jama, Jira, appium); it provisions a compatible Python and all dependencies automatically, so no host Python installation or `pip install` is needed. The plugin finds it on your `PATH` or where its installers put it (`~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.cargo/bin`), even when your assistant was opened from the Dock
- A TestRail account with API access (for test cases)
- Requirements from Jama Connect — a Jama API client ID and secret (Jama → profile menu → **Set API Credentials**), on an account allowed to use the REST API (Admin → REST API → Managed Access Control, when your site restricts it). A read-only Jama account is recommended; the plugin never writes to Jama
- Optional: the [GitHub CLI](https://cli.github.com/) (`gh`), authenticated — for requirement-doc enrichment when your requirements link GitHub docs
- A Jira account with access to your team's dev board, on **either Atlassian Cloud or Jira Server/Data Center** — plus a token: an [API token](https://id.atlassian.com/manage-profile/security/api-tokens) on Cloud, or a Personal Access Token (profile menu → **Personal Access Tokens**) on Server/DC. `/bc:setup` requires the board — it feeds in-flight work into contradiction and coverage analysis, and is where `/bc:validate` files tickets. Nothing needs enabling by a site admin. If the connection test fails (a self-hosted Jira usually needs your corporate VPN), setup records the board as *pending* and finishes — the requirements-vs-test-cases commands work without it
- For the automation tools (`/bc:automate`): **nothing to install by hand**. When `/bc:setup` runs inside an Appium or WebdriverIO test project, it installs its own Node and appium-mcp — see [Automation tools](#automation-tools-only-inside-a-test-project). iPhone tests need Xcode from the App Store, and setup's final check needs a phone connected over USB

## Installation

Install the plugin with your assistant's own plugin command:

| Assistant | Install | Tested |
|---|---|---|
| Copilot CLI | `copilot plugin marketplace add Omar-Hegazy-Integrant/buddy-council-plugin`, then `copilot plugin install bc@buddy-council` | Yes |
| Claude Code | `/plugin marketplace add Omar-Hegazy-Integrant/buddy-council-plugin`, then `/plugin install bc@buddy-council` and `/reload-plugins` | Not tested yet |
| VS Code | Command Palette → **Chat: Install Plugin From Source**, then `https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin` | Not tested yet |
| Codex | `codex plugin marketplace add Omar-Hegazy-Integrant/buddy-council-plugin`, then `codex plugin add bc@buddy-council` | Not tested yet |
| Cursor | Add this repository to your team marketplace (Teams and Enterprise plans), or clone it into a folder under `~/.cursor/plugins/local/` | Not tested yet |

Every assistant gets the eight commands. Copilot CLI, Claude Code, and VS Code also load the plugin's agents and
hooks. Codex and Cursor load neither yet: there, each command runs its agents' steps itself, and nothing is
auto-approved, so expect more permission prompts.

**Keep Copilot CLI current.** Copilot updates a marketplace plugin at the start of each session only when its
marketplace opts in. Add this to `~/.copilot/settings.json` once — or run `copilot plugin update bc` after each
release:

```json
{
  "extraKnownMarketplaces": {
    "buddy-council": {
      "source": { "source": "github", "repo": "Omar-Hegazy-Integrant/buddy-council-plugin" },
      "autoUpdate": true
    }
  }
}
```

**Then run `/bc:setup`** (where your assistant lists skills by name, pick **setup**). No restart is needed:
the connectors are already running, and each uses a key the moment setup saves it.

> **The connectors come with the plugin.** TestRail, Jama, Jira, and appium are MCP servers the plugin declares
> itself (`mcp.json`, and `.claude-plugin/plugin.json` for Claude Code), so your assistant starts them and setup
> registers nothing — run `/mcp` in either assistant to see them. **Keys belong to sites:**
> one file per site in `~/.buddy-council/keys/`, and every call a command makes names its project's site, so two
> projects on two TestRail, Jama, or Jira sites both work, side by side — and a key only ever reaches the site it
> was saved for. A call that names no site uses your only key for that tool, so everyday Jira use is unchanged.

> **Jira — nothing to authorize.** The Jira connector runs **[`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian)** for each Jira site, with the token setup saved for that site. There is no browser consent step and no site-admin switch to flip; a site's first call downloads mcp-atlassian, which can take half a minute. Re-run `/bc:setup` to change a token.

> **Updating from an older version.** Older setups kept your keys in `~/.buddy-council/secrets.json` and `~/.buddy-council/atlassian.env`, and registered `testrail`, `jama`, and `atlassian` in your assistant's own settings. The plugin's connectors carry the same names and take their place, and on their first call they copy each key to the site it was set up for — so everything keeps working before you run `/bc:setup` again. A key whose site can't be told for sure (say, two old settings files name different sites) stays where it is, and `/bc:setup` asks for it.

> **Where Copilot puts the plugin.** A marketplace install lands in `~/.copilot/installed-plugins/<marketplace>/<plugin>/`. Installing straight from the repo URL instead lands in `~/.copilot/installed-plugins/_direct/<owner>--<repo>/` — e.g. `_direct/Omar-Hegazy-Integrant--buddy-council-plugin/`. **The owner segment is expected**: it is the GitHub account the plugin was published from, the same for everyone, not a leftover from another user's machine. `/bc:setup` handles both layouts.

### Copilot CLI — fewer permission prompts

The plugin's bundled hooks run in Claude Code, which loads `hooks/hooks.json`, and in Copilot CLI and VS Code, which load `com.github.copilot/hooks/hooks.json` — same scripts, same behavior. In each they auto-approve the plugin's curated read-only operations (`jq`, `gh api` reads, the connectors' reads under every name an assistant gives them, appium-mcp's screen reads, the key store's checks, and the automation script's install and checks) and writes to the plugin's own generated files (`.buddy-council/` config and progress log). Saving a key and write operations like Jira ticket creation always prompt.

If your Copilot version still prompts for MCP reads (one without plugin hooks), pre-approve them at launch with `--allow-tool`:

```bash
copilot --allow-tool='testrail(testrail_get_projects),testrail(testrail_get_suites),testrail(testrail_get_sections),testrail(testrail_get_cases),testrail(testrail_get_cases_by_refs),testrail(testrail_get_case),testrail(testrail_get_case_fields),testrail(testrail_get_case_types),testrail(testrail_get_priorities),testrail(testrail_get_templates),testrail(testrail_get_requirement_ids),testrail(testrail_get_linked_cases),testrail(testrail_get_canonical_cases)'
```

Append the read-only tools for any other sources you configured:

- **Jira** (board reads, ticket validation, V&V sprint prep): `atlassian(jira_get_user_profile),atlassian(jira_get_issue),atlassian(jira_search),atlassian(jira_get_agile_boards),atlassian(jira_get_board_issues),atlassian(jira_get_sprints_from_board),atlassian(jira_get_sprint_issues),atlassian(jira_get_all_projects),atlassian(jira_get_project_issue_types),atlassian(jira_get_project_fields),atlassian(jira_search_fields),atlassian(jira_get_create_fields),atlassian(jira_get_transitions),atlassian(jira_get_link_types),atlassian(jira_search_assignable_users)`
- **GitHub MCP** (doc enrichment): `github-mcp-server(get_file_contents)` — Copilot's built-in GitHub server
- **Jama** (requirements): `jama(jama_get_current_user),jama(jama_get_projects),jama(jama_get_item_types),jama(jama_get_features),jama(jama_get_requirements),jama(jama_get_item),jama(jama_get_items),jama(jama_get_scope_proposal)` — the server's whole tool list, since it has no write tools
- **appium-mcp** (automation — reading the phone's screen only): `appium(appium_mobile_device_info),appium(appium_find_element),appium(appium_find_elements),appium(appium_get_text),appium(appium_get_element_attribute),appium(appium_get_active_element),appium(appium_get_window_size),appium(generate_locators),appium(appium_get_page_source),appium(appium_screenshot)`

**Never add the Jira write tools** — `jira_create_issue`, `jira_update_issue`, `jira_add_comment`, `jira_transition_issue`, `jira_create_issue_link`, `jira_add_issues_to_sprint`. They are left out on purpose so ticket creation, label changes, links, and comments on the dev team's stories always prompt. The same goes for the TestRail write tools — `testrail_add_case`, `testrail_add_cases`, `testrail_add_section`, `testrail_update_case`, `testrail_update_cases` — which create and change cases in the team's suite, and for every other appium-mcp tool (`appium_session_management`, `appium_gesture`, `appium_set_value`, `appium_app_lifecycle`, …), which taps, types, opens sessions, or launches apps on the phone.

On Copilot versions that predate plugin hooks, also append the shell and file entries the hooks would otherwise cover: `shell(jq:*),shell(gh api:*),write(.buddy-council/sources.json),write(.buddy-council/onboarding-progress.json)` — and choose **"always allow"** when a connection test first prompts. `/bc:setup` prints this recipe tailored to your configuration.

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

To try a build in Copilot without touching your own Copilot profile — its settings, MCP servers, and installed plugins — give the session a throwaway home:

```bash
COPILOT_HOME="$(mktemp -d)" copilot --plugin-dir /path/to/buddy-council-plugin
```

A fresh home has no sign-in of its own: Copilot uses `GH_TOKEN` or `GITHUB_TOKEN` when one is set, else run `/login` in it. Start commands in an interactive session — `copilot -p "/bc:coverage"` sends the text to the model as it is, and the model can't start a command itself.

Edit → start a new session with the flag → test `/bc:…` commands. No commit, push, or version bump is needed while iterating; the MCP servers resolve their own dependencies via `uv` on first launch. If the plugin is *also* installed on your machine, disable or uninstall the installed `bc` first (`/plugin` in Claude Code; `copilot plugin uninstall bc`) so the dev and installed copies don't both answer `/bc:` commands. A normal session without the flag runs the installed copy — that's your production reference.

### Debugging a bc run

Three layers of visibility, in order of reach:

1. **In-transcript trace (every assistant).** Every analysis command follows a mandatory Data Contract: it prints `Fetch: requirements → N` / `Fetch: test cases → M` (plus `Enrichment: fetched K of N` when GitHub enrichment is configured) before analyzing, and stops to ask before continuing if any configured source failed. If you don't see these lines in a run, the run violated the contract — that itself is the bug to report.
2. **Tool log (Claude Code, Copilot CLI, VS Code).** A bundled PostToolUse hook appends every tool call — timestamp, tool name, redacted target — to `.buddy-council/logs/tool-log-<date>.jsonl` in the project. It is active only in projects containing `.buddy-council/`, and never logs file contents, tool responses, or credentials. Read it to see exactly which tools ran, in what order — and which never ran. Under Copilot each line additionally carries a `result` status (`success` or a failure kind), and failed calls are logged too.
3. **Native session logs (Copilot CLI).** For deeper Copilot internals beyond the tool log, launch with `copilot --log-level debug` (logs land in `~/.copilot/logs/`, or set `--log-dir`).

**Automation tools.** Their logs are in `~/.buddy-council/automation/logs/`: npm's install, appium-mcp's own log from each check, the project's Appium server, and the project's setup script. To see what is installed and what is pending without changing anything, run `python3 <plugin>/scripts/bc_tools.py status --project '<test project>'`.

#### Jama on a large project

Jama Cloud takes about a second per request, and a 5,000-item project tree about a minute to send. The plugin's Jama server runs up to 8 requests in parallel and caches what it reads for 10 minutes, so:

- The first whole-project or feature-scoped read in a session may show a progress line such as `Fetch: Jama project 42 — reading, 85 of 99 pages` before the requirements arrive. The server answers early with progress and the agent asks again; that is expected, not a failure.
- Questions about specific requirement IDs — a bug's or a ticket's — never read the whole project. The IDs are read by key in one batched call: about 45 s for 50 requirements with their traceability when nothing is cached, and instant when repeated within 10 minutes.

#### Troubleshooting: the connectors' tools are missing

The connectors start from the plugin's own manifests through `scripts/bc-mcp`, which needs [uv](https://docs.astral.sh/uv/getting-started/installation/). When `/mcp` shows `testrail`, `jama`, `atlassian`, or `appium` as failed, its log has one line from the launcher — almost always that uv isn't installed, or not in `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, or `~/.cargo/bin`. Install it, then restart the assistant. To try a connector by hand:

```bash
sh <plugin>/scripts/bc-mcp jama < /dev/null    # starts, then exits at end of input; anything wrong is one line on stderr
```

- **"No key is saved for <site>"** — run `/bc:setup` with a link to that site; `python3 <plugin>/scripts/bc_keys.py sites` lists the sites this computer has keys for (never the keys themselves).
- **"This computer has keys for N sites; pass `site`"** — a call outside the plugin's commands named no site while you have several for that tool. Say which site in your request; commands always pass it.
- **Jira: "mcp-atlassian is still starting"** — a site's first call downloads it; call again in a moment.
- **`appium` shows no tools** — expected until `/bc:setup` installs the automation tools in a test project; restart the assistant once after that.

**Release to the team:**

1. Bump `version` in the root `plugin.json` — the one place it is set — and run `python3 scripts/sync_version.py`, which copies it into `.claude-plugin/plugin.json`. The tests (`python3 -m unittest discover -s scripts/tests`) fail while the two differ, and `claude plugin validate . --strict` must pass.
2. Merge `dev` → `main` (bumping first keeps the two branches identical afterwards).
3. Push `main`.
4. Everyone refreshes their installed copy — no other action needed:
   - Claude Code: `/plugin marketplace update buddy-council`
   - Copilot CLI: `copilot plugin update bc`, or nothing at all with [auto-update](#installation) on
   - Codex: updates its plugins at startup

Claude Code's install path contains the version (`~/.claude/plugins/cache/<marketplace>/bc/<version>/`), so every update moves the plugin. Nobody needs to re-run `/bc:setup` for that: commands find the plugin root from their own skill folder, the connectors are started from the plugin's own manifests, and `/bc:setup` repairs a drifted `plugin_root` automatically if it is ever run again. A new session picks up the updated connectors.

## Setup

After installation, run the setup command to configure your data sources:

```
/bc:setup
```

The wizard runs in four steps with a single review-and-save confirmation at the end:

1. **Requirements (Jama)** — the base URL and an API client ID/secret, unless this computer already has a key for that site. The key is saved for that site alone, the connection is tested through the plugin's Jama connector before moving on, then you pick the project. Setup reads the project tree once to report its folders — **features are Jama folders** — excludes `Text` and test-case item types automatically, and confirms the Status / Rationale / GitHub-link field mapping in one question. GitHub doc enrichment is auto-configured when a GitHub-link field is mapped (`gh` CLI preferred, MCP fallback).
2. **Test cases (TestRail)** — base URL, credentials (unless this computer already has a key for that site), project; the connection is verified before moving on. Setup then **anchors Jama on your test suite, without asking**: it reads which requirement IDs your cases link to, finds the **document** they live in (a Jama set, such as `Mobile App SRS`), and takes that whole document as the requirements in scope — so a requirement in it with no linked case shows up as untested instead of vanishing. The features stay the folders your linked requirements sit in, however deep the document nests them, and only the item types the tests link to count. The rest of the project — system requirements, risks, labeling, standards, design, archives — is **background**: `/bc:ask` searches it to enrich answers, and no report counts it. The review in step 4 shows this in plain words; say "leave out <folder>", "only <folder>", or "the whole project" to change it, then or on any re-run. The same step picks where generated test cases go **without asking**: **beside the existing cases for the same requirement or feature** when your cases already carry requirement IDs, so no parallel folder tree appears — otherwise in a `V&V` folder, one subfolder per feature. The review in step 4 shows the choice in one line; say where you'd rather have them, then or on any re-run. Whatever the setting, every command that creates cases shows each case's folder before writing anything.
3. **Jira (required)** — the base URL, a token, and your team's board URL. Cloud vs Server/Data Center is detected from the host, so you're only asked for the credential that deployment actually uses. **The connection is tested before moving on**, exactly like TestRail, through the plugin's Jira connector. The board URL is parsed for the board id and project key — and when a classic RapidBoard URL carries no key, it's derived from the board's own issues rather than asked for. If the test fails (usually a self-hosted Jira needing the VPN), the answers are saved as *pending*, setup finishes, and it re-tests on the next run — `/bc:contradiction` and `/bc:coverage` keep working meanwhile, only `/bc:validate` waits.
   The same step then offers the **V&V board** plus its platform field and scenario reviewer, for `/bc:vnv-sprint-prep`. That part *is* skippable — the command can collect it itself on first run. **The V&V board may live in the same project as the dev board**; only a duplicate board *id* is refused. When they share a project, setup samples both boards to work out what the V&V board's filter keys off — a label, component, or issue type — confirms it in one question, and stamps it on every clone.
4. **Review & save** — one recap of everything collected (including the auto-detected code-mapping settings and requirement-ID patterns), one confirmation, then `.buddy-council/sources.json` (per-project config, no secrets) is written. Keys were saved as each step collected them, one file per site in `~/.buddy-council/keys/` (`chmod 600`, never committed) — your assistant asks before saving each one. Setup writes no connector settings for any assistant.

Re-running `/bc:setup` shows the current configuration and changes only what you ask. Setup finishes in one run: there is nothing to restart.

### Automation tools (only inside a test project)

When you run `/bc:setup` in an Appium or WebdriverIO test project — such as ApolloAutomation — it also gets the tools that `/bc:automate` drives the app with. **It adds no questions:** the step-4 recap lists what it found and what it will install (the stack, the phone, the app build, the download size), your one "yes" covers it, and "leave out automation" there skips it. Analysis-only projects download nothing.

- **Your project's own setup comes first.** Setup runs the project's check-only mode, such as `./setup --check` — the one command it asks permission for, since it runs your project's code — and shows the result. If it finds problems, the project's own fix runs after you save. What still needs you — a password, tapping Trust on the phone, choosing a signing profile — comes back as "run ./setup in a terminal".
- **bc keeps its own copies, the way `uv` provides Python.** It installs Node 26 LTS and appium-mcp into `~/.buddy-council/tools` (about 620 MB), pinned by version and checksum. They are never on your PATH and never shared, and setup never edits your shell profile, `JAVA_HOME`, or `ANDROID_HOME`. A tool your Mac already has is never replaced; for Android on appium-mcp's built-in drivers, a missing JDK or Android SDK is installed beside yours, only when the recap said so.
- **The phone is driven through your project's own Appium** when the Mac has it — so screens are read on the same Appium and WebDriverAgent your tests run on — and through appium-mcp's built-in drivers otherwise.
- **It proves it works.** Setup checks that appium-mcp answers and reads one page source from the phone, without launching any app, then records it for the plugin's `appium` connector — which serves no tools until then, so restart your assistant once after the first install. No phone connected? Automation is saved as *pending* and checked again on the next `/bc:setup`, like a pending Jira board. Once a phone passes, unplugging it later doesn't undo that. The phone is the one your project's setting names (such as `APOLLO_UDID`), else the first one connected over USB.
- **It keeps itself current.** When a plugin release changes a pinned version, the next `/bc:setup` installs it by itself, keeps the old one until the new server answers, then removes it. Downloads use your Mac's own proxy, npm registry, and certificates — plus `NODEJS_ORG_MIRROR` for Node — and the checksums keep any mirror safe. A download that still fails is retried on the next run.

### Manual Configuration

If you prefer to configure manually instead of using `/bc:setup`:

**1. Create `.buddy-council/sources.json`** (no secrets in this file):

```json
{
  "requirements": {
    "provider": "jama",
    "base_url": "https://company.jamacloud.com",
    "project_id": 42,
    "feature_inference": { "strategy": "hierarchical_folder", "folder_item_type": "Folder" },
    "item_type_exclude": ["Text", "Test Case"],
    "field_mapping": { "status": "Status", "rationale": "Rationale" }
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

This is the minimal shape — the full schema (feature inference, enrichment, code-mapping settings) is documented in [AGENTS.md](AGENTS.md) under "Configuration Schema Additions".

**2. Save a key for each site.** Keys belong to sites, not projects: one file per site in
`~/.buddy-council/keys/`, mode 600 in a 700 folder. Ask the key store where a site's file goes — it creates the
folder privately and never prints a key:

```bash
python3 <plugin>/scripts/bc_keys.py path --url https://your-instance.testrail.io --provider testrail
```

Write that file with the provider, the site's base URL, and the credential:

```json
{ "provider": "testrail", "url": "https://your-instance.testrail.io", "username": "user@company.com", "api_key": "your-testrail-api-key" }
```

Then `python3 <plugin>/scripts/bc_keys.py check --url https://your-instance.testrail.io --provider testrail` confirms
it is complete and makes it private. The connectors read it on the next call — nothing to register or restart.

**Scoping Jama.** A large project is usually narrowed to the document your test cases link to:

```json
{
  "requirements": {
    "provider": "jama",
    "base_url": "https://company.jamacloud.com",
    "project_id": 42,
    "roots": [{ "id": 1001, "path": "Product / Software Requirements" }],
    "feature_inference": { "strategy": "hierarchical_folder", "folder_item_type": "Folder", "depth": 3 },
    "item_type_filter": ["Mobile SRS"],
    "item_type_exclude": ["Text", "Test Case"],
    "field_mapping": { "status": "Status", "rationale": "Rationale", "github_url": "Linked to Github" }
  }
}
```

`roots` limits the requirements to those Jama containers — `/bc:setup` picks the whole document (set) your
test cases link to — and a feature is then a folder `depth` levels below a root: setup sets the depth so the
features are the folders your linked requirements sit in. Leave `roots` out to use the whole project, where
every folder is a feature, and add `"exclude": [{ "id": …, "path": … }]` to leave a subtree out. Everything
outside the requirements (except excluded types and subtrees) is background that `/bc:ask` lookups search,
never counted. `field_mapping` values are Jama field **labels**; `folder_item_type` is the item
type that marks a feature when there are no roots. The
credential is the Jama site's key — `client_id` + `client_secret` from Jama → profile menu →
**Set API Credentials** (`username` + `password` only on instances without API credentials):

```json
{ "provider": "jama", "url": "https://company.jamacloud.com", "client_id": "your-jama-api-client-id", "client_secret": "your-jama-api-client-secret" }
```

**3. Jira dev board (required).** Add a `jira` block to `.buddy-council/sources.json` — no credentials there either, just which board to read and file into:

```json
{
  "jira": {
    "base_url": "https://jira.company.com",
    "deployment": "server",
    "project_key": "PROJ",
    "default_issue_type": "Story",
    "board": {
      "url": "https://jira.company.com/secure/RapidBoard.jspa?rapidView=42",
      "id": 42
    },
    "pending": false
  }
}
```

`deployment` is `"cloud"` or `"server"`, detected from the host. There is no `cloud_id` — every Jira call names its site with `base_url`. `board.id` is the integer from the board URL. Set `pending: true` if the connection hasn't been verified yet; analysis commands will skip the board with a visible line and `/bc:validate` will refuse real ticket creation until `/bc:setup` clears it.

The Jira credential does **not** go here. It is the Jira site's key — a Personal Access Token on Server/Data
Center, or your email and an API token on Cloud (add `"ssl_verify": false` only for a self-signed certificate):

```json
{ "provider": "jira", "url": "https://jira.company.com", "personal_token": "<your Server/Data Center PAT>" }
```

```json
{ "provider": "jira", "url": "https://yourorg.atlassian.net", "username": "you@company.com", "api_token": "<your Cloud API token>" }
```

**4. V&V workflow (only for `/bc:vnv-sprint-prep`).** Three more keys inside the same `jira` block:

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
- **`vnv_workflow.labels`** are the four pipeline states. Rename them freely — the workflow reads this config, never hardcoded strings. `reviewer.account_id` can be omitted and resolved from `display_name` via `jira_search_assignable_users`.

**5. Automation tools — not by hand.** Run `/bc:setup` inside the test project, or the script it uses:
`python3 <plugin>/scripts/bc_tools.py ensure --project '<test project>'`. It installs the pinned Node and
appium-mcp, proves the server answers, then records how the plugin's `appium` connector starts it — absolute paths,
so it works whatever your PATH is — and records an `automation` block in that project's
`.buddy-council/sources.json`. No assistant's settings are written. The record,
`~/.buddy-council/tools/appium-mcp.json`, looks like this:

```json
{
  "command": "/Users/<you>/.buddy-council/tools/node/v26.13.0-darwin-arm64/bin/node",
  "args": ["--use-system-ca", "/Users/<you>/.buddy-council/tools/appium-mcp/1.99.2-<lock>/node_modules/appium-mcp/dist/index.js"],
  "env": {
    "NO_UI": "true",
    "ALLOW_REMOTE_APP_URLS": "false",
    "REMOTE_SERVER_URL_ALLOW_REGEX": "^https?://(127\\.0\\.0\\.1|localhost|\\[::1\\])(:[0-9]{1,5})?(/[^?#]*)?$",
    "PAGE_SOURCES_DIR": "/Users/<you>/.buddy-council/automation/page-sources",
    "SCREENSHOTS_DIR": "/Users/<you>/.buddy-council/automation/screenshots"
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

A feature is a **Jama folder** — by default one of the folders your linked requirements sit in, inside the document `/bc:setup` anchored on your test suite, with deeper folders rolled into it. The scope covers everything under it; a sub-folder name narrows to just that part, and a path such as `"Software Requirements/Patient Monitoring"` picks between folders that share a name. The same holds for `/bc:coverage`, `/bc:onboarding`, and `/bc:codemap`. Test cases are found by their requirement links, whatever their TestRail folders are called.

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

Every requirement-versus-test finding names the side to fix. When at least one says **the test case** is wrong, the report ends with a one-line offer to update those cases so they match the requirement — only the conflicting precondition, step, or expected result, never the requirement links. Answer `all`, `pick some`, or `no`; you see every before → after change before anything is written. Findings where the requirement looks wrong, or that need a decision, are never offered: requirements live in Jama, and the plugin never writes them.

### Check Coverage

```
/bc:coverage                             # Full coverage analysis
/bc:coverage "Login"                     # Coverage for a specific feature
```

Alongside the usual untested-requirements and orphan-test-case findings, the report carries a **Delivery risk** section built from your dev board: work in flight whose requirement has no test case, and board issues that match no requirement at all. These are listed separately and deliberately kept out of the headline coverage percentage, which stays requirements-vs-tests so it remains comparable across runs.

The report ends with up to two one-line offers, and nothing is written without a yes — you see the plan first:

1. **Re-link orphan cases.** A case whose requirement ID sits only in TestRail's References field (not the requirement field this plugin reads) shows up twice — as an orphan *and* as an untested requirement. The repair copies the ID across. Cases with no requirement ID at all get suggested links, each confirmed by you. Needs `test_cases.authoring.requirement_field`.
2. **Draft missing cases** — skeleton TestRail cases for requirements that are genuinely untested, linked to their requirement IDs. A requirement whose only problem is a stranded link is left out, so no duplicate case is ever drafted. Needs `test_cases.authoring`.

Answer `all`, `pick some`, or `no` to each.

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

Seven phases: confirm the V&V board → fetch the current sprint and check that every iOS story has a linked
Android counterpart (via the `OS` field, falling back to title prefixes) → clone the stories onto the V&V
board → validate each clone against requirements and test cases → write high-level scenarios into the ticket
→ mark ready for test-case creation once the reviewer approves → **create the TestRail cases**.

That last phase turns each approved ticket's scenarios into cases in the suite folder your config points at,
setting `refs` to the requirement IDs and the dev story key. Those refs are what let `/bc:coverage` see the
requirement as covered on its next run — a case created without them would leave the requirement untested
*and* add a new orphan. The cases are **skeletons** (title, preconditions, one step per Given/When/Then) for
the V&V team to expand, not finished tests. It needs `test_cases.authoring` in your config; without it the
phase skips itself with a visible line rather than guessing which TestRail template to use.

When approved scenarios are **revised** later — the ticket sent back to `pending-validation` for re-review and
re-approved, or the scenario block on the approved ticket edited by hand — the next run refreshes the cases: a
case still exactly as the plugin generated it is updated to match, a new scenario gets a new case, and a case
the V&V team has edited by hand is **never overwritten** — it is listed for manual review instead. Nothing is
deleted, and a requirement link a revised scenario dropped is flagged, not removed. A re-review needs a fresh
approval: the reviewer's comment on the earlier draft does not count.

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

Scenarios are written into the V&V ticket's description rather than an attached `.md`, so reviewers read and
edit them right in Jira. Each clone is linked to its dev story and also carries a `src-<DEV-KEY>` label — the
label is what the duplicate guard searches on when you re-run.

The V&V board **may** share a Jira project with the dev board; only a duplicate board *id* is refused. When
they share one, the run stamps each clone with whatever the V&V board's filter keys off (a label, component,
issue type, or the active sprint) and then verifies the clone actually landed on that board.

### Ask Questions

```
/bc:ask "Why does TC-1234 contradict REQ-85?"
/bc:ask "What requirements have no tests?"
/bc:ask "What does CWA-REQ-85 do?"
```

Routes automatically to the right agent based on intent.

### Find Where Something Is Already Specified

```
/bc:ask "Do we already have a requirement that silences alerts during sensor warm-up?"
/bc:ask "Where do the requirements talk about Bluetooth permissions?"
/bc:ask "In the alerts requirements, is snoozing covered?"
```

Describe a behavior or business rule in your own words, and get back whether it is **already specified**, **partly specified**, or **not specified** — with the requirements that cover it quoted verbatim, any requirement that **conflicts** with it, the related ones, where in the requirements tree it lives, which test cases verify it (or test it without a requirement behind them), and in-flight board issues that touch it.

It doesn't read every requirement to find out. Your session's model splits the ask into its concepts and gives each the other wordings the requirements might use ("alarm" → alert, notification, audible); code then ranks every requirement in scope against them (BM25 — keyword relevance, no embeddings) in about a second and writes the top 25 out in full; and the model judges only those. When the first search can't settle it — nothing covers the ask, or the top results use the project's own wording for a concept — it searches again with those words before ever answering "not specified".

With Jama, the same search runs over the **rest of the project** too — the system requirements, risks, labeling, standards, and design outside the requirements in scope — and the answer adds what they say as background: "not in the app requirements, but the system requirement H-GMS-31 says alarms must reach 50 dBA". Background enriches an answer; it never makes something "specified" and never counts in a report.

The data comes from a run folder (see [Low-cost collection](#low-cost-collection-for-bccoverage-bccontradiction-and-bcask-lookups)); a complete run of the whole scope collected in the last 30 minutes is reused, so follow-up questions answer in seconds. Say "refresh" to re-read Jama and TestRail.

### Edit Test Cases

```
/bc:ask "Change step 2 of C1234 to expect a 3-second refresh"
/bc:ask "Add CWA-REQ-90 to TC-55's requirements"
/bc:ask "Set every case in Sync/Offline handling to High priority"
```

Name the cases (`C1234`, `TC-1234`) or a folder, and say what to change. Each case is read fresh from TestRail,
and you see a before → after plan before anything is written. The update is refused for a case someone edits
between the plan and your yes, and for a change that would drop a requirement link you did not explicitly ask to
remove. Requirement IDs you add are checked against the live requirement set and written to both References
and the requirement field, so the case counts as coverage. The report lists each field's previous value; ask to
undo in the same session to put them back.

### Map Your Own Scenarios into TestRail

```
/bc:ask "Map the scenarios in docs/sync-scenarios.md into TestRail"
/bc:ask "Turn these into TestRail test cases: 1. Sync retries on network loss 2. Banner clears on reconnect"
/bc:ask "Create TestRail cases from the scenarios in PROJ-123"
/bc:ask "Preview how login.feature would map to TestRail"
```

Already have high-level scenarios? Hand them over as a file (markdown, Gherkin `.feature`, CSV, JSON), pasted
text, or a Jira key whose description holds them. The agent:

1. **Traces each scenario to requirements.** It uses the IDs you wrote (`@CWA-REQ-85`, `Traces to: …`) and
   checks them against the live requirement set, or suggests matches for scenarios that have none.
2. **Checks for existing cases.** A scenario an existing case already tests is marked `covered by TC-…` and
   skipped. Partial overlaps and scenarios that contradict their own requirement are flagged for you to
   decide.
3. **Shows one confirmation table**: trace (explicit or matched), existing coverage, and target folder for
   every scenario. You can re-trace, drop, force-create, or change folders before anything is written.
4. **Writes after a second confirmation**, through the same writer as `/bc:vnv-sprint-prep` and the coverage
   gap-fill, so every case carries its requirement IDs and counts as coverage on the next `/bc:coverage`.

A scenario that cannot be traced to any requirement is **never written**. It is reported as a possible
requirement gap, because a case with no requirement would show up as an orphan in the next coverage report.
Say "preview" to stop after the plan. Needs `test_cases.authoring` in your config (`/bc:setup`). Stories in
the V&V pipeline are redirected to `/bc:vnv-sprint-prep`, which writes their scenarios in phase 7, whether you
pass the V&V ticket or the dev story it was cloned from.

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

> **Visual reference:** the [Plugin Architecture board](https://lucid.app/lucidspark/9ed93500-df15-4b68-9fff-84a1f2e23afc/edit?page=h0VJK6ky~xWh) shows every command, how the parts talk to each other, what each one can change, and where the code could be better. Start with its "Start here" frame.

```
Command → Agent → How-tos (fetch → normalize → analyze) → Report
```

- **Manifests** — two of them (ADR 0007): the root `plugin.json` in the open Agent Plugins format, which Copilot CLI, VS Code, Codex, and Cursor read, and `.claude-plugin/plugin.json` for Claude Code. The root one holds the version
- **Commands** — user-facing entry points, in `skills/<name>/SKILL.md`: skills only you can start, so none of them loads into a session until you type it (ADR 0006)
- **Agents** — orchestrate the analysis workflow end-to-end, in `com.github.copilot/agents/`
- **How-tos** — reusable capabilities (fetching, normalization, analysis), plain files in `howtos/` that the commands and agents read by path. They cost nothing until a command needs them
- **Providers** — platform-specific data fetching (TestRail, Jama, Jira, GitHub)
- **Connectors** — the MCP servers the plugin ships, declared in both manifests (`mcp.json`, `.claude-plugin/plugin.json`) and started by `scripts/bc-mcp`, which finds `uv` and runs each one: `testrail` and `jama` (under `mcp-servers/`, `uv` projects with a committed `uv.lock`, so every machine resolves the identical dependency set — after changing a server's `pyproject.toml`, re-run `uv lock --directory mcp-servers/<name>` and commit the updated lock), `atlassian` (`mcp-servers/jira-router/`, which hands each Jira call to a copy of the community [`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian) for that call's site, serving Cloud and Server/Data Center alike — it never talks to Jira itself), and `appium`. Every tool takes `site`, and each site's key lives in its own file (`scripts/bc_keys.py`, ADR 0008). GitHub is read with the `gh` CLI or the assistant's own GitHub connection. appium-mcp, the server `/bc:automate` drives the phone with, isn't vendored either: `/bc:setup` installs it on each Mac from the lockfile in `scripts/automation/appium-mcp/`, on bc's own pinned Node, and the `appium` connector starts it — or answers with no tools until then. To move a pin, change `scripts/automation/pins.json` (Node, by version and SHA-256) or regenerate the lockfile with `npm install --package-lock-only --ignore-scripts --registry=https://registry.npmjs.org/`, and release it only after a device check passed. To move mcp-atlassian's pin, change `PACKAGE` in `mcp-servers/jira-router/server.py` and run its `--snapshot`
- **Hooks** — dual-manifest: `hooks/hooks.json` (Claude Code) and `com.github.copilot/hooks/hooks.json` (Copilot CLI, VS Code) register the same four runtime-agnostic scripts
- **Scripts** — `scripts/`: the run-folder, key-store, and automation-tools scripts, the connector launcher `bc-mcp`, `sync_version.py`, and every script, hook, and manifest test (`python3 -m unittest discover -s scripts/tests`)

Agents never call providers directly — they go through router how-tos, which read the config and delegate to the correct provider. This means adding a new platform (e.g., Jira, Qase) only requires adding a `providers/<name>/` folder and updating the router.

### Low-cost collection for `/bc:coverage`, `/bc:contradiction`, and `/bc:ask` lookups

These commands read the most data, so they split the work by what it needs:

| Work | Runs on |
|---|---|
| Fetching requirements, every test case in the suite, and the GitHub docs — for a lookup on Jama, the rest of the project as background too; waiting out a slow Jama read | the `data-collector` sub-agent on a **low-cost model** — `claude-haiku-4.5`, else `gpt-5-mini`, in Copilot CLI; Haiku in Claude Code; your session's model in assistants that don't load plugin agents |
| Cross-linking IDs and counting untested requirements, orphans, stranded links, coverage per feature; ranking requirements and background against a lookup | **code** (`scripts/bc_run.py`) — no model at all |
| Contradictions, weak coverage, severity, the report, every question to you, every write | **your session's model**, reading one feature at a time |

The data travels as files, never as a model's retelling: the servers write it straight into a **run folder** in your project, `.buddy-council/runs/<run-id>/`, and your model reads `links.json` (the counts) and then each feature's bundle — its requirements, verbatim, with their custom fields (platform, product, version), each followed by the test cases that link to it with theirs (applicable OS, product version, CtQ, priority) — plus the GitHub docs the feature links to. The low-cost model is never taken at its word: the link step checks the run against your `sources.json` and writes the `Fetch:`, `Enrichment:` and `Readiness:` lines itself, and a run that doesn't match the config is collected again by your session's model. If the sub-agent can't run, you'll see `Collect: ran inline — …` and your session's model does the same steps itself.

To use a different model for the collector in Copilot CLI, pick one for `bc:data-collector` in `/subagents`. Board issues, and GitHub docs under the `mcp` enrichment strategy, are still fetched by your session's model.

See [docs/architecture.md](docs/architecture.md) for the full architecture documentation.

## Security

- Credentials are **never** committed to git
- `.buddy-council/sources.json` contains only provider names and non-secret settings
- **Keys belong to sites.** Each lives in its own file, `~/.buddy-council/keys/<site>.json` (user home, `chmod 600` in a `700` folder), and names the address it was saved for. A connector talks only to that address with that key and follows no redirect, so a key never reaches another site — the connectors' tests prove it for two TestRail, two Jama, and two Jira sites side by side. One key per site
- No assistant's settings hold a key or a connector: the connectors ship in the plugin, and setup writes no `.mcp.json` or `~/.copilot/mcp-config.json`. Saving a key always asks first, and the key store's commands never print one
- Every write path in the plugin **always prompts** — each is deliberately excluded from the auto-approve hook, from `settings.json`, and from every `--allow-tool` recipe. Do not add them. Jira writes go through `mcp-atlassian`: `jira_create_issue` (from `/bc:validate` and `/bc:vnv-sprint-prep`), plus `jira_add_comment`, `jira_update_issue`, `jira_create_issue_link`, and `jira_add_issues_to_sprint` (from `/bc:vnv-sprint-prep`). TestRail writes go through the vendored server: `testrail_add_case`, `testrail_add_cases`, and `testrail_add_section` — reached only through the one `draft-test-cases` how-to, from `/bc:vnv-sprint-prep` phase 7, the `/bc:coverage` gap-fill offer, and `/bc:ask` scenario mapping — and `testrail_update_case` and `testrail_update_cases`, reached only through the one `update-test-cases` how-to, from `/bc:ask` edits, the `/bc:coverage` orphan repair, the `/bc:contradiction` fix offer, and phase 7's refresh
- **Jama is read-only by construction.** The vendored Jama server has no write tools at all: every tool is `jama_get_*`, its HTTP client can only issue GET requests, and its test suite fails if that ever changes. That is why the hook can auto-approve the `jama_get_*` glob. Jama's own hosted MCP server is deliberately not used — it exposes create/edit/move tools and has no bulk reads
- The Jama credential is the Jama site's key and nowhere else — the server reads it only from the key store, never from environment variables. Prefer an API client ID/secret on a read-only Jama account; it is revocable without touching the account password
- The hook's TestRail allowlist is the glob `testrail_get_*`, which is safe only because every TestRail write tool is named `testrail_add_*` or `testrail_update_*`. Never name a write tool `testrail_get_*` — the server's test suite fails if one is
- **Run folders hold requirement and test case text on disk.** `/bc:coverage`, `/bc:contradiction`, and `/bc:ask` lookups write what they fetch to `.buddy-council/runs/` in your project — inside `.buddy-council/`, which `/bc:setup` keeps out of git — with files readable only by you (`chmod 600`). Only the newest 5 runs are kept, none longer than a week; delete the folder any time. The read tools' `save_to` option is the servers' only local write, and it accepts nothing but a `.json` file directly inside an existing run folder — never a symbolic link, never anywhere else
- **Updates guard what was there.** The update tools read every case first and send only what differs, refuse to remove a requirement link unless the user asked for that explicitly, refuse to overwrite a case edited since it was read, verify every field against what TestRail stored (it silently discards unknown custom fields), and never move a case or change its template. No path deletes a test case
- **`/bc:vnv-sprint-prep` is dry-run by default** because it writes to tickets other teams own — and, in phase 7, to the team's TestRail suite. A plain run creates nothing, comments nowhere, relabels nothing, and writes or updates no test cases; `--apply` executes after one batch confirmation per phase
- The Jira token is the Jira site's key and nowhere else. The Jira connector gives it to that site's mcp-atlassian copy in an env file that exists only while the copy starts (`chmod 600`, in a private folder), never on a command line or in a process's environment, and strips every other Jira or Confluence setting from the copy's environment. The site's key file is the single place to rotate or revoke it
- **The automation tools are pinned and kept apart.** Node is checked against its SHA-256 and every npm package against the lockfile's integrity hash, so a mirror or proxy can never change what gets installed; `npm ci --ignore-scripts` runs no package's install scripts. They live in `~/.buddy-council/tools` — never on PATH, never shared — and setup never edits a shell profile, `JAVA_HOME`, or `ANDROID_HOME`. The `appium` server may only talk to Appium servers on this Mac (`REMOTE_SERVER_URL_ALLOW_REGEX`) and never downloads an app from a URL (`ALLOW_REMOTE_APP_URLS=false`)
- **On the phone, only reading is auto-approved**: appium-mcp's eight read-only tools, plus page sources and screenshots, which it saves in `~/.buddy-council/automation`. Everything that taps, types, opens a session, or launches an app always prompts. Setup's own check of the phone opens one session without launching any app, reads one screen, and closes it
- Bundled hooks run in Claude Code, Copilot CLI, and VS Code (Claude Code loads `hooks/hooks.json`; Copilot CLI and VS Code load `com.github.copilot/hooks/hooks.json` — same scripts). Codex and Cursor don't run them yet, so there every one of these operations prompts. If a Copilot version still prompts for MCP reads, the `--allow-tool` recipe under [Installation](#copilot-cli--fewer-permission-prompts) covers the gap:
  - a PreToolUse hook hard-blocks destructive Bash commands (`rm -rf`, `kill`, `git push --force`, etc.)
  - the same hook **auto-approves** the plugin's curated read-only operations (`gh api` reads, `jq`, the TestRail connection test, and read-only MCP fetches), the run-folder script `bc_run.py`, the key store `bc_keys.py` (`path`, `sites`, `check`, `migrate` — never `forget`), and the automation script `bc_tools.py` — but only as one plain command: `python3`, the plugin's own copy by absolute path, a known subcommand, and arguments that are bare or single-quoted, so nothing can be chained onto it. `bc_tools.py project` (which runs your project's own setup script) and `bc_tools.py remove` always prompt
  - a second PreToolUse hook auto-approves writes **only** to the plugin's own generated files (`.buddy-council/` config and progress log) — never a key in `~/.buddy-council/keys/`, and never an assistant's MCP settings; all other writes still prompt
  - a PostToolUse hook writes the redacted tool log described under [Debugging a bc run](#debugging-a-bc-run) — metadata only, credentials masked, never file contents or responses

## Adding a New Provider

1. Create `providers/<name>/fetch.md` with fetch instructions
2. Update the router how-to (`howtos/fetch-requirements.md`, `howtos/fetch-test-cases.md`, or `howtos/fetch-board-issues.md`)
3. Update `/bc:setup` to offer the new provider as an option
4. Optionally add a connector in `mcp-servers/<name>/` whose every tool takes `site`: declare it in `mcp.json` and `.claude-plugin/plugin.json`, add it to `scripts/bc-mcp`, and its sign-in fields to `scripts/bc_keys.py`

No changes to agents or analysis how-tos required.

## License

MIT
