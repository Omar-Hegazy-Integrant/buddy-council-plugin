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
  Test cases:    testrail — https://company.testrail.io (project 1)
  Jira board:    PROJ board 42 (or: NOT VERIFIED — re-check this run)
  Enrichment:    cli
  Code mapping:  enabled

What would you like to change? [requirements / test cases / jira / everything / nothing]: _
```

Only walk the steps for the sections the user names; carry every other section over unchanged when writing config in Step 4.

**Three exceptions, because the Jira board is required** — all three run even when the user answers "nothing":

- **Missing `jira` block** → the config predates the requirement, or an earlier run was abandoned. Say so and walk Step 3 regardless of what they asked to change.
- **`jira.pending` is `true`** → re-verify now. Run the server preflight (Step 3a) and the credential check (Step 3b); if both pass, run the board check from Step 3c, drop `pending`, and report `Jira board: PROJ board 42 — verified`. If it still fails, leave `pending` as it is and remind them `/bc:validate` stays blocked until it clears. Never silently keep a stale `pending: true` that would now verify.
- **`jira.base_url` no longer matches `jira.deployment`** → the team migrated Jira, almost always Server/DC
  → Cloud. Detect it by re-probing `<base_url>/rest/api/2/serverInfo` (Step 3b) and comparing `deploymentType`
  against the recorded value. On a mismatch, walk Step 3: the auth model changes entirely — a Data Center PAT
  does not work on Cloud, and the env file must be rewritten from `JIRA_PERSONAL_TOKEN` to
  `JIRA_USERNAME`+`JIRA_API_TOKEN`. Say exactly that, and **delete the stale variables** rather than leaving
  both shapes in the file. Board and project ids change too on a Cloud migration, so re-verify the board
  rather than trusting the recorded id.
- **`jira.deployment` is missing entirely** → the config predates deployment detection. Probe once and record
  it, without re-walking Step 3 if everything else still verifies.
- **`jira.cloud_id` is present, or either MCP config has an `atlassian` entry pointing at `mcp.atlassian.com`** → this config predates 0.20.0, when Jira moved off Atlassian's OAuth server. **Remove the stale `atlassian` entry from both MCP configs and drop `cloud_id` immediately**, as part of the Step 0a automatic repair — a user who answers "nothing" must not be left with a server that 401s on every call. Then walk Step 3 to collect the API token and write the `uvx` entry. Say why in one line.

**If it does not exist**, run all steps in order.

In both cases, resolve the **plugin install path** now, using the procedure in Step 4a-bis — Step 1 runs the bundled Excel parser from it (`<plugin_root>/providers/excel/parse.py`, invoked with `uv run`; its PEP 723 header lets uv provision Python and dependencies automatically, so nothing needs to be installed). Reuse the resolved value when writing `plugin_root` in Step 4.

### Step 0a: Path health check (runs on every re-run, including "nothing")

Claude Code's install path embeds the plugin version, so **every plugin update invalidates the paths recorded by the previous setup**. Before asking the user anything, compare the freshly resolved install path against what is stored:

- `plugin_root` in `.buddy-council/sources.json`
- each `--directory` argument in the project's `.mcp.json`
- each `--directory` argument in `~/.copilot/mcp-config.json`, **and whether that file exists at all** — a config written before this check was added will be missing it entirely, which is the single most common reason TestRail tools don't load under Copilot. If it is absent, create it per Step 4c instead of only repairing paths.
- the `atlassian` entry in **both** MCP configs. If it has `"type": "http"` or a `mcp.atlassian.com` URL, it is the retired official server: **delete it here**, and drop any `jira.cloud_id` from `sources.json`. This entry has no `--directory`, so the path comparison above will never catch it. Repairing it in Step 0a is what makes the removal actually automatic — a 0.18.x user who answers "nothing" would otherwise keep a server that 401s on every call.

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

## Step 3 of 4: Jira & Confluence (Required)

**This step is not optional — do not offer to skip it.** The dev board is where the team's in-flight work
lives, and every analysis command reads it: `/bc:contradiction` and `/bc:coverage` compare board issues
against requirements and test cases, and `/bc:validate` files new tickets onto it.

Jira and Confluence run on the **`sooperset/mcp-atlassian` server**, launched with `uvx` — the same `uv`
that already runs the TestRail server and the Excel parser, so this adds no new prerequisite and no
background daemon. It authenticates with an **Atlassian API token**. Unlike earlier versions of this plugin,
this step **does collect credentials** — there is no browser OAuth. They go into
`~/.buddy-council/atlassian.env`, never into the repo and never into an MCP config.

This step has four parts: check `uvx`, identify the deployment and collect credentials, pick the dev board, then the V&V settings.

### 3a: Server preflight

Confirm `uvx` is present and can fetch the server. `uv` is already a plugin prerequisite, so this normally
passes silently:

```bash
command -v uvx && uvx mcp-atlassian@0.23.1 --version
```

- **`uvx` not found** → `uv` is not installed, which also breaks Step 1 and Step 2. Point the user at
  https://docs.astral.sh/uv/getting-started/installation/ and continue in *deferred* mode (below) — record
  their answers with `"pending": true` rather than abandoning the run.
- **`uvx` present but the fetch fails** → no network, or a proxy blocking PyPI. Report the error verbatim
  and defer; the version is pinned, so this is a download problem, not a version-resolution one.

The first run downloads and caches the package (a few seconds); later runs start from cache. Report it as
one line — `Atlassian server: mcp-atlassian 0.23.1 ready` — not as a wall of download output.

**Pin the version.** The server changed its default `TOOLSETS` in 0.22.0; floating to latest would let a
future release silently change which tools exist. Write `mcp-atlassian@0.23.1` in both MCP configs and bump
it deliberately, the same way the vendored servers' `uv.lock` is bumped.

### 3b: Deployment type, then credentials

**Jira comes in two deployments and they authenticate differently.** Ask for the URL first and settle this
before asking for anything else — guessing wrong wastes the user's time on a token that cannot work.

> What is your Jira URL? e.g. `https://yourorg.atlassian.net` (Cloud) or `https://jira.company.com`
> (Server / Data Center)

