# TestRail Test Cases Fetch — Provider Skill

Fetch test cases from TestRail using the `testrail` MCP server.

## Prerequisites

The `testrail` MCP server must be configured in `.mcp.json` and running. Check if the MCP tools are available by looking for tools prefixed with `mcp__testrail__` (e.g., `mcp__testrail__testrail_get_cases`).

If the MCP tools are NOT available:
- Tell the user to run `/bc:setup` to configure the MCP server
- Then restart Claude Code or toggle the server with `/mcp`
- Do NOT fall back to curl — the MCP server is the required data access method

## Input

- `project_id`: TestRail project ID (from `config/sources.json`)
- `suite_id`: Optional suite ID filter (from `config/sources.json`)
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
