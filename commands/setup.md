---
description: Configure Buddy-Council data sources and credentials in four steps with a single review-and-save.
---

# /bc:setup — Configure Buddy-Council Data Sources

You are the Buddy-Council setup assistant. Walk the user through configuring their data sources and credentials.

The wizard has **four user-facing steps**. Prefix the first message of each step with a progress marker — `[Step 1/4] Requirements`, `[Step 2/4] Test cases`, `[Step 3/4] Jira dev board`, `[Step 4/4] Review & save` — so the user always knows how much is left. Keep questions to a minimum: detect and default wherever possible, and batch confirmation into the single save prompt in Step 4.

## Step 0: Re-run detection

Before anything else, check whether `.buddy-council/sources.json` exists in the current working directory.

**If it exists**, this is a re-run. Show a compact summary of the current config and ask what to change instead of re-walking every step:

```
Buddy-Council is already configured in this project:
  Requirements:  excel — /path/to/requirements.xls (7 columns mapped)
  Test cases:    testrail — https://company.testrail.io (project 1, authoring configured)
  Jira board:    PROJ board 42 (or: NOT VERIFIED — re-testing this run)
  Enrichment:    cli
  Code mapping:  enabled

What would you like to change? [requirements / test cases / jira / everything / nothing]: _
```

Only walk the steps for the sections the user names; carry every other section over unchanged when writing config in Step 4.

**Two exceptions, because the Jira board is required** — both run even when the user answers "nothing":

- **Missing `jira` block** → the config predates the requirement, or an earlier run was abandoned. Say so and walk Step 3 regardless of what they asked to change.
- **`jira.pending` is `true`** → re-test now, using the full 3a-bis procedure (MCP tool if live, `curl` otherwise). On success drop `pending` and report `Jira board: PROJ board 42 — verified`. If it still fails, leave `pending` as it is and remind them `/bc:validate` stays blocked until it clears. Never silently keep a stale `pending: true` that would now verify — and never leave it set without having attempted a call this run.

**Pre-0.21.1 config migration** — also unconditional, because these configs cannot work as written:

- **`jira.cloud_id` present, or `jira.deployment` missing** → the config targets Atlassian's hosted Cloud-only server. Drop `cloud_id`, detect `deployment` from `base_url`, and walk Step 3a to collect the credential the `uvx` server needs. Explain in one line why: the hosted server is gone, and it never worked against Jira Server/Data Center at all.
- **`~/.buddy-council/atlassian.env` missing while a `jira` block exists** → same thing; the credential was never collected. Walk Step 3a.

**If it does not exist**, run all steps in order.

In both cases, resolve the **plugin install path** now, using the procedure in Step 4a-bis — Step 1 runs the bundled Excel parser from it (`<plugin_root>/providers/excel/parse.py`, invoked with `uv run`; its PEP 723 header lets uv provision Python and dependencies automatically, so nothing needs to be installed). Reuse the resolved value when writing `plugin_root` in Step 4.

### Step 0a: Path health check (runs on every re-run, including "nothing")

Claude Code's install path embeds the plugin version, so **every plugin update invalidates the paths recorded by the previous setup**. Before asking the user anything, compare the freshly resolved install path against what is stored:

- `plugin_root` in `.buddy-council/sources.json`
- each `--directory` argument in the project's `.mcp.json`
- each `--directory` argument in `~/.copilot/mcp-config.json`, **and whether that file exists at all** — a config written before this check was added will be missing it entirely, which is the single most common reason TestRail tools don't load under Copilot. If it is absent, create it per Step 4c instead of only repairing paths.

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

### Step 2a: Case authoring defaults (one question, only when the tools are live)

`/bc:vnv-sprint-prep` phase 7 writes test cases into this instance. Creating a case needs three ids and a
folder policy that have no safe universal default, so resolve them **now**, once, rather than asking on every
run. This is a single question — everything else is detected.

Skip this sub-step entirely when the MCP tools are not yet live (first-time setup): omit `authoring`, and the
next `/bc:setup` run fills it in. Phase 7 skips itself with a visible line until it exists, which is correct
— it must never guess a template.

With the tools live:

1. `testrail_get_templates` for the project. **Pick the steps-style template automatically** — the one whose
   name contains "Steps" — because scenarios are written as Given/When/Then and only that template has the
   `custom_steps_separated` field to hold them. If no steps template exists, take the default and say that
   steps will be written as plain text into `custom_steps`/`custom_expected` instead.
2. `testrail_get_case_types` → default to the type named "Functional", else the instance default.
3. `testrail_get_priorities` → default to the middle priority, else the instance default.
4. Ask the **one** question — where generated cases should live:

   > Where should `/bc:vnv-sprint-prep` file the test cases it generates?
   > - Under a `V&V` folder, mirroring each feature (`V&V/Sync/Offline handling`) — keeps generated cases separate from hand-written ones
   > - Mirroring the feature directly (`Sync/Offline handling`) — mixed in with existing cases
   > - All in one fixed folder — you name it

