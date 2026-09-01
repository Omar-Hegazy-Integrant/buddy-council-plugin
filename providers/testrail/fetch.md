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

**Choose the narrowest fetch strategy based on available context:**

### Strategy 1: Single Test Case (scope is a test case ID like "TC-1234")
- Call `mcp__testrail__testrail_get_case` with `case_id` (strip the "TC-" prefix)
- Done — return a single test case

### Strategy 2: By Section (scope is a feature name, or feature_name was passed from requirements)
This is the **preferred strategy** when analyzing a specific feature — avoids fetching all cases.

1. Call `mcp__testrail__testrail_get_sections` with `project_id` and `suite_id`
2. Find the section(s) whose name matches the feature name (case-insensitive, partial match)
3. For each matching section, call `mcp__testrail__testrail_get_cases` with `project_id`, `suite_id`, AND `section_id`
4. Paginate within each section if needed (check `size == limit`)
5. Combine results from all matching sections

### Strategy 3: By Requirement References (requirement_ids were passed)
Use when you know which requirement IDs you care about and want to find their linked test cases.

1. Call `mcp__testrail__testrail_get_cases_by_refs` with `project_id`, `suite_id`, and `refs` (comma-separated requirement IDs)
2. This searches TestRail's built-in References field
3. If results are empty (the instance may use custom fields instead of refs), fall back to Strategy 2 or 4

### Strategy 4: All Cases (scope is "all" or no narrower context is available)
Use only when no feature or requirement context is available.

1. Call `mcp__testrail__testrail_get_cases` with `project_id`, `suite_id`, `offset=0`
2. Paginate: if `size == limit`, call again with `offset += limit`
3. Repeat until all cases are collected

## How to Choose

| Context Available | Strategy |
|-------------------|----------|
| Specific test case ID | Strategy 1 |
| Feature/section name known (from requirements or scope) | Strategy 2 |
| Specific requirement IDs to find linked tests for | Strategy 3, then fall back to 2 |
| "all" or no context | Strategy 4 |

## Section Lookup (required for all strategies except 1)

Before fetching cases (unless using Strategy 1), always fetch sections first:
- Call `mcp__testrail__testrail_get_sections` with `project_id` and `suite_id`
- Build a lookup: `{section_id: section_name}` for resolving the `feature` field

## Field Mapping

For each test case returned, extract and map fields:
- `id` → prefix with "TC-" (e.g., `id: 1234` becomes `"TC-1234"`)
- `title` → test case title
- `custom_desc` or `custom_preconds` → combine into description
- `custom_steps_separated` → structured test steps (array of `{content, expected}`)
- `custom_jama_req_id` → parse into `linked_ids` (see Linking section below)
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

The `custom_jama_req_id` field contains Jama requirement references. Parse this field to extract requirement IDs (e.g., "CWA-REQ-85") and store them in `linked_ids`. This field may contain:
- A single ID: "CWA-REQ-85"
- Multiple IDs: "CWA-REQ-85, CWA-REQ-86"
- IDs with prefixes or formatting variations — normalize to consistent format

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

`title` is the only required field. `refs` is the built-in References field — put requirement IDs there
(`"CWA-REQ-85,CWA-REQ-86"`) so `testrail_get_cases_by_refs` can find the case later. `type_id`,
`priority_id`, and `template_id` take **ids, not names**: resolve them with `testrail_get_case_types`,
`testrail_get_priorities`, and `testrail_get_templates`.

**Against an unfamiliar instance, call `testrail_get_case_fields` first.** A required custom field you did
not set is the usual cause of a 400, and the error names the field.

### Before writing anything

1. **Dry-run first** when creating more than a couple of cases: `dry_run: true` resolves folders and
   duplicate-checks without creating anything, so you can show the user the plan.
2. **Leave `skip_if_title_exists` on** (the default). It skips a case whose title already exists in the
   target folder, which makes a re-run safe. Turn it off only when the team genuinely wants duplicate
   titles.
3. **Report `failed` verbatim.** TestRail has no bulk-create endpoint, so the server loops `add_case`: a
   failed case does not stop the others. A silently dropped case is a test that never gets written.

The response shape is `{dry_run, created[], skipped[], failed[], sections_created[]}`, where each `created`
entry carries the new `id`, its `section_path`, and a browse `url`.