Normalize it before storing: strip a trailing slash and any path (`/jira`, `/browse/...`) so `base_url` is
the bare origin.

#### Detect the deployment — don't infer it from the hostname alone

A `*.atlassian.net` host is always Cloud, but the converse does not hold: a Cloud site can sit behind a
custom domain. Ask the server:

```bash
curl -sS "<base_url>/rest/api/2/serverInfo"
```

`deploymentType` comes back as `"Cloud"` or `"Server"` (Data Center reports `Server`). This endpoint needs no
credentials on most instances, which is why it runs before the token question. If it is locked down, fall
back to the hostname rule and say you did, so a wrong guess is visible rather than silent.

Record the result as **`jira.deployment`** (`"cloud"` or `"server"`) in `sources.json`. Everything below
branches on it, and recording it is what lets a later re-run notice a Cloud migration.

#### Then collect credentials, per deployment

**Cloud** — email plus an API token:

> 1. Your Atlassian account email
> 2. An API token — create one at https://id.atlassian.com/manage-profile/security/api-tokens

**Server / Data Center** — a Personal Access Token, and **no email**:

> A Personal Access Token — in Jira, click your avatar → **Profile** → **Personal Access Tokens** →
> **Create token**.

Server/DC PATs authenticate as `Authorization: Bearer <token>`, not as basic auth, and there is no username
component. Asking a Data Center user for an `id.atlassian.com` token is a dead end — that site only issues
Cloud tokens.

#### Confluence

- **Cloud**: Confluence lives at `<site>/wiki` and takes the *same* email and API token. Derive
  `CONFLUENCE_URL = <site>/wiki` and reuse the credentials — do not ask. Show the derived value in the Step 4
  recap so the user can correct it.
- **Server / Data Center**: Confluence is usually a **separate host** (`https://confluence.company.com`), not
  a path under Jira, and needs its **own** PAT. Ask for both — one question, two fields — and say why the
  Cloud shortcut does not apply. If the user does not use Confluence, omit the `CONFLUENCE_*` variables
  entirely rather than writing a guessed URL that will fail on first use.

**Do not write `sources.json` or either MCP config yet** — those wait for the Step 4 save prompt. The env
file is the one exception, for the reason given below.

#### Verify the credentials immediately

The MCP server cannot be used for this: it is registered in Step 4 and does not load until the CLI restarts.
So verify with a direct REST call — **this is the one place the plugin talks to the Jira API outside MCP**,
and it exists because immediate feedback on a bad token is worth far more than waiting for a restart to find
out. Everywhere else, Jira access goes through the MCP server.

Write the env file **now**, then verify against it. This is a deliberate, narrow exception to the
"nothing is written until Step 4" rule, and it is the only one: the credential file is the thing being
verified, and each shell call runs in a fresh process, so there is nowhere else to keep the token between
the checks in 3b, 3c and 3d. Everything else — `sources.json`, `.mcp.json`, the Copilot config — still waits
for the save confirmation.

Write it exactly as specified in Step 4b, then verify with the call matching the deployment. **The REST API
version differs**: Cloud serves `/rest/api/3`, while Server/Data Center tops out at `/rest/api/2` and returns
404 for v3.

**Cloud** — basic auth, API v3:

```bash
set -a; . ~/.buddy-council/atlassian.env; set +a
curl -sS -o /dev/null -w '%{http_code}' -u "$JIRA_USERNAME:$JIRA_API_TOKEN" \
  "$JIRA_URL/rest/api/3/myself"
```