Write the block, and show the resolved names (not just ids) in the Step 4 recap so the user can veto:

```json
"authoring": {
  "template_id": 2,
  "type_id": 7,
  "priority_id": 4,
  "section_strategy": "feature",
  "section_root": "V&V",
  "create_missing_sections": true
}
```

`section_strategy` is `"feature"` (folder path from each requirement's feature) or `"fixed"` (everything in
`section_root`). `section_root` is `null` when the user picked the un-prefixed option.
`create_missing_sections` defaults to `true`; set it to `false` only if the user says their suite structure
is fixed and a missing folder should be an error.

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

## Step 3 of 4: Jira (Required)

**This step is not optional — do not offer to skip it.** The dev board is where the team's in-flight work
lives, and every analysis command reads it: `/bc:contradiction` and `/bc:coverage` compare board issues
against requirements and test cases, and `/bc:validate` files new tickets onto it.

Jira runs on **`mcp-atlassian`** (<https://github.com/sooperset/mcp-atlassian>), launched with `uvx` as a
local stdio server pinned to `mcp-atlassian@0.23.1`. Nothing needs installing ahead of time — `uvx`
provisions it on first launch. It speaks to **both Atlassian Cloud and Jira Server/Data Center**, and unlike
Atlassian's hosted server it exposes real **board and sprint tools**, which is what lets this plugin address
a board by id instead of approximating it with a project-wide JQL.

Unlike the plugin's earlier Jira integration, this step **does collect a credential** — `mcp-atlassian`
authenticates as the user, not by browser OAuth. It goes into `~/.buddy-council/atlassian.env` (`chmod 600`),
never into `.mcp.json`, `~/.copilot/mcp-config.json`, or any committed file.

### 3a: Site and credentials

Ask for the Jira base URL first, then derive everything you can from it:

> What's your Jira base URL? For Cloud it looks like `https://yourorg.atlassian.net`;
> for a self-hosted instance it's whatever your team browses to, e.g. `https://jira.company.com`.

**Detect the deployment from the host — do not ask.** A host ending in `.atlassian.net` (or `.jira.com`) is
**Cloud**; anything else is **Server/Data Center**. Record it as `jira.deployment` (`"cloud"` or `"server"`),
because it decides the auth method, the REST API version the fallback probe uses, and which fields
`/bc:validate` can set. State the detection so a user on an unusual vanity domain can correct it:
`Detected Jira Server/Data Center from jira.company.com — say "it's Cloud" if that's wrong.`

Then ask for the one credential that deployment needs:

| Deployment | Ask for | Env keys | Where the user gets it |
|---|---|---|---|
| Cloud | account email + API token | `JIRA_USERNAME`, `JIRA_API_TOKEN` | <https://id.atlassian.com/manage-profile/security/api-tokens> |
| Server / Data Center | Personal Access Token only | `JIRA_PERSONAL_TOKEN` | Jira → profile menu → **Personal Access Tokens** → Create token |

Server/DC takes **no username** — the PAT identifies the user. Asking for one and writing it into the env
file makes `mcp-atlassian` prefer basic auth and fail with a confusing 401, so don't.

Write `~/.buddy-council/atlassian.env` (`mkdir -p ~/.buddy-council` first, `chmod 600` after). Keep
`TOOLSETS` and `ENABLED_TOOLS` exactly as below — the plugin depends on the agile, links, projects, and users
toolsets, and the explicit allowlist keeps the tool surface small enough that runtimes don't truncate it:

```dotenv
JIRA_URL=https://jira.company.com
JIRA_PERSONAL_TOKEN=<the PAT>
TOOLSETS=default,jira_agile,jira_links,jira_projects,jira_users
ENABLED_TOOLS=jira_get_issue,jira_search,jira_create_issue,jira_update_issue,jira_add_comment,jira_get_transitions,jira_transition_issue,jira_search_fields,jira_get_agile_boards,jira_get_board_issues,jira_get_sprints_from_board,jira_get_sprint_issues,jira_add_issues_to_sprint,jira_get_link_types,jira_create_issue_link,jira_get_all_projects,jira_get_project_issue_types,jira_get_create_fields,jira_get_project_fields,jira_get_user_profile,jira_search_assignable_users
```

For Cloud, swap the token line for `JIRA_USERNAME=` plus `JIRA_API_TOKEN=`. If the user also wants Confluence
reads, append `CONFLUENCE_URL` and the matching `CONFLUENCE_PERSONAL_TOKEN` (Server/DC) or
`CONFLUENCE_USERNAME` + `CONFLUENCE_API_TOKEN` (Cloud), and add `confluence_search,confluence_get_page` to
`ENABLED_TOOLS`. Don't prompt for Confluence — offer it in one line and move on if they decline.

If the file already exists, **merge**: keep the keys the user set by hand, replace only what this run
collected, and never print a token value back to the screen or into the Step 4 recap.

**Self-signed certificates.** If the connection test below fails TLS verification against a Server/DC host,
tell the user they can add `JIRA_SSL_VERIFY=false` to the env file, and say plainly that it disables
certificate checking. Never write that line without asking.

### 3a-bis: Test the connection

**Always produce a verdict here — this step never ends in "unknown".** This mirrors the TestRail check in
Step 2, and it is the difference between a working config and a silent `pending: true`.

1. **If the Atlassian MCP tools are live in this session** — look for `mcp__atlassian__jira_get_user_profile`
   (Claude Code) or `jira_get_user_profile` (Copilot CLI) — call it with `user_identifier` set to the
   configured account (the email on Cloud; on Server/DC pass `currentUser()`, or omit it and let the server
   resolve the token's owner). Report the verdict:
   `Jira: connected as Omar Hegazy — jira.company.com (Server/Data Center)`.

2. **If the tools are NOT live** — the usual case on a first run, because the server only gets registered
   later in Step 4c — do **not** jump to deferral. Probe the REST API directly with the credential you just
   collected, exactly as Step 2 falls back to `curl` for TestRail. Source the env file rather than
   interpolating the token into the command line, so it never lands in shell history or a transcript:

   ```bash
   set -a; . ~/.buddy-council/atlassian.env; set +a
   # Server/Data Center (PAT, REST v2):
   curl -sS -H "Authorization: Bearer $JIRA_PERSONAL_TOKEN" -w '\nHTTP %{http_code}\n' "$JIRA_URL/rest/api/2/myself"
   # Cloud (email + API token, REST v3):
   curl -sS -u "$JIRA_USERNAME:$JIRA_API_TOKEN" -w '\nHTTP %{http_code}\n' "$JIRA_URL/rest/api/3/myself"
   ```

   `200` → connected; read `displayName` out of the response and report it the same way as case 1. Treat the
   probe as authoritative — the credential works, and the tools will be live once the user restarts the
   runtime at the end of setup. Do **not** set `pending`.

3. **Only a failed probe defers.** Map the status honestly rather than reporting a generic failure:

   | Result | What to say |
   |---|---|
   | `401` | The token is wrong, expired, or (Cloud) paired with the wrong email. Offer to re-collect it. |
   | `403` | The credential is valid but the account can't use the API — often a Jira permission or an IP allowlist. |
   | `404` on `/rest/api/2/myself` | The base URL is wrong or points at a proxy. Confirm the URL. |
   | TLS error | Self-signed certificate — see `JIRA_SSL_VERIFY` above. |
   | Connection refused / timeout | The host is unreachable from here. **Ask whether it needs a corporate VPN** — a self-hosted Jira usually does, and this is the most common cause on a laptop that is off the network. |

   Offer one retry, then continue with `"pending": true` per *Deferral* below. Never write `pending: true`
   after a successful probe.

Do not silently skip this test. If you could not run it at all, say why in one line.

### 3b: The dev board

Ask for the board itself:

> Paste the URL of your team's dev board in Jira — open the board and copy the address bar.
> It looks like `https://yourorg.atlassian.net/jira/software/projects/PROJ/boards/42`, or
> `https://jira.company.com/secure/RapidBoard.jspa?rapidView=42` on a self-hosted instance.

Parse it rather than asking for the pieces separately. Accept every layout Jira produces:

| Layout | Example | Extract |
|---|---|---|
| Team-managed / company-managed | `.../jira/software/projects/PROJ/boards/42` | id `42`, key `PROJ` |
| Company-managed with `/c/` | `.../jira/software/c/projects/PROJ/boards/42` | id `42`, key `PROJ` |
| With a tab suffix | `.../boards/42/backlog`, `.../boards/42/timeline` | id `42`, key `PROJ` |
| Classic RapidBoard | `.../secure/RapidBoard.jspa?rapidView=42&projectKey=PROJ` | id `42`, key `PROJ` |
| Classic RapidBoard, no key | `.../secure/RapidBoard.jspa?rapidView=42` | id `42`, key **derived** (below) |

Take the board id from `boards/(\d+)` or `rapidView=(\d+)`, and the project key from `projects/([A-Z][A-Z0-9_]+)`
or `projectKey=([A-Z][A-Z0-9_]+)`.

**Derive the project key rather than asking for it.** The classic RapidBoard layout usually carries no key,
and that is the common case on Server/DC. When the URL has no key and the tools are live, call
`jira_get_board_issues` with `board_id`, `jql: "ORDER BY created DESC"`, `limit: 1`, and take the key prefix
off the returned issue key (`CWA-1234` → `CWA`). Say where it came from:
`Board 1656 → project CWA (derived from its issues)`. Only ask the user if the board is empty or the tools
are not live.

Then:

- **Project key from the board wins** over anything inferred earlier — the board is the thing being
  configured. If they disagree, say so plainly and use the board's, e.g. "Board 42 belongs to `PROJ`, not
  `OTHER` — using `PROJ`."
- If the URL has no recognizable board id, show what you tried to match and ask again. Do **not** invent an
  id and do **not** proceed with a partial board block.
- **Verify the board as a board**, whenever the tools are live — not as a project:
  1. `jira_get_agile_boards` with `project_key: <key>` — find the entry whose `id` matches and report its
     name and type: `Board check: 1656 "CWA Scrum Board" (scrum)`.
  2. `jira_get_board_issues` with `board_id`, `jql: "statusCategory != Done ORDER BY updated DESC"`, and a
     small `limit` — report the count, e.g. `37 open issues`. A zero count is not a failure; report it and
     move on.

  If the id is not in the project's board list, say so and ask again — never record a board id the server
  does not recognize.

**The board reference is exact.** `mcp-atlassian` reads issues by board id
(`${CLAUDE_PLUGIN_ROOT}/skills/fetch-board-issues/SKILL.md`), so analysis sees the board's actual rows —
including boards whose filter spans projects or excludes issue types. No project-wide approximation is
involved, and nothing about this needs caveating to the user.

### 3c: V&V board and workflow settings (for `/bc:vnv-sprint-prep`)

The V&V team's own board, plus the few settings `/bc:vnv-sprint-prep` needs. **Ask, but allow a skip** — unlike the dev
board, `/bc:vnv-sprint-prep` can collect this itself on first run (`--board`, or it prompts). Do not turn this into a
second mandatory gate.

> Do you also run the V&V (Validation & Verification) workflow? If so, paste the V&V board URL — `/bc:vnv-sprint-prep`
> clones sprint stories onto it. Skip if you don't use `/bc:vnv-sprint-prep` yet.

If given, parse and verify it with the same rules as 3b, and record
`jira.vnv_board = {url, id, project_key, discriminator}`.

**A V&V board in the same Jira project as the dev board is supported and common.** Plenty of teams run one
project with several boards, each backed by a different filter. Because the plugin reads and writes boards by
id, the two are never confused. The only hard rule is that the **board ids must differ** — refuse a V&V board
id equal to the dev board id, since that would clone stories onto the board they came from.

#### The clone discriminator

A Jira board is a saved filter, so a new issue lands on whichever boards' filters match it. When the V&V board
sits in its **own project**, the project key alone does that job and no discriminator is needed — record
`"discriminator": null`.

When both boards share a project, `/bc:vnv-sprint-prep` needs to know what makes an issue appear on the V&V
board and not the dev board. Work it out by sampling rather than asking:

1. Call `jira_get_board_issues` for **each** board with `jql: "ORDER BY updated DESC"`, `limit: 50`, and
   `fields: "summary,labels,components,issuetype,status"`.
2. Compare the two samples for an attribute that is near-universal on the V&V board and near-absent on the dev
   board — check in this order: **label**, **component**, **issue type**. Require it on ≥ 80% of V&V issues
   and ≤ 5% of dev issues; anything weaker is not a discriminator.
3. Show the finding and confirm it in one question:
   `Board 1568 looks filtered by label "vnv" (47/50 V&V issues, 0/50 dev issues). Stamp that on every clone?`
4. Record the confirmed answer:

   ```json
   "discriminator": { "kind": "label", "value": "vnv", "confidence": "sampled", "sampled_at": "2026-09-01T12:00:00Z" }
   ```

   `kind` is one of `label`, `component`, `issue_type`, or `sprint`. `confidence` is `"sampled"` when derived
   as above and `"stated"` when the user supplied it.

If sampling is inconclusive — mixed signals, both boards empty, or the tools aren't live — **ask outright**
rather than guessing:

> Both boards are in CWA, so I need to know what puts an issue on board 1568 rather than 1656.
> Usually it's a label, a component, or an issue type. What does the V&V board's filter key off?

Accept `sprint` as an answer too: `/bc:vnv-sprint-prep` then creates the clone and calls
`jira_add_issues_to_sprint` for the V&V board's active sprint instead of stamping a field.

If the user doesn't know, record `"discriminator": null` and warn once, here, that clones will land in the
shared project and may surface on the dev board until someone adjusts the filter. Don't block setup over it —
but don't pretend the placement is solved either.

#### Platform and reviewer

Then collect two more things, both with working defaults so this stays one or two questions:

- **Platform field.** `/bc:vnv-sprint-prep` checks that each iOS story has an Android counterpart. Default to the `OS`
  field; if the tools are live, resolve it with `jira_search_fields` (`keyword: "OS"`) or
  `jira_get_project_fields` and cache the returned custom-field id as `jira.platform.field_id`. If no such
  field exists, say so and record `field_name` anyway — the skill falls back to title prefixes and flags
  lower confidence.
- **Scenario reviewer.** Who approves high-level scenarios. Ask for an email or display name, resolve it with
  `jira_search_assignable_users` (or `jira_get_user_profile`) when available, and store
  `{account_id, display_name}`. On Server/DC the identifier is a username rather than an account id — store
  whatever the server returns in `account_id`. If it cannot be resolved, store the display name alone;
  `/bc:vnv-sprint-prep` re-resolves later.

Write the block with defaults filled in:

```json
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
```

Never prompt for the label names or approval phrases — write the defaults and mention in the Step 4 recap
that they can be hand-edited in `.buddy-council/sources.json`. If the user skipped the V&V board, omit
`vnv_board` and `vnv_workflow` entirely, but still write `platform` if the `OS` field was confirmed — the
parity data is useful to `/bc:coverage` regardless.

### Deferral (when the connection could not be verified)

The requirement is firm, but it must not strand a user who is off the VPN or still waiting on a token. If the
connection test in 3a-bis failed even after a retry:

- Say plainly that the board is required and setup will keep asking until it is confirmed.
- Still collect what they can give: the base URL → `base_url`, the detected deployment → `deployment`, the
  board URL → `board.url` + `board.id` + `project_key`, and the default issue type.
- Still write `~/.buddy-council/atlassian.env` **and both MCP configs** — a credential that turns out to be
  fine once they're on the VPN should never need re-entering.
- Write the `jira` block with **`"pending": true`**.
- Finish the wizard normally. `/bc:contradiction` and `/bc:coverage` keep working — they skip the board
  source with a visible line rather than failing. Only `/bc:validate` is blocked.
- On every later `/bc:setup` re-run, re-verify a pending block first (Step 0) and clear `pending` once the
  connection test succeeds.

Never write `"pending": true` when verification actually succeeded, and never leave the `jira` block out
entirely — an absent block now means "the user has not been through Step 3", which the re-run flow treats
as unfinished setup.

## Step 4 of 4: Review & Save

Before writing anything, show one compact recap of everything collected and ask a **single** save question — this replaces all per-section save prompts:

```
Ready to save:
  Requirements:  excel — /path/to/requirements.xls (skip_rows 3, 7 columns mapped)
  Features:      hierarchical folders (Item Type = "Folder")
  Item types:    Requirement, MAS Software Requirement Specification (Text excluded — narrative)
  Enrichment:    cli (gh CLI, smoke test OK)
  Test cases:    testrail — https://company.testrail.io, project 1
  Case authoring: template "Test Case (Steps)", type "Functional", priority "Medium"
                 generated cases → V&V/<feature>, missing folders created
  Jira:          connected as Jane Doe — jira.company.com (Server/Data Center)
  Jira board:    PROJ board 42 "PROJ Scrum Board" (scrum) — 37 open issues
  V&V board:     PROJ board 77 "V&V Board" — same project, clones stamped label "vnv"
                 parity on OS field; reviewer Jane Doe
                 (labels/approval phrases: edit jira.vnv_workflow in .buddy-council/sources.json)
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
    "suite_id": null,
    "authoring": {
      "template_id": 2,
      "type_id": 7,
      "priority_id": 4,
      "section_strategy": "feature",
      "section_root": "V&V",
      "create_missing_sections": true
    }
  },
  "jira": {
    "base_url": "https://jira.company.com",
    "deployment": "server",
    "project_key": "PROJ",
    "default_issue_type": "Story",
    "board": {
      "url": "https://jira.company.com/secure/RapidBoard.jspa?rapidView=42",
      "id": 42
    },
    "vnv_board": {
      "url": "https://jira.company.com/secure/RapidBoard.jspa?rapidView=77",
      "id": 77,
      "project_key": "PROJ",
      "discriminator": {
        "kind": "label",
        "value": "vnv",
        "confidence": "sampled",
        "sampled_at": "2026-09-01T12:00:00Z"
      }
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
    },
    "pending": false
  },
  "project": {
    "enabled": true,
    "ignore_dirs": ["node_modules", "dist", ".next", "build", "vendor", "__pycache__", ".venv", "target"],
    "id_patterns": ["CWA-REQ-\\d+", "TC-\\d+"]
  }
}
```

The `"jira"` section is **always written** — Step 3 is required. If the connection test failed, write it with `"pending": true`. `board.id` and `vnv_board.id` are integers, not strings. `deployment` is `"cloud"` or `"server"`, detected from the host. `vnv_board.project_key` **may equal** `project_key` — that is a supported layout, and `discriminator` is what keeps the two boards apart; set it to `null` when the V&V board has its own project. There is no `cloud_id`: `mcp-atlassian` is bound to one site by `JIRA_URL` in the env file, so no site id is ever passed to a tool. If a `cloud_id` survives from a pre-0.21.1 config, drop it. If `github_url` column was not mapped or no GitHub strategy is available, set `requirements.enrichment.enabled: false` and omit `strategy`. Omit `item_type_exclude` when the sheet's Item Type sample contains no `Text` rows. If the cwd is not a code project, set `project.enabled: false`. On a re-run (Step 0), carry over unchanged sections verbatim.

### 4a-bis: Record the plugin install path (`plugin_root`)

The MCP servers and the Excel parser live **inside the plugin's install directory**, which must be referenced by an absolute path that works on the current machine — `${CLAUDE_PLUGIN_ROOT}` only resolves under Claude Code, not Copilot CLI. Determine the absolute install path and store it as a top-level `plugin_root` in `.buddy-council/sources.json`:

1. If `${CLAUDE_PLUGIN_ROOT}` resolves to an existing directory that contains `mcp-servers/` → use it (Claude Code).
2. Otherwise auto-detect: search Copilot's real install locations for a directory containing `mcp-servers/testrail-server/server.py` — in this order:
   - `~/.copilot/installed-plugins/*/*/` — installed from a marketplace, e.g. `~/.copilot/installed-plugins/buddy-council/bc/`
   - `~/.copilot/installed-plugins/_direct/*/` — installed straight from the repo URL, or run with `--plugin-dir`. Copilot flattens `owner/repo` into `owner--repo` here, so this looks like `_direct/Omar-Hegazy-Integrant--buddy-council-plugin/`. **That owner segment is normal** — it is the GitHub account the plugin was installed from, not a per-user path.

   If exactly one matches → use it. If several match (both a marketplace and a direct install), prefer the marketplace copy and tell the user which was chosen.
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
  "github": {
    "token": "<github-personal-access-token>"
  }
}
```

**There is no `jira` section here, ever.** The Jira credential lives in `~/.buddy-council/atlassian.env`
instead (Step 3a), because `mcp-atlassian` reads its configuration from a dotenv file rather than from this
plugin's secrets format. Two credential files, one rule: both are `chmod 600`, both live under
`~/.buddy-council/`, and neither is ever referenced from a committed file. If a `jira` block survives here
from a version before 0.21.1, move its token into `atlassian.env` if it is still valid, delete the block, and
tell the user.

If the GitHub enrichment strategy is **not** `mcp` (e.g., CLI was chosen, or enrichment is disabled), omit the `"github"` section — `gh` CLI handles its own credentials.

Set restrictive permissions on the secrets file:

```bash
chmod 600 ~/.buddy-council/secrets.json
```

### 4c: Configure MCP servers

**The two runtimes read different files, and you must write BOTH — always, regardless of which CLI is running.** A user who sets up in one CLI and later opens the other must not have to re-run setup:

| Runtime | File it reads | Schema |
|---|---|---|
| Claude Code | `.mcp.json` (project/plugin root) | `mcpServers.<name>.{command,args,env}` |
| Copilot CLI | `~/.copilot/mcp-config.json` | same, **plus** `type: "local"` and a `tools` allowlist |

Copilot **ignores `.mcp.json` entirely**. Writing only that file is why TestRail tools silently fail to load under Copilot — the server is fine, the wiring is missing.

**Resolve the absolute paths to `uv` and `uvx` first** — run `command -v uv` and `command -v uvx` and use the results (e.g. `/opt/homebrew/bin/uv`, `/opt/homebrew/bin/uvx`) as `command` in **both** files. A bare `"uv"`/`"uvx"` works interactively but the spawned MCP server does not inherit your shell's `PATH`, so it fails with a confusing "server not loaded" error. `uvx` ships with `uv`; if only one resolves, the other is its sibling in the same directory.

If `.mcp.json` does not exist in the plugin root, copy it from `.mcp.example.json`.

`.mcp.json` must contain **no credentials** — only non-secret config (`*_BASE_URL`) and `BC_SECRETS_FILE`. For each server's `--directory`, write the **absolute** `plugin_root` path recorded in 4a-bis (e.g. `<plugin_root>/mcp-servers/testrail-server`) — **not** the `${CLAUDE_PLUGIN_ROOT}` token, which stays literal under Copilot CLI. An absolute path works on both runtimes. Update the blocks like this (substitute the real `<plugin_root>`):

```json
{
  "mcpServers": {
    "testrail": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--directory", "<plugin_root>/mcp-servers/testrail-server", "mcp", "run", "server.py"],
      "env": {
        "TESTRAIL_BASE_URL": "https://company.testrail.io",
        "BC_SECRETS_FILE": "~/.buddy-council/secrets.json"
      }
    },
    "atlassian": {
      "command": "/absolute/path/to/uvx",
      "args": [
        "mcp-atlassian@0.23.1",
        "--env-file", "/Users/<you>/.buddy-council/atlassian.env",
        "--transport", "stdio"
      ]
    }
  }
}
```

**Write the `atlassian` entry into this file too.** Earlier versions deliberately kept it out, because Claude
Code registered Atlassian's hosted server from the plugin manifest and a second entry would have loaded the
same server twice. That no longer applies: the manifest declares no MCP servers as of 0.21.1, because a
`uvx` server needs an absolute interpreter path and an absolute env-file path, and neither can be expressed
in a committed manifest. `atlassian` is now wired exactly like `testrail` — written by setup into both
runtime configs, with real paths resolved on this machine.

**The `--env-file` path must be absolute and fully expanded** — write `/Users/<you>/.buddy-council/atlassian.env`,
never `~/...`. The spawned server does not expand a tilde and will start with no credentials at all,
reporting a confusing "Jira is not configured" instead of a path error.

`<plugin_root>` is the absolute path from 4a-bis — it differs per machine and stays only in the local, gitignored `.mcp.json`, never in a committed file.

#### The Copilot CLI copy — `~/.copilot/mcp-config.json`

Write the same servers again to `~/.copilot/mcp-config.json`, in Copilot's schema. **Merge, never overwrite**:
read the file if it exists, add or replace only the `testrail`/`atlassian`/`github` keys under `mcpServers`,
and leave every other server the user has configured untouched. Create the file (and `~/.copilot/`) if absent.

```json
{
  "mcpServers": {
    "testrail": {
      "type": "local",
      "command": "<absolute path from `command -v uv`>",
      "args": ["run", "--directory", "<plugin_root>/mcp-servers/testrail-server", "mcp", "run", "server.py"],
      "tools": ["*"],
      "env": {
        "TESTRAIL_BASE_URL": "https://company.testrail.io",
        "BC_SECRETS_FILE": "/Users/<you>/.buddy-council/secrets.json"
      }
    },
    "atlassian": {
      "type": "local",
      "command": "<absolute path from `command -v uvx`>",
      "args": [
        "mcp-atlassian@0.23.1",
        "--env-file", "/Users/<you>/.buddy-council/atlassian.env",
        "--transport", "stdio"
      ],
      "tools": ["*"]
    }
  }
}
```

**Write the `atlassian` entry unconditionally** — even when the connection test deferred. It holds no secret
of its own (the credential is in the env file it points at), and having it present means Jira works the
moment the user gets on the VPN or fixes the token, with no second setup run.

**Why the plugin manifest no longer declares it.** Copilot CLI does not reliably pick up MCP servers declared
in a plugin manifest — `copilot plugin install` doesn't merge a plugin's `.mcp.json` into the runtime config
([copilot-cli#2709](https://github.com/github/copilot-cli/issues/2709)) — so Copilot always needed this file.
And as of 0.21.1 Claude Code needs a setup-written entry too, because a `uvx` server takes absolute paths
that a committed manifest cannot contain. One registration per runtime, both written here in 4c, never two.

Three differences from the Claude Code file, all required:

- **`"type": "local"`** — Copilot rejects entries without it.
- **`"tools": ["*"]`** — the per-server tool allowlist. Without it the server loads but exposes nothing.
- **Fully expanded `$HOME`** in `BC_SECRETS_FILE` and in the atlassian `--env-file` — write `/Users/<you>/...`, not `~/...`. The tilde is not expanded here.

Same rules as the Claude Code copy: no credentials in the file itself (base URLs, `BC_SECRETS_FILE`, and the `--env-file` path only), and add `github` only under `strategy: "mcp"`.

The TestRail server reads its credentials (`username`/`api_key`) from `~/.buddy-council/secrets.json`. `BC_SECRETS_FILE` is optional — the server defaults to `~/.buddy-council/secrets.json` — but write it explicitly for clarity. Env vars still take precedence if set, so a legacy `.mcp.json` with literal credentials keeps working.

**Migration.** Clean up both older shapes if you find them, and say what you removed:

- A `"jira"` server pointing at `mcp-servers/jira-server` (pre-0.17.0) — the vendored server no longer
  exists and the entry fails to start.
- An `"atlassian"` server with `"type": "http"` and the `mcp.atlassian.com` URL (0.17.0–0.20.x) — replace it
  with the `uvx` entry above. That hosted server serves **Atlassian Cloud only**, so on a Jira Server/Data
  Center site it could never connect no matter how the OAuth flow went. If the user is on Cloud it did work,
  and it is still worth replacing: the hosted server exposes no board or sprint tools.

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

**Important**: Tell the user that after setup completes, they need to restart their CLI — Claude Code can also just toggle the servers with `/mcp`; Copilot CLI must be fully exited and relaunched — for the MCP servers to become available.

**There is no browser authorization step for Jira.** `mcp-atlassian` authenticates with the token already in
`~/.buddy-council/atlassian.env`, so the server is usable the moment the runtime restarts. Do not tell the
user to run `/mcp` → Authenticate for it; there is nothing to approve.

The first launch downloads `mcp-atlassian` through `uvx`, which takes a few seconds. If Jira tools are
missing right after a restart, that download is the usual cause — say so rather than sending the user to
re-run setup.

## After saving: validate

- Confirm `.buddy-council/sources.json` was written
- Confirm `~/.buddy-council/secrets.json` was written
- Confirm `~/.buddy-council/atlassian.env` was written and is `chmod 600`, and that it carries `JIRA_URL` plus exactly one auth style (`JIRA_PERSONAL_TOKEN`, or `JIRA_USERNAME` + `JIRA_API_TOKEN`) — never both
- Confirm `.mcp.json` was written (base URLs + `BC_SECRETS_FILE`, no secrets) and contains the `atlassian` entry
- Confirm `~/.copilot/mcp-config.json` was written, that every entry has `type: "local"` and `tools: ["*"]`, and that any pre-existing servers in it survived the merge
- Confirm neither MCP config still carries a `jira` entry pointing at the removed `mcp-servers/jira-server`, nor an `atlassian` entry of `type: "http"` pointing at `mcp.atlassian.com`
- Confirm `.buddy-council/sources.json` has a `jira` block with `deployment`, `board.id`, and `board.url`, and that `pending` is `false` whenever the connection test actually succeeded
- Confirm the `command` in both files is an absolute `uv`/`uvx` path that exists on disk, and that the atlassian `--env-file` argument is an absolute path with no `~`
- If a V&V board was configured, confirm its `id` differs from the dev board's, and that `discriminator` is set whenever the two share a `project_key`
- If Excel was configured, confirm the file is readable
- Tell the user:
  1. Restart Claude Code or toggle the MCP servers with `/mcp` for connections to activate
  2. Then run `/bc:contradiction` to detect contradictions or `/bc:validate` to create tickets

## After saving: reduce permission prompts (Copilot CLI)

The bundled hooks run on **both runtimes** (Claude Code loads `hooks/hooks.json`; Copilot CLI 1.0.7x+ loads the plugin-root `hooks.json` — same scripts). They auto-approve the plugin's read-only operations **and writes to its own generated files** (`.buddy-council/` config and progress log, `~/.buddy-council/secrets.json`, the plugin's `.mcp.json`) — no action needed on Claude Code, and usually none on Copilot either.

Because MCP tool naming in Copilot's hooks varies by version, MCP reads may still prompt there. Print a ready-to-paste `--allow-tool` launch recipe covering the MCP read tools that were just configured, and tell the user it's only needed if prompts appear (writes like Jira creation still prompt by design):

- Always include the TestRail read tools:
  `testrail(testrail_get_projects),testrail(testrail_get_suites),testrail(testrail_get_sections),testrail(testrail_get_cases),testrail(testrail_get_cases_by_refs),testrail(testrail_get_case),testrail(testrail_get_case_fields),testrail(testrail_get_case_types),testrail(testrail_get_priorities),testrail(testrail_get_templates)` — deliberately **not** `testrail(testrail_add_case)`, `testrail(testrail_add_cases)`, or `testrail(testrail_add_section)`, which must keep prompting
- If Jira was configured, also add the Atlassian read tools: `atlassian(jira_get_user_profile),atlassian(jira_get_issue),atlassian(jira_search),atlassian(jira_get_agile_boards),atlassian(jira_get_board_issues),atlassian(jira_get_sprints_from_board),atlassian(jira_get_sprint_issues),atlassian(jira_get_all_projects),atlassian(jira_get_project_issue_types),atlassian(jira_get_project_fields),atlassian(jira_search_fields),atlassian(jira_get_create_fields),atlassian(jira_get_transitions),atlassian(jira_get_link_types),atlassian(jira_search_assignable_users)` — deliberately **none** of `jira_create_issue`, `jira_update_issue`, `jira_add_comment`, `jira_transition_issue`, `jira_create_issue_link`, or `jira_add_issues_to_sprint`, which must keep prompting
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
- Step 3 (Jira) is **required** — never offer to skip it. When the connection test fails, record the answers with `"pending": true` and finish the wizard; do not abandon the run and do not omit the `jira` block
- ALWAYS test the Jira connection in 3a-bis, by MCP tool if live and by `curl` otherwise. A `pending: true` written without an attempted call is a bug, not a safe default
- The Jira credential goes in `~/.buddy-council/atlassian.env` (`chmod 600`) and nowhere else — not in `secrets.json`, not in either MCP config, never echoed back to the user
- A V&V board **may** share a project with the dev board. Refuse only a duplicate board *id*, and record a `discriminator` when the projects match
- If the user explicitly asks about Jama: explain the API integration is in progress and that the Excel export path is the supported route for now
