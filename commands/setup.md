---
description: Configure Buddy-Council data sources and credentials in four steps with a single review-and-save, and install the automation tools when it finds a test project.
---

# /bc:setup — Configure Buddy-Council Data Sources

You are the Buddy-Council setup assistant. Walk the user through configuring their data sources and credentials.

The wizard has **four user-facing steps**. Prefix the first message of each step with a progress marker — `[Step 1/4] Requirements`, `[Step 2/4] Test cases`, `[Step 3/4] Jira dev board`, `[Step 4/4] Review & save` — so the user always knows how much is left. Keep questions to a minimum: detect and default wherever possible, and batch confirmation into the single save prompt in Step 4.

## Step 0: Re-run detection

Before anything else, check whether `.buddy-council/sources.json` exists in the current working directory.

**If it exists**, this is a re-run. Show a compact summary of the current config and ask what to change instead of re-walking every step:

```
Buddy-Council is already configured in this project:
  Requirements:  jama — https://company.jamacloud.com, project CWA (42),
                 root Mobile Software Requirements, 23 features
  Test cases:    testrail — https://company.testrail.io (project 1, authoring configured)
  Jira board:    PROJ board 42 (or: NOT VERIFIED — re-testing this run)
  Enrichment:    cli
  Code mapping:  enabled
  Automation:    ready — appium-mcp 1.99.2, checked 2026-11-05
                 (or: pending — no phone is connected over USB yet (re-checking this run))

What would you like to change? [requirements / test cases / jira / automation / everything / nothing]: _
```

The `Automation:` line is the `line` that `python3 '<plugin_root>/skills/automation-tools/bc_tools.py' status --project '<cwd>'`
prints (resolve `<plugin_root>` first, per Step 4a-bis); omit it when that `line` is `null` (no test project here).

Only walk the steps for the sections the user names; carry every other section over unchanged when writing config in Step 4.

**A test project found for the first time joins this run without a question.** When `status` reports
`"enabled": null` with `"test_project": true` — a test project, and no `automation` block yet — its line already
says so. Whatever the user answers, even "nothing", run the **Automation** section below and include it in the
Step 4 recap and its single save question; "leave out automation" there skips it. Once left out, setup only
mentions it (`Automation: left out — say "automation" to add it`) and never brings it up again by itself.

**Two exceptions, because the Jira board is required** — both run even when the user answers "nothing":

- **Missing `jira` block** → the config predates the requirement, or an earlier run was abandoned. Say so and walk Step 3 regardless of what they asked to change.
- **`jira.pending` is `true`** → re-test now, using the full 3a-bis procedure (MCP tool if live, `curl` otherwise). On success drop `pending` and report `Jira board: PROJ board 42 — verified`. If it still fails, leave `pending` as it is and remind them `/bc:validate` stays blocked until it clears. Never silently keep a stale `pending: true` that would now verify — and never leave it set without having attempted a call this run.

**Excel configs** — also unconditional: when `requirements.provider` is `excel`, say in one line that
requirements now come only from Jama, and walk Step 1 for the Jama project, whatever the user answered.

**Pre-0.21.1 config migration** — also unconditional, because these configs cannot work as written:

- **`jira.cloud_id` present, or `jira.deployment` missing** → the config targets Atlassian's hosted Cloud-only server. Drop `cloud_id`, detect `deployment` from `base_url`, and walk Step 3a to collect the credential the `uvx` server needs. Explain in one line why: the plugin no longer uses the hosted server, and it never worked against Jira Server/Data Center at all.
- **`~/.buddy-council/atlassian.env` missing while a `jira` block exists** → same thing; the credential was never collected. Walk Step 3a.

**If it does not exist**, run all steps in order.

In both cases, resolve the **plugin install path** now, using the procedure in Step 4a-bis — Step 1 runs the Jama server's connection check from it (`uv run` provisions Python and its dependencies automatically, so nothing needs to be installed). Reuse the resolved value when writing `plugin_root` in Step 4.

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