**Server / Data Center** — bearer auth, API v2:

```bash
set -a; . ~/.buddy-council/atlassian.env; set +a
curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $JIRA_PERSONAL_TOKEN" \
  "$JIRA_URL/rest/api/2/myself"
```

A `-u`-style basic auth call against a DC PAT usually returns 401 and looks exactly like a bad token, so use
the right form rather than trying both and guessing from the result.

Say plainly that the file was created: *"Saved your Atlassian credentials to
`~/.buddy-council/atlassian.env` (chmod 600) so I can verify them."* If the user later answers **no** at the
Step 4 save prompt, offer to delete it — do not leave a credential on disk that they decided against.

Never interpolate the token into a command line literally — always source it from the file, so it stays out
of shell history and out of the tool log.

| Result | Meaning | Action |
|---|---|---|
| `200` | Credentials are good | Report `Atlassian: authenticated as <displayName>` and continue |
| `401` | Bad email or token | Say which two fields to re-check and re-ask. Do not defer on a typo |
| `403` | Token valid, account lacks access | Report it; the user needs Jira permissions from an admin |
| `404` | Wrong site URL — or API v3 against Server/DC | Re-check `jira.deployment` before re-asking for the URL; a v3 call to Data Center 404s even when everything else is right |
| `401` on Server/DC | PAT sent as basic auth | Confirm the call used `Authorization: Bearer`, not `-u` |
| SSL / certificate error | Internal CA on a Data Center instance | Common on self-hosted Jira. Do **not** disable verification silently — tell the user, and only set `JIRA_SSL_VERIFY=false` in the env file if they explicitly accept it |
| connection error | Wrong host, VPN required, or no network | Self-hosted Jira is often reachable only on the corporate network or VPN. Say so — it is the most likely cause for a Data Center URL |

### 3c: The dev board

**If credentials verified, list the boards instead of asking for a URL.** This is the pleasant path — the
user picks from a list rather than hunting for an address:

```bash
set -a; . ~/.buddy-council/atlassian.env; set +a
curl -sS <AUTH> "$JIRA_URL/rest/agile/1.0/board?maxResults=50"
```

Throughout 3c and 3d, `<AUTH>` means the deployment's auth flag from 3b — `-u "$JIRA_USERNAME:$JIRA_API_TOKEN"`
on Cloud, `-H "Authorization: Bearer $JIRA_PERSONAL_TOKEN"` on Server/DC — and `<APIV>` means `3` on Cloud,
`2` on Server/DC. The Agile API (`/rest/agile/1.0/...`) is **unversioned and identical on both**.

Re-source the env file in every shell call that needs credentials — each call is a fresh process, so
variables set in 3b are gone by the time this runs.

Show `id`, `name`, `type` (scrum/kanban) and the project key from `location.projectKey`, and ask which is the
dev board. Record `board.id`, `board.url` (construct it as
`<base_url>/jira/software/projects/<KEY>/boards/<ID>`), and take `project_key` from the board's location.

If the list is long, ask for a project key first and re-request with `?projectKeyOrId=<KEY>`.

**If the board list is unavailable** (deferred mode, or the Agile API returned an error), fall back to asking
for the URL:

> Paste the URL of your team's dev board in Jira — open the board and copy the address bar.

Show an example matching the **deployment detected in 3b**, so the shape is recognizable:

- **Cloud** — `https://yourorg.atlassian.net/jira/software/projects/PROJ/boards/42`
- **Server / Data Center** — `https://jira.company.com/secure/RapidBoard.jspa?rapidView=42`

**Always use these neutral placeholders.** Never build an example out of the user's real site, and never
echo a URL they already entered — showing someone their own dev board as the "example" for the next question
is confusing, and it puts a real internal address into the transcript for no reason. `yourorg` and
`jira.company.com` are the only hostnames that belong in a prompt.

Parse it rather than asking for the pieces separately. Accept every layout Jira produces:

| Layout | Example | Extract |
|---|---|---|
| Team-managed / company-managed | `.../jira/software/projects/PROJ/boards/42` | id `42`, key `PROJ` |
| Company-managed with `/c/` | `.../jira/software/c/projects/PROJ/boards/42` | id `42`, key `PROJ` |
| With a tab suffix | `.../boards/42/backlog`, `.../boards/42/timeline` | id `42`, key `PROJ` |
| Classic RapidBoard | `.../secure/RapidBoard.jspa?rapidView=42&projectKey=PROJ` | id `42`, key `PROJ` |

Take the board id from `boards/(\d+)` or `rapidView=(\d+)`, and the project key from `projects/([A-Z][A-Z0-9_]+)`
or `projectKey=([A-Z][A-Z0-9_]+)`.

Then:

- **Project key from the board wins** over anything chosen elsewhere — the board is the thing being
  configured. If they disagree, say so plainly and use the board's, e.g. "Board 42 belongs to `PROJ`, not
  `OTHER` — using `PROJ`."
