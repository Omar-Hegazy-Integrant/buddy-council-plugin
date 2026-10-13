# TestRail Test Cases Fetch — Provider Skill

Fetch test cases from TestRail using the `testrail` MCP server.

> **This file covers reading.** The same server can also *create* cases — see
> [Creating test cases](#creating-test-cases-write) at the bottom. Those tools write to the team's live
> TestRail and always prompt.

## Prerequisites

The `testrail` MCP server must be configured in `.mcp.json` and running. Check if the MCP tools are available by looking for tools prefixed with `mcp__testrail__` (e.g., `mcp__testrail__testrail_get_cases`).

If the MCP tools are NOT available:
- Tell the user to run `/bc:setup` to configure the MCP server
- Then restart Claude Code or toggle the server with `/mcp`
- Do NOT fall back to curl — the MCP server is the required data access method

## Input

- `project_id`: TestRail project ID (from `.buddy-council/sources.json`)
- `suite_id`: Optional suite ID filter (from `.buddy-council/sources.json`)
- `scope`: Optional — a specific test case ID, feature/section name, requirement IDs, or "all"
- `feature_name`: Optional — the feature name from the requirements (used to narrow the fetch)
- `requirement_ids`: Optional — list of requirement IDs to find linked test cases for

## Fetching Strategy

**IMPORTANT: Always use the MCP tools below. Never use curl or Bash to call the TestRail API directly.**

**Read cases with `mcp__testrail__testrail_get_canonical_cases`.** It returns them already in the canonical
schema below — `TC-` ids, `linked_ids` parsed from the requirement field, steps, References, folder path — at
a fraction of the raw size, so there is nothing to map. One call covers each strategy through its filters:
`requirement_ids` (Strategy 3), `section` (Strategy 2; given with `requirement_ids`, a case matching either is
returned), `unlinked: true` (orphans), or no filter (Strategy 4). Always pass `requirement_field`. The raw
tools (`testrail_get_cases`, `testrail_get_case`, `testrail_get_linked_cases`) remain for the raw fields a
write path needs. `save_to` writes the result into a run folder instead of returning it — that is how
`skills/collect-artifacts` collects; don't use it elsewhere.

**Choose the narrowest fetch strategy based on available context:**

### Strategy 1: Single Test Case (scope is a test case ID like "TC-1234")
- Call `mcp__testrail__testrail_get_case` with `case_id` (strip the "TC-" prefix)
- Done — return a single test case

### Strategy 2: By Section (scope is a feature name, or feature_name was passed from requirements)
Use for a feature whose requirement IDs you do not have, and alongside Strategy 3 to catch the **unlinked**
cases filed in a folder named after the feature — the cases Strategy 3 cannot see.

1. Call `mcp__testrail__testrail_get_canonical_cases` with `project_id`, `suite_id`, `requirement_field`, and
   `section` = the feature name. A bare name matches every folder whose name contains it as whole words,
   case-insensitively and at any depth (`Alarms` finds `Alarms - iOS`), subfolders included; a path such as
   `"Alarms/Battery"` matches that folder. When the folders are
   named differently, list them with `mcp__testrail__testrail_get_sections` and pass the closest `path`.
2. Page: while the response carries `next_offset`, call again with `offset: <next_offset>` and concatenate
   `test_cases`.

### Strategy 3: By Requirement Links (requirement_ids were passed)
The **preferred strategy** whenever the requirement IDs in scope are known — a feature's requirements, just
fetched, or a single requirement. It finds cases by the link this plugin actually reads, so it works whatever
the TestRail folders are called, and it is exact.

1. Call `mcp__testrail__testrail_get_canonical_cases` with `project_id`, `suite_id`, `requirement_field`, and
   `requirement_ids` — plus `section` = the feature name when it is known, so the unlinked cases filed in the
   feature's folder come back in the same call (a case with no requirement ID cannot be found by its links).
   The field is `test_cases.authoring.requirement_field`, else `custom_jama_req_id` — the same field the
   *Linking* section below reads.
2. Page: while the response carries `next_offset`, call again with `offset: <next_offset>` and concatenate
   `test_cases`. The suite is read once and cached for 10 minutes, so further calls in the run are cheap.

`unlinked_ids` lists the requested requirements no case links to — the untested ones, straight from TestRail.

`mcp__testrail__testrail_get_cases_by_refs` searches the built-in References field instead. That field often
holds other things — Jira keys, legacy IDs — so use it for what it is good at, like finding the cases that
reference a Jira story key, not for requirement coverage.

### Orphans across the whole suite (scope is "all")
Strategies 2 and 3 only see cases near a feature. For an `"all"` run, call
`mcp__testrail__testrail_get_canonical_cases` once with `unlinked: true` to list every case whose requirement
field holds no ID — the suite's orphans, whatever folder they sit in.

### Strategy 4: All Cases (scope is "all" or no narrower context is available)
Use only when no feature or requirement context is available: call
`mcp__testrail__testrail_get_canonical_cases` with no filter and follow `next_offset` until it is null.

## How to Choose

| Context Available | Strategy |
|-------------------|----------|
| Specific test case ID | Strategy 1 |
| Requirement IDs known (a feature's requirements, or one requirement) | Strategy 3, plus Strategy 2 when the feature name is known too |
| Only a feature/section name known | Strategy 2 |
| "all" or no context | Strategy 4 — or, feature by feature, Strategy 3 — plus the orphan listing |

## Section Lookup (raw tools only)

`testrail_get_canonical_cases` resolves folders itself (`feature` is the folder name,
`raw_fields.section_path` the full path). When you read raw cases instead, fetch the sections first with
`mcp__testrail__testrail_get_sections` and build `{section_id: section_name}` to resolve the `feature` field.

## Field Mapping

`testrail_get_canonical_cases` returns exactly this mapping. When you read raw cases, map each one:
- `id` → prefix with "TC-" (e.g., `id: 1234` becomes `"TC-1234"`)
- `title` → test case title
- `custom_desc` or `custom_preconds` → combine into description
- `custom_steps_separated` → structured test steps (array of `{content, expected}`)
- the requirement field → parse into `linked_ids` (see Linking section below)
- `refs` → `raw_fields.refs`, verbatim. Coverage needs it to tell a case whose requirement link is stranded in
  References from one with no link at all — see the orphan repair in `skills/update-test-cases/SKILL.md`
- `section_id` → resolve to section name using the section lookup

## Output

Return a JSON array of test case objects in the canonical schema:

```json
[
  {
    "type": "test_case",
    "id": "TC-1234",
    "title": "Verify patient vitals display updates in real-time",
    "description": "Preconditions: Patient monitor connected...",
    "feature": "Section Name",
    "status": "Active",
    "linked_ids": ["CWA-REQ-85", "CWA-REQ-86"],
    "raw_fields": {
      "steps": [
        {"content": "Open patient monitoring view", "expected": "Vitals dashboard loads"},
        {"content": "Connect monitor device", "expected": "Real-time data appears within 3s"}
      ],
      "refs": "CWA-REQ-85,CWA-REQ-86",
      "section_id": 5,
      "suite_id": 1
    }
  }
]
```

## Pagination

- `mcp__testrail__testrail_get_cases` returns max 250 cases per call
- Check if `size == limit` in the response — if true, there are more pages
- Call again with `offset += limit` until all cases are collected

## Linking

The requirement field is `test_cases.authoring.requirement_field` from `.buddy-council/sources.json` when it
is set, and `custom_jama_req_id` otherwise (configs from before `authoring` existed, or a `null` field). Read
the **same** field the authoring path writes — `/bc:setup` resolves it per instance, and reading a different
one makes every created case come back an orphan. Parse it to extract requirement IDs (e.g., "CWA-REQ-85")
and store them in `linked_ids`. This field may contain:
- A single ID: "CWA-REQ-85"
- Multiple IDs: "CWA-REQ-85, CWA-REQ-86"
- IDs with prefixes or formatting variations — normalize to consistent format
- Rich text — on some instances the field holds markup such as `<p>CWA-REQ-85,&nbsp;CWA-REQ-86</p>`. The
  server's link index, `testrail_get_canonical_cases`, and the update guards drop the markup before splitting;
  when you parse a raw case yourself, strip it too, or every such link reads as a different ID

## Creating test cases (write)

`mcp__testrail__testrail_add_cases` creates one or more cases; `testrail_add_case` is the single-case
wrapper. **These write to the team's live TestRail and always prompt** — they are excluded from the
auto-approve hook on purpose. Never call them to "check" something; use the read tools for that.

### Choosing the folder

Every case must land in a section. Pass one of these, at batch level or per case:

- `section_id` — a numeric id, validated against the suite before anything is written. A wrong id would
  quietly file cases into another team's folder, so it is checked rather than trusted.
- `section_path` — `"Login/Negative cases"`, matched case-insensitively against the section tree.
  `create_missing_sections` (default true) creates missing folders in the chain and reports them under
  `sections_created`. Set it to `false` when the team's suite structure is fixed and a missing folder should
  be an error instead.

Entries inside `cases` may override the batch target with their own `section_id`/`section_path`, so one call
can write into several folders. Read `mcp__testrail__testrail_get_sections` first when you need to show the
user where cases will go — it returns a derived `path` on every section, which is exactly what
`section_path` accepts.

### Building a case

Use the readable aliases; the server maps them onto TestRail's field names:

| You pass | TestRail field |
|---|---|
| `preconditions` | `custom_preconds` |
| `steps` | `custom_steps` |
| `expected` | `custom_expected` |
| `steps_separated` | `custom_steps_separated` — `[{"content": ..., "expected": ...}]`, needs a steps template |
| `custom_fields` | merged as-is; a missing `custom_` prefix is added |

`title` is the only required field.

**Requirement IDs go in two fields, always.** `refs` is the built-in References field, which
`testrail_get_cases_by_refs` searches (Strategy 3 above). But this plugin builds a case's `linked_ids` from
the requirement field — the *Linking* section above — so a case written with `refs` alone comes back with no
`linked_ids` and is counted an **orphan**, while the requirement it covers still reads **untested**. Write
both: `refs` gets the requirement IDs plus any originating Jira key,
`test_cases.authoring.requirement_field` (default `custom_jama_req_id`) gets the requirement IDs only, in
the same delimiter format the existing cases use. `type_id`,
`priority_id`, and `template_id` take **ids, not names**: resolve them with `testrail_get_case_types`,
`testrail_get_priorities`, and `testrail_get_templates`.

**Against an unfamiliar instance, call `testrail_get_case_fields` first.** A required custom field you did
not set is the usual cause of a 400, and the error names the field.

### Before writing anything

1. **Dry-run first** when creating more than a couple of cases: `dry_run: true` resolves folders and
   duplicate-checks without creating anything, so you can show the user the plan. Folders it would create
   are listed as `would create folder "…"` under `sections_created`, with `section_id: null` on their cases.
2. **Leave `skip_if_title_exists` on** (the default). It skips a case whose title already exists in the
   target folder, which makes a re-run safe. Turn it off only when the team genuinely wants duplicate
   titles.
3. **Report `failed` verbatim.** TestRail has no bulk-create endpoint, so the server loops `add_case`: a
   failed case does not stop the others. A silently dropped case is a test that never gets written.

The response shape is `{dry_run, created[], skipped[], failed[], sections_created[]}`, where each `created`
entry carries the new `id`, its `section_path`, and a browse `url`.

## Updating test cases (write)

`mcp__testrail__testrail_update_cases` changes existing cases, each with its own fields;
`testrail_update_case` is the single-case wrapper. **These write to the team's live TestRail and always
prompt.** Call them only through `skills/update-test-cases/SKILL.md` — the plugin's one update path — never
directly from an agent.

Each entry is `case_id` (`1234`, `"C1234"`, or `"TC-1234"`) plus the fields to change, with the same keys as
creation (`title`, `type_id`, `priority_id`, `estimate`, `milestone_id`, `refs`, `preconditions`, `steps`,
`expected`, `steps_separated`, `custom_fields`). Omit a field to leave it alone; pass `""` to clear it.
`steps_separated` replaces the whole step list.

The server enforces the safety rules itself, so a careless caller cannot get around them:

| Guard | What it does |
|---|---|
| **Diff first** | Reads each case and sends only the fields that actually differ; a case with nothing to change is not written at all |
| **Link protection** | Blocks an update that would remove an ID from `refs` — or from `requirement_field`, which every call must pass — unless that entry sets `allow_unlink` |
| **Conflict check** | With `expected_updated_on` (the `updated_on` from the dry run), blocks a case someone edited since it was read |
| **Read-back** | Compares every sent field with the case TestRail returns and lists any it silently dropped in `dropped_fields`; by default the rest of the batch is then not attempted |
| **No moves** | Refuses `template_id` and `section_id` — switching a template or folder is done in TestRail |

`dry_run: true` reads and diffs without writing, and returns each case's current `updated_on` to pass back
when applying. The response is `{dry_run, updated[], unchanged[], blocked[], failed[], not_attempted[],
stopped}`; every `updated` entry carries `changes` with each field's `before` and `after`, so the previous
values are always at hand to undo. Surface `blocked`, `failed`, `dropped_fields`, and `not_attempted` to the
user every time.