**Then the automation tools** — only when `.buddy-council/sources.json` has `automation.enabled: true`, and also
before answering "nothing". After the repair above (so the new plugin path's `.mcp.json` exists), run `status`
(the same command as in Step 0). When its `needs` is empty, do nothing and say nothing. Otherwise — a plugin
update changed a pin (`install`), a config lost or holds a stale `appium` entry (`wire`), or something was
pending (`retry`) — run the same two calls as 4d steps 2–3, each with a 10-minute timeout:

```bash
python3 '<plugin_root>/skills/automation-tools/bc_tools.py' ensure --project '<cwd>'
python3 '<plugin_root>/skills/automation-tools/bc_tools.py' smoke --project '<cwd>'
```

`smoke` only when `ensure` reported `"phone_check": "due"`. `ensure` installs a pin a plugin update changed —
keeping the old version wired, in both CLIs, until the new server answers, then removing it — re-adds the
`appium` entry to a `.mcp.json` that a plugin update replaced, and retries a failed download. Both ask nothing,
and the bash hook approves both. Show their `lines`, then use a fresh `status` line in the summary. A pending
result never blocks the rest of setup.

## Step 1 of 4: Requirements (Jama)

Requirements come from Jama, read live through the plugin's read-only Jama server. Run **Step 1a (Jama)**,
then **GitHub enrichment detection** (Step 1b) if a GitHub doc-link field was mapped.

### Step 1a (Jama): Connection, project, and features

Jama is read through the plugin's own **read-only** MCP server (`mcp-servers/jama-server/`): every tool is
`jama_get_*`, and its HTTP client can only issue GET requests, so nothing the plugin does can change Jama.
Features are Jama **folders** — each requirement belongs to its nearest folder.

1. **Base URL.** Ask for the address the team opens Jama at (e.g. `https://company.jamacloud.com`, or a
   self-hosted `https://jama.company.com`). Accept a pasted link to any Jama page: keep the scheme, host, and
   any context path before `/perspective.req`, and if the link carries `projectId=<n>` or `/projects/<n>`,
   remember that id for item 4. Require `https://` — the server refuses to send credentials over plain HTTP.

2. **Credentials.** Ask for a Jama API **client ID and client secret** — Jama → profile menu → **Set API
   Credentials** → Create. That is OAuth client credentials: no browser login, revocable on its own, and never
   the account password. Only when the instance offers no API credentials (some older self-hosted versions),
   fall back to username + password, and say plainly that this stores the account password. Recommend
   creating them on an account with **read-only** project permissions — defense in depth on top of the
   server's own guarantee.

   Write them **now**, because the connection test reads them: merge into `~/.buddy-council/secrets.json`
   under `jama` (`mkdir -p ~/.buddy-council` first, `chmod 600` after), with exactly one auth style:

   ```json
   { "jama": { "client_id": "<client id>", "client_secret": "<client secret>" } }
   ```

   Never echo the secret back, and never put it in `sources.json` or either MCP config.

3. **Test the connection — always produce a verdict.** If the `jama` tools are live
   (`mcp__jama__jama_get_current_user` under Claude Code, `jama_get_current_user` under Copilot CLI), call it.
   Otherwise — the usual first run, because the server is only registered in Step 4c — run the server's
   one-shot check. It reads the credential from the secrets file, so nothing secret goes on the command line:

   ```bash
   JAMA_BASE_URL="<base url>" BC_SECRETS_FILE="$HOME/.buddy-council/secrets.json" \
     uv run --quiet --directory "<plugin_root>/mcp-servers/jama-server" python server.py --check
   ```

   It prints JSON — `{"ok": true, "user": {...}, "projects": [...], "summary": "..."}`, or `{"ok": false,
   "error": "..."}` with a non-zero exit. Report the verdict:
   `Jama: connected as Jane Doe — company.jamacloud.com (OAuth API credentials)`. Map failures honestly:

   | The error says | What to tell the user |
   |---|---|
   | rejected the API client ID/secret | Wrong or revoked credentials — offer to re-collect them. |
   | HTTP 403 / Managed Access Control | The credentials work, but the account may not use the REST API. A Jama admin enables it under Admin → REST API → Managed Access Control. |
   | redirected … single sign-on | The base URL is wrong, or the REST API sits behind SSO. Confirm the URL. |
   | Could not reach | The host is unreachable from here — **ask whether it needs the corporate VPN**. Behind a TLS-inspecting proxy, `SSL_CERT_FILE` must point at the company CA bundle. |

   Offer one retry. If it still fails, Jama cannot be verified this run: say so, and stop here — every analysis
   command needs requirements, so there is nothing useful to save until Jama answers.

4. **Project.** List the projects from the check (or `jama_get_projects`) and ask which one — unless the pasted
   URL named a project id that is in the list, in which case use it and show it in the recap instead of asking.

5. **Inspect the project.** Run the check again with `--project <id>` appended (or, with the tools live, call
   `jama_get_item_types` with `project_id` and `jama_get_features`). It reads the project tree once and
   reports item-type counts, folders (features), document-key samples, and field candidates. Decide the
   following **without asking**, surfacing each in the Step 4 recap:

   - **Features are folders.** Write `feature_inference: { "strategy": "hierarchical_folder",
     "folder_item_type": "Folder" }`, and show the folder count with a few paths (`features.sample`). If the
     project has **no folders**, say requirements will group by their Set or Component instead, and ask once
     whether another item type marks features (e.g. a "Feature" type) — record its display name as
     `folder_item_type`. In a large project that count mostly measures how much *else* the project holds —
     other documents, archives — so do not dwell on it: Step 2b scopes the requirements to the document the
     test suite links to, and keeps the rest as background.
   - **Item types.** Folders, Sets, and Components are never requirements — the server drops them itself.
     Write `item_type_exclude` with `Text` (narrative) plus any test-management type present, such as
     `Test Case` — this plugin reads test cases from TestRail. Every other type is included.
   - **Field mapping** — one confirmation. Fill the rows from
     `field_candidates`, and leave a row as `(none)` when nothing matched:

     ```
     Proposed field mapping (Jama → canonical):
       Requirement ID   ← document key (always)
       Title            ← Name (always)
       Description      ← Description (always)
       Status           ← Status
       Rationale        ← Rationale
       GitHub doc URL   ← Linked to Github   (e.g. https://github.com/org/repo/blob/main/docs/sds.md)

     Look right? [Enter to accept, or tell me what to change]: _
     ```

     Persist it as `field_mapping`, keyed `status` / `rationale` / `github_url`, with the field **labels** as
     values. Omit a key whose row is `(none)`.

### Step 1b: GitHub enrichment (auto-detected — no strategy question)

**Only run this step if a `github_url` field was mapped in Step 1a.** Otherwise skip silently and proceed to Step 2.

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

After the strategy is chosen, try fetching one example URL end-to-end: the `first_url` the Step 1a check reported for the mapped field. If a URL is found, fetch it via the chosen strategy (CLI: `gh api repos/.../contents/...` and base64-decode; MCP: call `mcp__github__get_file_contents`). Show the user the first 200 chars of the decoded content as a preview:

```
Smoke test: fetching <first URL from sheet>...
  Fetched 4,231 chars from <url>
  Preview: "# Patient Monitoring Architecture\n\nThe patient monitoring..."
```

Do **not** ask to save here — config is saved once, in Step 4. If the smoke test fails (auth, repo not accessible, network), offer to retry, switch strategy, or continue with enrichment disabled.

## Step 2 of 4: Test Cases (TestRail)

Test cases come from **TestRail** — currently the only supported test-case source, so there is no menu here. Tell the user you're configuring TestRail, then:

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

### Step 2a: Case authoring defaults (no questions, only when the tools are live)

`/bc:vnv-sprint-prep` phase 7 writes test cases into this instance. Creating a case needs three ids that
differ per instance and a folder policy, so resolve them **now**, once, rather than on every run. **Nothing
here is a question** — the ids are detected, the folder policy is defaulted from how the suite is already
organized, and the Step 4 recap shows both in plain words, where the user can change them.

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
4. **Resolve the requirement-link field — the one that silently breaks everything.** Call
   `testrail_get_case_fields` and find the custom field this instance uses to reference requirements: match
   a `system_name` containing `req`, `jama`, `requirement`, or `story` (`custom_jama_req_id` is the common
   one). Confirm the guess against reality by reading a handful of existing cases with `testrail_get_cases`
   and checking which field actually holds requirement-shaped IDs — the config's `project.id_patterns`
   (e.g. `CWA-REQ-\d+`) tells you what those look like.

   Record it as `requirement_field`. **If nothing matches, say so plainly and record `null`** — do not guess
   a field name. `/bc:coverage` builds `linked_ids` from this field, so a wrong name means every case the
   plugin creates comes back an orphan while its requirement still reads untested. That failure is silent:
   TestRail accepts a payload with an unknown `custom_*` key and discards the value without an error.

   State what you found: `Requirement link: custom_jama_req_id (found on 18 of 20 sampled cases)`.
5. **Choose where generated cases go — do not ask.** Folder placement can't be judged in the abstract at
   setup time, and doesn't need to be: every path that creates cases — `/bc:vnv-sprint-prep`, the
   `/bc:coverage` gap-fill, and `/bc:ask` scenario mapping — shows each case's folder in its plan and waits
   for a yes before writing. Default from what step 4 found:

   - The requirement field was populated on **most** sampled cases → `section_strategy: "linked"`,
     `section_root: null`. The suite already organizes its cases, and new ones belong among them.
   - Otherwise → `section_strategy: "feature"`, `section_root: "V&V"`. With few links there is little to file
     beside, and one `V&V` folder keeps generated cases together instead of scattering new feature folders
     across the suite.

   **On a re-run, keep an existing `section_strategy` and `section_root`** — the user may have chosen them —
   unless they ask to change them.

   Show the placement as one line in the Step 4 recap, using the wording below verbatim — no config keys and
   no command names, since the reader may not have run any command yet. When the user wants something else,
   at the recap or on any later run, map their words onto the config:

   | Placement | Config | Recap line |
   |---|---|---|
   | Beside existing cases (default) | `"linked"`, root `null` | `new cases are saved next to existing cases for the same requirement or feature` |
   | Their own folder, one subfolder per feature | `"feature"`, root `"V&V"` or the folder they name | `new cases are saved in a <root> folder, one subfolder per feature` |
   | A folder per feature, among the existing ones | `"feature"`, root `null` | `new cases are saved in a folder named after their feature` |
   | Everything in one folder | `"fixed"`, root = the folder they name | `new cases are all saved in <root>` |

Write the block, and show the resolved names (not just ids) in the Step 4 recap so the user can veto:

```json
"authoring": {
  "template_id": 2,
  "type_id": 7,
  "priority_id": 4,
  "requirement_field": "custom_jama_req_id",
  "section_strategy": "linked",
  "section_root": null,
  "create_missing_sections": true
}
```

`section_strategy` is `"linked"` (beside the existing cases for the same requirement, else the same feature,
else a feature folder), `"feature"` (folder path from each requirement's feature), or `"fixed"` (everything in
`section_root`). `section_root` is `null` for an un-prefixed placement.
`create_missing_sections` defaults to `true`; set it to `false` only if the user says their suite structure
is fixed and a missing folder should be an error.

### Step 2b: Anchor the requirements on the test suite (no questions)

A large Jama project holds far more than the requirements this team tests — other documents, system
requirements, risk analyses, archives. The test suite already knows which part matters: its cases link to
specific requirements, and those live in one **document** — a Jama Set, such as `Mobile App SRS`. That whole
document becomes the requirements **root**, so every requirement in it is in scope and one without a linked
case shows as untested instead of vanishing. The **features** stay the folders where the links concentrate,
however deep the document nests them. Everything else in the project is **background**: `/bc:ask` lookups
search it to enrich their answers, and no report ever counts it.

Decide all of it from the evidence, **without asking**. Roots, depths, and anchors are not something a person
can weigh in the abstract — and they don't need to: the Step 4 recap shows the result in plain words, and the
user changes it there or on any later run.

Run this when the TestRail tools are live and Step 2a resolved a
`requirement_field`. Otherwise skip it with a visible line — `Requirements root: not anchored yet — re-run
/bc:setup once the TestRail tools are live` — and save without `roots`: everything still works on the whole
project, with every folder a feature.

1. **What the tests link to.** Call `testrail_get_requirement_ids` with the project, suite,
   `requirement_field`, and `sample_size: 2000`. It returns counts, the ID prefixes in use, and the IDs —
   all of them when the suite links to 2,000 or fewer distinct IDs, otherwise an even spread. Passing every
   ID makes the "not tested yet" list exact. When `distinct_ids` exceeds the sample, say that the per-feature
   link counts come from a sample, so a feature listed as not tested may still have a few links.
2. **Where those live in Jama.** With the Jama tools live, call `jama_get_scope_proposal` with
   `document_keys` set to the sample and the Step 1a `item_type_exclude`. Otherwise write the sample to a
   temp file, one ID per line, and run the check — it prints JSON with a `scope_proposal`:

   ```bash
   JAMA_BASE_URL="<base url>" BC_SECRETS_FILE="$HOME/.buddy-council/secrets.json" \
     uv run --quiet --directory "<plugin_root>/mcp-servers/jama-server" python server.py \
     --check --project <id> --anchors-file <temp file> --item-type-exclude Text
   ```

   The proposal finds where the links concentrate (`linked_folders`), widens that to its whole document
   (`roots`), and counts `feature_depth` from the root so the features stay the folders the links sit in.
3. **Record** for the Step 4 save, straight from the proposal:
   - `requirements.roots`: each of `roots` as `{"id": <id>, "path": "<path>"}` — the id is what the server
     uses; the path is for people reading the config
   - `requirements.feature_inference.depth`: the proposal's `feature_depth`
   - `requirements.item_type_filter`: the proposal's `proposed_item_types` — the types the tests link to
   - `requirements.exclude`: on a re-run, the saved entries that still sit inside the new root; otherwise omit it

   On a re-run whose proposal differs from the saved `roots`, the recap says what the root was, so a root the
   user chose earlier is never replaced unseen.
4. **Show it in the Step 4 recap, in plain words** — the reader may never have opened Jama's tree, so no ids
   and no "root", "depth", "anchor", or "item type filter":

   ```
     In scope:      the whole "Mobile App SRS (SRS-1000003-01)" document — the one your test cases link to
                    420 requirements in 11 features (General Requirements, App Setup, Alerts, …)
                    every one has a linked test case
     Background:    the rest of the project — 3,190 items (system requirements, design, labeling,
                    standards, …) — is searched to enrich answers, never counted in reports
   ```

   Add a line only for what applies, in these words:
   - Features without a linked case (`features.without_links`): `not tested yet: Non-Functional (25) — say
     "leave out Non-Functional" if it is an archive`. Only the user knows whether an untested folder is a gap
     or an archive, so list them and never drop one on your own.
   - Same-type requirements outside the document (`requirements.of_type_outside_where`): `25 more of the same
     kind sit in Archive / Archive- MAS Software Requirement Specifications — background, not counted`
   - Links outside the document (`outliers`): `41 of the IDs your tests link to sit outside this document
     (e.g. CWA-REQ-412 in Product / Legacy / Import) — not counted`
   - IDs Jama does not have (`anchors.not_found`): `2 IDs your test cases link to are not in Jama
     (NO_REQUIREMENTS_OK, TBD) — deleted or mistyped`
   - A re-run that changed it: `was: Mobile App SRS / REQUIREMENTS / Functional Requirements — say "keep the
     previous scope" to keep it`
5. **When the user asks for a change** — at the recap, or on any later run — preview it with the same tool
   (`root_ids`, `exclude_ids`, `feature_depth`; on the CLI `--root`, `--exclude`, `--feature-depth`), then
   show the recap again. Folder ids are in the proposal's `candidates`, `linked_folders`, and
   `features.list`; with `root_ids`, `feature_depth` counts from that root.
   - "leave out <folder>" → add its id to `exclude_ids`
   - "only <folder>" or "use <folder>" → `root_ids` = its id, `feature_depth` 1
   - "split the features one level deeper" → the same `root_ids`, `feature_depth` + 1
   - "the whole project" → save no `roots`
   - "keep the previous scope" → the saved `roots`, `exclude`, and `depth`, unchanged

Edge cases — still no question; each becomes a recap line:

- **No root proposed** — the links are spread across the project. Save no root, and say so: `In scope: the
  whole project — your test cases link to requirements all over it, so nothing narrower fits`.
- **`anchors.resolved` is 0** — none of the IDs exist in this Jama project: either it is the wrong project, or
  the requirement field holds something other than Jama document keys. Save no root and lead the recap with
  `None of the 1,480 requirement IDs your test cases link to exist in this Jama project — check the project
  before saving.`

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

#### Requirement ID pattern — infer from the requirements source

Do **not** ask the user to type the ID pattern blind. Infer it from real IDs. For **Jama**, use the `document_key_samples` the Step 1a project check returned (a few keys per item type — e.g. `CWA-REQ-85`, `CWA-SRS-12`), keeping only the types that were not excluded.

From the sample values, derive a grep regex: escape the literal prefix and generalize the numeric part to `\d+` (e.g. samples `CWA-REQ-85, CWA-REQ-86, CWA-REQ-92` → `CWA-REQ-\d+`). Then:

- **Exactly one prefix detected** (the normal case) → accept it automatically, no prompt. Show it in the Step 4 review summary (`ID patterns: CWA-REQ-\d+, TC-\d+`).
- **Multiple distinct prefixes** → produce one pattern per prefix and confirm them with the user in a single question.
- **Sampling fails** (no Jama samples) → ask, with the default `CWA-REQ-\d+, TC-\d+`.

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
provisions it on first launch. It speaks to **both Atlassian Cloud and Jira Server/Data Center** — Atlassian's
hosted server is Cloud-only — and it exposes real **board and sprint tools**, which is what lets this plugin
address a board by id instead of approximating it with a project-wide JQL.

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

## Automation — automatic, no questions

`/bc:automate` turns TestRail cases into automated tests by driving the app on a phone through **appium-mcp**.
This section gets the tools for that ready **without a single extra question**: it detects, plans, and puts the
plan in the Step 4 recap, where the one save confirmation covers it. It runs after Step 3 so every question is
answered before the slower checks start. Follow `${CLAUDE_PLUGIN_ROOT}/skills/automation-tools/SKILL.md`; the
steps here are the setup-specific part.

1. **Detect.** Run, from the current working directory:

   ```bash
   python3 '<plugin_root>/skills/automation-tools/bc_tools.py' detect --project '<cwd>'
   ```

   - `test_project: false`, or `applies: false` → **skip this section silently**: no recap line, nothing
     downloaded. Analysis-only projects never hear about automation.
   - Otherwise keep the result: its `lines` go into the recap as they are, `plan.shared` says which shared
     tools the Mac is missing, and `setup_script` names the project's own setup script, if it has one.

2. **The project's own check.** When `setup_script` is not `null`, run its check-only mode:

   ```bash
   python3 '<plugin_root>/skills/automation-tools/bc_tools.py' project --project '<cwd>' --mode check
   ```

   This one reaches the normal permission prompt — it runs the project's own code — and can take a few
   minutes: give it a 10-minute timeout. The script stops the project's script itself after 9 minutes. Add its `line` to the recap and, when it reports `problems`, up to four of them, then the line
   `its fix runs after you save` (the fix runs in 4d). A check that finds nothing wrong adds only its `line`.

3. **Recap.** The automation lines go last in the Step 4 recap, followed by
   `(say "leave out automation" to skip it)`. They already say what will be installed, how big it is, which
   phone and app it found, and what only the user can do — plugging in the phone, `sudo`, the App Store,
   signing. Add nothing that implies a question.

On **"leave out automation"**, write `"automation": { "enabled": false }` in 4a and skip 4d. Anything else the
user says about automation in the save answer — "don't install the Android tools" — is applied before saving,
like any other correction to the recap.

## Step 4 of 4: Review & Save

Before writing anything, show one compact recap of everything collected and ask a **single** save question — this replaces all per-section save prompts:

```
Ready to save:
  Requirements:  jama — https://company.jamacloud.com, project CWA "Clinical Window App" (42) — read-only
                 connected as Jane Doe (OAuth API credentials)
  In scope:      the whole "Software Requirements" document — the one your test cases link to
                 1,512 requirements in 23 features (Login, Patient Monitoring, Alarms, …)
                 not tested yet: Release 3 Drafts (40) — say "leave out Release 3 Drafts" if it is an archive
  Background:    the rest of the project — 2,900 items (system requirements, hazards, design, …) —
                 is searched to enrich answers, never counted in reports
  Field mapping: status ← Status, rationale ← Rationale, GitHub doc URL ← Linked to Github
  Item types:    Mobile SRS (Text and Test Case excluded)
  Enrichment:    cli (gh CLI, smoke test OK)
  Test cases:    testrail — https://company.testrail.io, project 1
  Case authoring: template "Test Case (Steps)", type "Functional", priority "Medium"
                 requirement link → custom_jama_req_id (+ built-in References)
                 new cases are saved next to existing cases for the same requirement or feature
                 (want them somewhere else? say where before saving)
  Jira:          connected as Jane Doe — jira.company.com (Server/Data Center)
  Jira board:    PROJ board 42 "PROJ Scrum Board" (scrum) — 37 open issues
  V&V board:     PROJ board 77 "V&V Board" — same project, clones stamped label "vnv"
                 parity on OS field; reviewer Jane Doe
                 (labels/approval phrases: edit jira.vnv_workflow in .buddy-council/sources.json)
  Code mapping:  enabled — detected Node project; ID patterns CWA-REQ-\d+, TC-\d+
                 (extra ignore dirs: edit project.ignore_dirs in .buddy-council/sources.json)
  Automation:    Appium test project (Java, TestNG, Gradle) — iOS and Android
                 drives the phone through the project's own Appium 2.19.0 (uiautomator2 4.2.9, xcuitest 9.10.5)
                 phone: QA iPhone (APOLLO_UDID) · app: ~/Desktop/apollo.ipa · language: the phone's own
                 installs bc's own Node 26.13.0 and appium-mcp 1.99.2 into ~/.buddy-council/tools — about 620 MB on disk, never on PATH
                 the project's ./setup --check: 31 OK, 1 warning, 1 problem
                   ❌ WebDriverAgent isn't signed for QA iPhone
                 its fix runs after you save
                 (say "leave out automation" to skip it)

Save? [y]: _
```

On yes, write all files (4a–4c below), then install the automation tools (4d) when the recap included them. On
no, ask what to change — unless the answer already says — fix it, and re-show the recap.

### 4a: Write source config

Config lives at `.buddy-council/sources.json` **in the user's project root** (the current working directory) — not the plugin folder, and not home. Both Claude Code and Copilot CLI find it relative to the project, with no plugin-path token. Config is therefore **per-project**: each project the user runs the plugin in gets its own.

Ensure the directory exists, then make sure `.buddy-council/` is git-excluded via the repo-local `.git/info/exclude` exactly as the onboarding log does (follow `manage-progress-log` Operation 1 step 3 — no `.gitignore` edit):

```bash
mkdir -p .buddy-council
```

**Migration:** if an older `config/sources.json` exists inside the plugin directory (from before this move), copy its contents into `.buddy-council/sources.json` and tell the user it was migrated.

Write `.buddy-council/sources.json` with the selected providers and non-secret settings. Include the field-mapping, enrichment, and project blocks as collected in Steps 1a, 1b, and the code-mapping section. No credentials, ever — those went to `~/.buddy-council/secrets.json` in Step 1a:

```json
{
  "requirements": {
    "provider": "jama",
    "base_url": "https://company.jamacloud.com",
    "project_id": 42,
    "project_key": "CWA",
    "project_name": "Clinical Window App",
    "roots": [{ "id": 1001, "path": "Product / Software Requirements" }],
    "exclude": [{ "id": 1950, "path": "Product / Software Requirements / Archive" }],
    "feature_inference": {
      "strategy": "hierarchical_folder",
      "folder_item_type": "Folder",
      "depth": 1
    },
    "item_type_filter": ["Mobile SRS"],
    "item_type_exclude": ["Text", "Test Case"],
    "field_mapping": {
      "status": "Status",
      "rationale": "Rationale",
      "github_url": "Linked to Github"
    },
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
      "requirement_field": "custom_jama_req_id",
      "section_strategy": "linked",
      "section_root": null,
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

The `"jira"` section is **always written** — Step 3 is required. If the connection test failed, write it with `"pending": true`. `board.id` and `vnv_board.id` are integers, not strings. `deployment` is `"cloud"` or `"server"`, detected from the host. `vnv_board.project_key` **may equal** `project_key` — that is a supported layout, and `discriminator` is what keeps the two boards apart; set it to `null` when the V&V board has its own project. There is no `cloud_id`: `mcp-atlassian` is bound to one site by `JIRA_URL` in the env file, so no site id is ever passed to a tool. If a `cloud_id` survives from a pre-0.21.1 config, drop it. If no `github_url` field was mapped, or no GitHub strategy is available, set `requirements.enrichment.enabled: false` and omit `strategy`. Omit `item_type_exclude` when the project has none of the excluded types. If the cwd is not a code project, set `project.enabled: false`. On a re-run (Step 0), carry over unchanged sections verbatim.

**Never write the `automation` block yourself** — `bc_tools.py ensure` writes it in 4d, from what it actually
installed and proved, and every later `ensure` keeps it current. On a re-run, carry an existing block over
verbatim. The one exception is "leave out automation", which writes `"automation": { "enabled": false }`.

`project_id` is an integer. `base_url` is the normalized root (no `/rest/v1`, no `/perspective.req…`).
`roots`, `exclude`, `feature_inference.depth`, and `item_type_filter` come from Step 2b; omit all four when
the requirements were not anchored, and every folder in the project is then a feature.

### 4a-bis: Record the plugin install path (`plugin_root`)

The MCP servers live **inside the plugin's install directory**, which must be referenced by an absolute path that works on the current machine — `${CLAUDE_PLUGIN_ROOT}` only resolves under Claude Code, not Copilot CLI. Determine the absolute install path and store it as a top-level `plugin_root` in `.buddy-council/sources.json`:

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

The file also carries the `jama` section written in Step 1a — keep it, with exactly one auth style:

```json
{
  "jama": {
    "client_id": "<Jama API client id>",
    "client_secret": "<Jama API client secret>"
  }
}
```

(`username` + `password` replace the pair only on instances without API credentials.) The `jama` MCP server
reads this section itself; the base URL is not a secret and lives in `JAMA_BASE_URL` instead.

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

**The `jama` entry.** It is wired exactly like `testrail`: the
vendored server under `<plugin_root>/mcp-servers/jama-server`, the non-secret `JAMA_BASE_URL`, and
`BC_SECRETS_FILE` pointing at the secrets file whose `jama` section holds the credential.

```json
{
  "mcpServers": {
    "jama": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--directory", "<plugin_root>/mcp-servers/jama-server", "mcp", "run", "server.py"],
      "env": {
        "JAMA_BASE_URL": "https://company.jamacloud.com",
        "BC_SECRETS_FILE": "~/.buddy-council/secrets.json"
      }
    }
  }
}
```

**The `--env-file` path must be absolute and fully expanded** — write `/Users/<you>/.buddy-council/atlassian.env`,
never `~/...`. The spawned server does not expand a tilde and will start with no credentials at all,
reporting a confusing "Jira is not configured" instead of a path error.

`<plugin_root>` is the absolute path from 4a-bis — it differs per machine and stays only in the local, gitignored `.mcp.json`, never in a committed file.

#### The Copilot CLI copy — `~/.copilot/mcp-config.json`

Write the same servers again to `~/.copilot/mcp-config.json`, in Copilot's schema. **Merge, never overwrite**:
read the file if it exists, add or replace only the `testrail`/`atlassian`/`github`/`jama` keys under `mcpServers`,
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

Add the `jama` entry here as well, in Copilot's shape.
`"tools": ["*"]` is safe for this server in particular: it has no write tools to expose.

```json
{
  "mcpServers": {
    "jama": {
      "type": "local",
      "command": "<absolute path from `command -v uv`>",
      "args": ["run", "--directory", "<plugin_root>/mcp-servers/jama-server", "mcp", "run", "server.py"],
      "tools": ["*"],
      "env": {
        "JAMA_BASE_URL": "https://company.jamacloud.com",
        "BC_SECRETS_FILE": "/Users/<you>/.buddy-council/secrets.json"
      }
    }
  }
}
```

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

The TestRail and Jama servers read their credentials (`testrail.username`/`api_key`, `jama.client_id`/`client_secret`) from `~/.buddy-council/secrets.json`. `BC_SECRETS_FILE` is optional — both default to `~/.buddy-council/secrets.json` — but write it explicitly for clarity. TestRail env vars still take precedence if set, so a legacy `.mcp.json` with literal credentials keeps working; the Jama server deliberately reads its credential from the secrets file only.

**Migration.** Clean up both older shapes if you find them, and say what you removed:

- A `"jira"` server pointing at `mcp-servers/jira-server` (pre-0.17.0) — the vendored server no longer
  exists and the entry fails to start.
- An `"atlassian"` server with `"type": "http"` and the `mcp.atlassian.com` URL (0.17.0–0.20.x) — replace it
  with the `uvx` entry above. That hosted server serves **Atlassian Cloud only**, so on a Jira Server/Data
  Center site it could never connect no matter how the OAuth flow went. On Cloud it did work, but it must
  still be replaced: every skill and the auto-approve hook name `mcp-atlassian`'s `jira_*` tools, which the
  hosted server does not expose.

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

**Jama needs no browser authorization either.** The `jama` server exchanges the API client ID/secret for a
token itself, on the first call, and renews it when it expires.

### 4d: Automation tools (only when the recap included them)

Skip this step when the Automation section found no test project, or the user said "leave out automation".
Otherwise, in this order, with no further questions:

1. **The project's fix, when its check reported problems.** Run:

   ```bash
   python3 '<plugin_root>/skills/automation-tools/bc_tools.py' project --project '<cwd>' --mode fix
   ```

   It reaches the normal permission prompt (it runs the project's own code). Without a terminal, a script like
   ApolloAutomation's `./setup` fixes what needs no answer and reports the rest. Show its `line`; when it
   returns `needs_you`, show that too, in its own words — `run ./setup in a terminal for what needs you: …` —
   because a password, tapping Trust on the phone, or choosing a signing profile can only be done there.

2. **Install, wire, and prove the server answers:**

   ```bash
   python3 '<plugin_root>/skills/automation-tools/bc_tools.py' ensure --project '<cwd>'
   ```

   Add `--shared jdk`, `--shared android-sdk`, or `--shared jdk,android-sdk` only for what the recap said it
   would install. The first run installs about 620 MB and usually takes one to three minutes — say so before
   running it, and give the call a 10-minute timeout (Claude Code's limit; under Copilot CLI, wait for it). The
   bash hook approves it, so it asks nothing. It records `pending` before installing, so a run stopped early is
   finished by the next `/bc:setup`. It wires the `appium` server into **both** MCP configs itself
   (`<plugin_root>/.mcp.json` and `~/.copilot/mcp-config.json`, by absolute paths), merging like 4c, and never
   replaces an `appium` server the user added themselves. It records the result in `.buddy-council/sources.json`.

3. **Read one screen from the phone** — only when `ensure` reported `"phone_check": "due"`:

   ```bash
   python3 '<plugin_root>/skills/automation-tools/bc_tools.py' smoke --project '<cwd>'
   ```

   Also a 10-minute timeout: the first session on an iPhone builds WebDriverAgent, which can take a few
   minutes. It opens one session without launching any app, saves one page source, and closes it.

4. **Show the `lines` of each call as they are.** `ready` means the server answered and a page source was read
   from the phone. `pending` names what is missing — most often the phone — and what to do; the next
   `/bc:setup` checks again by itself (Step 0a), the way it re-tests a pending Jira board. A phone that passed
   once stays `ready` when it is unplugged later; a check that fails on a connected phone makes it `pending`
   again. A pending result never makes the rest of setup fail.

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
- Confirm `requirements.provider` is `"jama"` with an integer `project_id`, and any `roots`/`exclude` entries carry integer `id`s; `~/.buddy-council/secrets.json` has a `jama` section with exactly one auth style; both MCP configs have a `jama` entry whose `--directory` is `<plugin_root>/mcp-servers/jama-server` and whose env holds only `JAMA_BASE_URL` and `BC_SECRETS_FILE`
- If automation was included, confirm `ensure` (and `smoke`, when it ran) returned `"ok": true` with a `status` — `ready` or `pending` — and that `.buddy-council/sources.json` now has an `automation` block. The script itself checks the rest (the server answered before it was wired, both `appium` entries use absolute paths that exist); do not re-check it by hand
- Tell the user:
  1. Restart Claude Code or toggle the MCP servers with `/mcp` for connections to activate — the `appium` server, too, when automation was set up
  2. Then run `/bc:contradiction` to detect contradictions or `/bc:validate` to create tickets

## After saving: reduce permission prompts (Copilot CLI)

The bundled hooks run on **both runtimes** (Claude Code loads `hooks/hooks.json`; Copilot CLI 1.0.7x+ loads the plugin-root `hooks.json` — same scripts). They auto-approve the plugin's read-only operations **and writes to its own generated files** (`.buddy-council/` config and progress log, `~/.buddy-council/secrets.json`, the plugin's `.mcp.json`) — no action needed on Claude Code, and usually none on Copilot either.

Because MCP tool naming in Copilot's hooks varies by version, MCP reads may still prompt there. Print a ready-to-paste `--allow-tool` launch recipe covering the MCP read tools that were just configured, and tell the user it's only needed if prompts appear (writes like Jira creation still prompt by design):

- Always include the TestRail read tools:
  `testrail(testrail_get_projects),testrail(testrail_get_suites),testrail(testrail_get_sections),testrail(testrail_get_cases),testrail(testrail_get_cases_by_refs),testrail(testrail_get_case),testrail(testrail_get_case_fields),testrail(testrail_get_case_types),testrail(testrail_get_priorities),testrail(testrail_get_templates),testrail(testrail_get_requirement_ids),testrail(testrail_get_linked_cases),testrail(testrail_get_canonical_cases)` — deliberately **not** `testrail(testrail_add_case)`, `testrail(testrail_add_cases)`, `testrail(testrail_add_section)`, `testrail(testrail_update_case)`, or `testrail(testrail_update_cases)`, which must keep prompting
- If Jira was configured, also add the Atlassian read tools: `atlassian(jira_get_user_profile),atlassian(jira_get_issue),atlassian(jira_search),atlassian(jira_get_agile_boards),atlassian(jira_get_board_issues),atlassian(jira_get_sprints_from_board),atlassian(jira_get_sprint_issues),atlassian(jira_get_all_projects),atlassian(jira_get_project_issue_types),atlassian(jira_get_project_fields),atlassian(jira_search_fields),atlassian(jira_get_create_fields),atlassian(jira_get_transitions),atlassian(jira_get_link_types),atlassian(jira_search_assignable_users)` — deliberately **none** of `jira_create_issue`, `jira_update_issue`, `jira_add_comment`, `jira_transition_issue`, `jira_create_issue_link`, or `jira_add_issues_to_sprint`, which must keep prompting
- If GitHub enrichment uses the `mcp` strategy, also add: `github(get_file_contents)`
- Also add the Jama tools: `jama(jama_get_current_user),jama(jama_get_projects),jama(jama_get_item_types),jama(jama_get_features),jama(jama_get_requirements),jama(jama_get_item),jama(jama_get_items),jama(jama_get_scope_proposal)` — that is every tool the server has, because it has no write tools
- If automation was set up, also add appium-mcp's read tools: `appium(appium_mobile_device_info),appium(appium_find_element),appium(appium_find_elements),appium(appium_get_text),appium(appium_get_element_attribute),appium(appium_get_active_element),appium(appium_get_window_size),appium(generate_locators),appium(appium_get_page_source),appium(appium_screenshot)` — deliberately **none** of `appium_session_management`, `appium_gesture`, `appium_set_value`, `appium_app_lifecycle`, or anything else that taps, types, opens a session, or launches an app, which must keep prompting

Present it as a single command, e.g.:

```bash
copilot --allow-tool='<comma-separated list from above>'
```

If the user's Copilot version predates plugin hooks, tell them to also append the entries the hooks would otherwise cover — `shell(jq:*),shell(gh api:*),write(.buddy-council/sources.json),write(.buddy-council/secrets.json),write(.buddy-council/onboarding-progress.json),write(<plugin_root>/.mcp.json)` (substitute the real `<plugin_root>` recorded in config) — and to choose **"always allow"** when the TestRail connection test first prompts. This step is informational only — do **not** edit any Copilot config files (Copilot manages `~/.copilot/config.json` itself; there is no documented per-tool allowlist file to write).

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
- Jama is read **only** through the plugin's read-only `jama` server. Its credential goes in the `jama` section of `~/.buddy-council/secrets.json` (`chmod 600`) and nowhere else; `JAMA_BASE_URL` is the only Jama value in either MCP config. The one direct call setup makes is the server's own `--check` mode, before the server is registered
- Never register Jama's hosted MCP server (Jama Connect MCP via `mcp-remote`) in place of the vendored one: it exposes write tools and no bulk reads
- Automation adds **no question** of its own: it is detected, planned, and shown in the Step 4 recap, and the one save confirmation covers it. Never install anything the recap didn't list, and pass `--shared` only for what it named
- bc's automation tools live only in `~/.buddy-council/tools`: never on PATH, never shared, and setup never edits a shell profile, `JAVA_HOME`, or `ANDROID_HOME`, and never replaces a working tool the Mac already has. Only `bc_tools.py` writes the `appium` MCP entry and the `automation` block
- What only a person can do — `sudo`, the App Store, signing, the phone — is passed on in the recap and the result lines, never attempted