- If a pasted URL has no recognizable board id, show what you tried to match and ask again. Do **not** invent
  an id and do **not** proceed with a partial board block.
- **Verify the board reads back**, when credentials are live:
  `set -a; . ~/.buddy-council/atlassian.env; set +a; curl -sS <AUTH> "$JIRA_URL/rest/agile/1.0/board/<ID>/issue?maxResults=1"`
  and report the `total`, e.g. `Board check: board 42 has 37 issues`. A zero count is not a failure — report
  it and move on.

Unlike previous versions, the board id is used **directly** by `jira_get_board_issues` — the board's contents
are read from the board itself, not reconstructed from a project-wide JQL guess. There is no accuracy caveat
to warn the user about anymore.

### 3d: V&V board and workflow settings (for `/bc:vnv-sprint-prep`)

The V&V team's own board, plus the few settings `/bc:vnv-sprint-prep` needs. **Ask, but allow a skip** — unlike the dev
board, `/bc:vnv-sprint-prep` can collect this itself on first run (`--board`, or it prompts). Do not turn this into a
second mandatory gate.

> Do you also run the V&V (Validation & Verification) workflow? If so, pick the V&V board — `/bc:vnv-sprint-prep`
> clones sprint stories onto it. Skip if you don't use `/bc:vnv-sprint-prep` yet.

Offer the same board list from 3c when it is available; otherwise accept a URL and parse it with the same
rules. Record `jira.vnv_board = {url, id, project_key}`.

**Refuse only a V&V board that is the same *board* as the dev board.** Compare `board.id`, not the project
key: a Jira project can host many boards, each backed by its own filter, and running dev and V&V boards side
by side inside one project is a normal setup. If the ids match, say so plainly — cloning a story onto the
board it came from is always a mistake — and ask for the other board.

When the two boards **share a project** (`vnv_board.project_key == jira.project_key`), accept it and say what
follows, once:

> Both boards are in `PROJ`. Clones are created in that project, so whether they also show up on the dev
> board depends on the dev board's own filter. If they do, narrow that filter — the clones all carry the
> `pending-validation` label, which makes them easy to exclude.

Then reassure them about the part that would otherwise be a silent problem: when the projects coincide,
`fetch-board-issues` excludes issues carrying a V&V pipeline label, so clones are never counted as in-flight
dev work by `/bc:contradiction` or `/bc:coverage`.

Also apply the same placeholder rule as 3c — offer the board list where possible, and if you must ask for a
URL, use the neutral example, never the dev board URL the user just gave you.

Then collect two more things, both with working defaults so this stays one or two questions:

- **Platform field.** `/bc:vnv-sprint-prep` checks that each iOS story has an Android counterpart. Default to the `OS`
  field. When credentials are live, resolve its id directly:
  `set -a; . ~/.buddy-council/atlassian.env; set +a; curl -sS <AUTH> "$JIRA_URL/rest/api/<APIV>/field"` and find the entry whose
  `name` matches `OS` (case-insensitive); cache its `id` as `jira.platform.field_id`. If no such field
  exists, say so and record `field_name` anyway — the skill falls back to title prefixes and flags lower
  confidence.
- **Scenario reviewer.** Who approves high-level scenarios. Ask for an email or display name and store
  `{account_id, display_name}`. Resolve the account id when credentials are live:
  on **Cloud**, `curl -sS <AUTH> "$JIRA_URL/rest/api/3/user/search?query=<email>"` and take `accountId`;
  on **Server/DC**, `curl -sS <AUTH> "$JIRA_URL/rest/api/2/user/search?username=<name-or-email>"` and take
  `name` or `key` — the query parameter and the identifier field both differ, and Data Center has no
  `accountId` at all. Store whichever identifier the deployment returned. If it cannot be resolved, store the
  display name alone; `/bc:vnv-sprint-prep` re-resolves it later with `jira_get_user_profile`.

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
`vnv_board` and `vnv_workflow` entirely, but still write `platform` if the `OS` field was found — the
parity data is useful to `/bc:coverage` regardless.

### Deferral (when the server or the credentials aren't ready)

The requirement is firm, but it must not strand a user whose `uv` install is broken, who is behind a proxy
that blocks PyPI, or who has to request a token from an admin. If the preflight fails, or credential
verification never succeeds:

- Say plainly that the board is required and setup will keep asking until it is confirmed.
- Still collect what they can give: the site URL → `base_url`, the detected `deployment` if the
  `serverInfo` probe worked (it needs no credentials on most instances), the board URL → `board.url` +
  `board.id` + `project_key`, and the default issue type.
- Write the `jira` block with **`"pending": true`**.
- Write the env file with whatever credentials were given (or omit it entirely if none were).
- Finish the wizard normally. `/bc:contradiction` and `/bc:coverage` keep working — they skip the board
  source with a visible line rather than failing. Only `/bc:validate` is blocked.
- On every later `/bc:setup` re-run, re-verify a pending block first (Step 0) and clear `pending` once the
  credential check and board check both succeed.

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
  Jira board:    PROJ board 42 — https://company.atlassian.net (Cloud, 37 issues)
  Confluence:    https://company.atlassian.net/wiki (same account + token)
  Atlassian MCP: uvx mcp-atlassian@0.23.1 (ready)
  V&V board:     VV board 77 — parity on OS field; reviewer Jane Doe
                 (labels/approval phrases: edit jira.vnv_workflow in .buddy-council/sources.json)
  Code mapping:  enabled — detected Node project; ID patterns CWA-REQ-\d+, TC-\d+
                 (extra ignore dirs: edit project.ignore_dirs in .buddy-council/sources.json)

Save? [y]: _
```

On yes, write all files (4a–4c below). On no, ask what to change, fix it, and re-show the recap. If they
abandon setup entirely, offer to delete `~/.buddy-council/atlassian.env` — Step 3b wrote it to verify the
credentials, and it should not outlive a run the user rejected.

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
    "deployment": "cloud",
    "project_key": "PROJ",
    "default_issue_type": "Story",
    "board": {
      "url": "https://yourorg.atlassian.net/jira/software/projects/PROJ/boards/42",
      "id": 42
    },
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

The `"jira"` section is **always written** — Step 3 is required. If the preflight or the credentials were not ready, write it with `"pending": true`. Never write `cloud_id` — it was only meaningful to the retired OAuth server; drop it if a pre-0.20.0 config still carries one. `board.id` is an integer, not a string. If `github_url` column was not mapped or no GitHub strategy is available, set `requirements.enrichment.enabled: false` and omit `strategy`. Omit `item_type_exclude` when the sheet's Item Type sample contains no `Text` rows. If the cwd is not a code project, set `project.enabled: false`. On a re-run (Step 0), carry over unchanged sections verbatim.

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

**There is no `jira` section in this file.** Jira and Confluence credentials go in the env file below
instead, because the Atlassian server reads environment variables and cannot read this JSON.

If the GitHub enrichment strategy is **not** `mcp` (e.g., CLI was chosen, or enrichment is disabled), omit the `"github"` section — `gh` CLI handles its own credentials.

Set restrictive permissions on the secrets file:

```bash
chmod 600 ~/.buddy-council/secrets.json
```

#### The Atlassian env file — `~/.buddy-council/atlassian.env`

The Atlassian server takes its configuration as environment variables via `--env-file`, so its credentials live
in a flat env file beside `secrets.json` — same protected directory, same `chmod 600`, still outside the
repo. **Step 3b already wrote this file** so it could verify the credentials; re-write it here only if
something changed since (most often a corrected token), and otherwise just confirm it exists with the right
contents:

**The variable set depends on `jira.deployment`.** Write one shape or the other, never a mixture — the
server picks its auth method from which variables are present, so a stray `JIRA_USERNAME` alongside a PAT
makes it try the wrong one.

**Cloud:**

```bash
cat > ~/.buddy-council/atlassian.env <<'EOF'
JIRA_URL=https://yourorg.atlassian.net
JIRA_USERNAME=you@company.com
JIRA_API_TOKEN=<the API token>

CONFLUENCE_URL=https://yourorg.atlassian.net/wiki
CONFLUENCE_USERNAME=you@company.com
CONFLUENCE_API_TOKEN=<the same API token>

TOOLSETS=default,jira_agile,jira_links,jira_projects,jira_users
ENABLED_TOOLS=jira_get_issue,jira_search,jira_create_issue,jira_update_issue,jira_add_comment,jira_get_transitions,jira_transition_issue,jira_search_fields,jira_get_agile_boards,jira_get_board_issues,jira_get_sprints_from_board,jira_get_sprint_issues,jira_add_issues_to_sprint,jira_get_link_types,jira_create_issue_link,jira_get_all_projects,jira_get_project_issue_types,jira_get_create_fields,jira_get_project_fields,jira_get_user_profile,jira_search_assignable_users,confluence_search,confluence_get_page,confluence_get_page_children,confluence_get_comments
EOF
chmod 600 ~/.buddy-council/atlassian.env
```

**Server / Data Center** — PATs instead of email+token, and Confluence on its own host:

```bash
cat > ~/.buddy-council/atlassian.env <<'EOF'
JIRA_URL=https://jira.company.com
JIRA_PERSONAL_TOKEN=<the Jira PAT>

CONFLUENCE_URL=https://confluence.company.com
CONFLUENCE_PERSONAL_TOKEN=<the Confluence PAT>

TOOLSETS=default,jira_agile,jira_links,jira_projects,jira_users
ENABLED_TOOLS=jira_get_issue,jira_search,jira_create_issue,jira_update_issue,jira_add_comment,jira_get_transitions,jira_transition_issue,jira_search_fields,jira_get_agile_boards,jira_get_board_issues,jira_get_sprints_from_board,jira_get_sprint_issues,jira_add_issues_to_sprint,jira_get_link_types,jira_create_issue_link,jira_get_all_projects,jira_get_project_issue_types,jira_get_create_fields,jira_get_project_fields,jira_get_user_profile,jira_search_assignable_users,confluence_search,confluence_get_page,confluence_get_page_children,confluence_get_comments
EOF
chmod 600 ~/.buddy-council/atlassian.env
```

Omit the two `CONFLUENCE_*` lines entirely if the user does not use Confluence. Add
`JIRA_SSL_VERIFY=false` **only** if the user explicitly accepted that after an internal-CA certificate error
in 3b — never pre-emptively.

Six rules for this file, each of which breaks something specific if ignored:

- **Never mix the two auth shapes.** `JIRA_USERNAME`/`JIRA_API_TOKEN` is Cloud; `JIRA_PERSONAL_TOKEN` is
  Server/DC. Writing both, or carrying a stale one across a Cloud migration, produces 401s that look like a
  bad token rather than a configuration error.

- **`TOOLSETS` must list `jira_agile`, `jira_links`, `jira_projects` and `jira_users` explicitly.** None of
  the four are in the server's `default` set, and **unknown or missing toolsets fail silently** — the tools
  simply do not appear, with no error. Omit `jira_agile` and every board read breaks; omit `jira_users` and
  reviewer resolution in `/bc:vnv-sprint-prep` breaks. Write all five entries even though it looks verbose.
- **`ENABLED_TOOLS` intersects with `TOOLSETS`** — a tool must pass both filters. The list above is exactly
  what the plugin calls: 21 Jira tools plus four Confluence *read* tools. Confluence's page toolset is core,
  so leaving `ENABLED_TOOLS` unset would also expose `confluence_delete_page` and friends; naming the four
  reads keeps the required Confluence credentials useful without handing the agent destructive tools.
- **Never set `READ_ONLY_MODE=true`.** It blocks writes at execution time regardless of `ENABLED_TOOLS`, and
  `/bc:validate` and `/bc:vnv-sprint-prep --apply` would fail with a permission error rather than a clear one.
- **Never write `JIRA_PROJECTS_FILTER`.** It looks like useful hardening and is actively dangerous here.
  The V&V board is optional in Step 3d, and `/bc:vnv-sprint-prep` can collect it later — at which point it
  writes `jira.vnv_board` to `sources.json` and *nothing updates this file*. A filter naming only the dev
  project would then hide the V&V project: the clone duplicate guard's `jira_search` returns zero, the run
  concludes nothing has been cloned yet, and it creates duplicates on a board another team works from. Every
  query the plugin issues is already explicitly project-scoped, so the filter buys no safety — only that
  failure. If a user hand-adds one, warn them it must list every project the plugin touches.
- **No quotes around values, no `export`, no trailing spaces.** The `--env-file` parser is literal: quotes
  become part of the value, which produces authentication failures that look like a wrong token.

Tell the user this file exists, that it holds a real credential, and that revoking the token at
https://id.atlassian.com/manage-profile/security/api-tokens is how you cut off access.

**Migration from a pre-0.20.0 setup.** Earlier versions used Atlassian's official OAuth server and stored no
Jira credential. If `~/.buddy-council/secrets.json` still has a `jira` section from before 0.17.0, delete it
and tell the user that old token is unused and should be revoked.

### 4c: Configure MCP servers

**The two runtimes read different files, and you must write BOTH — always, regardless of which CLI is running.** A user who sets up in one CLI and later opens the other must not have to re-run setup:

| Runtime | File it reads | Schema |
|---|---|---|
| Claude Code | `.mcp.json` (project/plugin root) | `mcpServers.<name>.{command,args,env}` |
| Copilot CLI | `~/.copilot/mcp-config.json` | same, **plus** `type: "local"` and a `tools` allowlist |

Copilot **ignores `.mcp.json` entirely**. Writing only that file is why TestRail tools silently fail to load under Copilot — the server is fine, the wiring is missing.

**Resolve the absolute path to `uv` first** — run `command -v uv` and use the result (e.g. `/opt/homebrew/bin/uv`, `~/.local/bin/uv`) as `command` in **both** files. A bare `"uv"` works interactively but the spawned MCP server does not inherit your shell's `PATH`, so it fails with a confusing "server not loaded" error.

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
    }
  }
}
```

**Add the `atlassian` entry to `.mcp.json` too.** As of 0.20.0 it is an ordinary stdio server like TestRail,
written by setup into both configs — it is no longer declared in the plugin manifest, because the
`--env-file` path is absolute and per-machine, so it cannot be committed:

```json
{
  "mcpServers": {
    "atlassian": {
      "command": "uvx",
      "args": [
        "mcp-atlassian@0.23.1",
        "--env-file", "/Users/<you>/.buddy-council/atlassian.env",
        "--transport", "stdio"
      ]
    }
  }
}
```

**The `--env-file` path must be absolute and fully expanded.** A tilde is not expanded here, and produces a
"no such file" error at startup. Note also that no credential appears in this entry — the env file holds
them, which is why `.mcp.json` stays secret-free even for Jira.

`<plugin_root>` is the absolute path from 4a-bis — it differs per machine and stays only in the local, gitignored `.mcp.json`, never in a committed file.

#### The Copilot CLI copy — `~/.copilot/mcp-config.json`

Write the same servers again to `~/.copilot/mcp-config.json`, in Copilot's schema. **Merge, never
overwrite**: read the file if it exists, add or replace only the `testrail`/`atlassian`/`github` keys under
`mcpServers`, and leave every other server the user has configured untouched. Create the file (and
`~/.copilot/`) if absent.

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

**Resolve `uvx` the same way you resolved `uv`** — `command -v uvx` — and write the absolute result (e.g.
`/opt/homebrew/bin/uvx`). A bare `"uvx"` fails for exactly the same reason a bare `"uv"` does: the spawned
server does not inherit your shell's `PATH`.

Three differences from the Claude Code file, all required:

- **`"type": "local"`** — Copilot rejects entries without it. It applies to the Atlassian server too:
  `local` describes how Copilot spawns the process (a stdio subprocess), not where Jira lives.
- **`"tools": ["*"]`** — the per-server tool allowlist. Without it the server loads but exposes nothing. The
  real narrowing happens in the env file's `ENABLED_TOOLS`, so `["*"]` here is not over-permissive.
- **Fully expanded `$HOME`** in `BC_SECRETS_FILE` and in `--env-file`. Neither is tilde-expanded.

Same rules as the Claude Code copy: no credentials in either file, and add `github` only under
`strategy: "mcp"`.

The TestRail server reads its credentials (`username`/`api_key`) from `~/.buddy-council/secrets.json`. `BC_SECRETS_FILE` is optional — the server defaults to `~/.buddy-council/secrets.json` — but write it explicitly for clarity. Env vars still take precedence if set, so a legacy `.mcp.json` with literal credentials keeps working.

**Migration from an older setup.** Remove two stale shapes if you find them:

- A `"jira"` server pointing at `mcp-servers/jira-server` (pre-0.17.0, vendored, since deleted).
- An `"atlassian"` server with `"type": "http"` and a `mcp.atlassian.com` URL (0.17.0–0.18.x, the official
  OAuth server). Replace it with the `uvx` entry above. Leaving it behind gives the user two servers named
  `atlassian`, or a stale one that fails every call with 401.

Tell the user which were removed.

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

**There is no authorization step anymore.** The server authenticates with the API token in the env file, so
once the CLI restarts the Jira tools work immediately — no browser consent, no `/mcp` → Authenticate, and no
dependency on a site admin enabling anything. If a user is coming from 0.18.x, say this explicitly; they
will otherwise go looking for the OAuth prompt.

If Atlassian tools are missing after a restart, the cause is one of three things, in this order:

1. **`command` is not an absolute `uvx` path** — the spawned server does not inherit your `PATH`.
2. **The `--env-file` path is wrong or not absolute** — check the entry in both MCP configs.
3. **A bad token** — re-run the 3b verification curl.

Verify by hand with the same command the CLI runs. It should print a JSON-RPC handshake rather than an error:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' \
  | uvx mcp-atlassian@0.23.1 --env-file ~/.buddy-council/atlassian.env --transport stdio
```

## After saving: validate

- Confirm `.buddy-council/sources.json` was written
- Confirm `~/.buddy-council/secrets.json` was written
- Confirm `~/.buddy-council/atlassian.env` was written and is `chmod 600`, and that its `TOOLSETS` line
  contains all four non-core toolsets (`jira_agile`, `jira_links`, `jira_projects`, `jira_users`) — a missing
  one fails silently at runtime, so check it here where it is still cheap to fix
- Confirm the env file carries **exactly one** auth shape matching `jira.deployment`: `JIRA_USERNAME` +
  `JIRA_API_TOKEN` for `cloud`, `JIRA_PERSONAL_TOKEN` for `server`, never both
- Confirm `.buddy-council/sources.json` records `jira.deployment`
- Confirm `.mcp.json` was written (base URLs + `BC_SECRETS_FILE`, no secrets)
- Confirm `~/.copilot/mcp-config.json` was written, contains every server with `type: "local"` and `tools: ["*"]`, and that any pre-existing servers in it survived the merge
- Confirm neither MCP config still carries a `jira` entry pointing at the removed `mcp-servers/jira-server`, nor an `atlassian` entry with an `mcp.atlassian.com` URL
- Confirm both MCP configs use an **absolute** `--env-file` path with no `~`
- Confirm `.buddy-council/sources.json` has a `jira` block with a `board.id` and `board.url`, and that `pending` is `false` whenever the credential and board checks actually succeeded
- Confirm the `command` in both files is an absolute `uv` / `uvx` path that exists on disk
- If Excel was configured, confirm the file is readable
- Tell the user:
  1. Restart Claude Code or toggle the MCP servers with `/mcp` for connections to activate
  2. Then run `/bc:contradiction` to detect contradictions or `/bc:validate` to create tickets

## After saving: reduce permission prompts (Copilot CLI)

The bundled hooks run on **both runtimes** (Claude Code loads `hooks/hooks.json`; Copilot CLI 1.0.7x+ loads the plugin-root `hooks.json` — same scripts). They auto-approve the plugin's read-only operations **and writes to its own generated files** (`.buddy-council/` config and progress log, `~/.buddy-council/secrets.json`, the plugin's `.mcp.json`) — no action needed on Claude Code, and usually none on Copilot either.

Because MCP tool naming in Copilot's hooks varies by version, MCP reads may still prompt there. Print a ready-to-paste `--allow-tool` launch recipe covering the MCP read tools that were just configured, and tell the user it's only needed if prompts appear (writes like Jira creation still prompt by design):

- Always include the TestRail read tools:
  `testrail(testrail_get_projects),testrail(testrail_get_suites),testrail(testrail_get_sections),testrail(testrail_get_cases),testrail(testrail_get_cases_by_refs),testrail(testrail_get_case)`
- If Jira was configured, also add the Atlassian read tools: `atlassian(jira_get_issue),atlassian(jira_search),atlassian(jira_get_all_projects),atlassian(jira_get_project_issue_types),atlassian(jira_search_fields),atlassian(jira_get_agile_boards),atlassian(jira_get_board_issues),atlassian(jira_get_sprints_from_board),atlassian(jira_get_sprint_issues),atlassian(jira_get_user_profile)` — deliberately **not** `atlassian(jira_create_issue)`, `atlassian(jira_update_issue)`, `atlassian(jira_add_comment)`, `atlassian(jira_create_issue_link)` or `atlassian(jira_transition_issue)`, which must keep prompting
- If GitHub enrichment uses the `mcp` strategy, also add: `github(get_file_contents)`

Present it as a single command, e.g.:

```bash
copilot --allow-tool='<comma-separated list from above>'
```

If the user's Copilot version predates plugin hooks, tell them to also append the entries the hooks would otherwise cover — `shell(jq:*),shell(gh api:*),write(.buddy-council/sources.json),write(.buddy-council/secrets.json),write(.buddy-council/onboarding-progress.json),write(<plugin_root>/.mcp.json)` (substitute the real `<plugin_root>` recorded in config) — and to choose **"always allow"** when the Excel parser or TestRail connection test first prompts. This step is informational only — do **not** edit any Copilot config files (Copilot manages `~/.copilot/config.json` itself; there is no documented per-tool allowlist file to write).

## Important

- NEVER write credentials into `.buddy-council/sources.json` — that file is user-specific and contains no secrets
- ALWAYS write credentials ONLY into `~/.buddy-council/` — `secrets.json` for the servers that read JSON (TestRail, GitHub), `atlassian.env` for the Atlassian server that reads environment variables. Both `chmod 600`. `.mcp.json` gets non-secret env (`*_BASE_URL`) plus `BC_SECRETS_FILE`, never tokens (the external GitHub MCP server is the sole exception)
- `.mcp.json` is gitignored; under this design it carries no secrets (except the GitHub MCP token). The Atlassian entry references the env file by path rather than inlining credentials, so adding Jira did not add a secret to it
- If `~/.buddy-council/secrets.json` already exists, merge new entries without overwriting existing ones
- If `.mcp.json` already exists, merge new server configs without overwriting other servers
- Step 3 (Jira & Confluence) is **required** — never offer to skip it. When the server preflight or the credentials are not ready, record the answers with `"pending": true` and finish the wizard; do not abandon the run and do not omit the `jira` block
- Ask for the credential that matches the deployment: an **email + API token** on Cloud, a **Personal Access Token** on Server/Data Center. Never send a DC user to `id.atlassian.com`; it only issues Cloud tokens
- DO ask for a credential at all — that changed in 0.20.0. The `sooperset/mcp-atlassian` server has no browser OAuth, so the credential is required and lives in `~/.buddy-council/atlassian.env`. Never put it in `sources.json` or either MCP config
- If the user explicitly asks about Jama: explain the API integration is in progress and that the Excel export path is the supported route for now
