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
- `scope`: Optional — a specific test case ID, section name, or "all"

## How to Fetch

**IMPORTANT: Always use the MCP tools below. Never use curl or Bash to call the TestRail API directly.**

1. Read `config/sources.json` for `project_id` and `suite_id`

2. **Fetch sections** to build a section ID → name map:
   - Call the MCP tool `mcp__testrail__testrail_get_sections` with `project_id` and `suite_id`
   - Build a lookup: `{section_id: section_name}` for resolving the `feature` field

3. **Fetch test cases** with pagination:
   - Call the MCP tool `mcp__testrail__testrail_get_cases` with `project_id`, `suite_id`, `offset=0`
   - Parse the response — it returns `{"cases": [...], "size": N, "offset": N, "limit": N}`
   - If `size == limit`, call again with `offset += limit`
   - Repeat until all cases are collected
   - Combine all pages into a single list

4. **If scope is a specific test case ID**: Call `mcp__testrail__testrail_get_case` with `case_id` (strip the "TC-" prefix if present)

5. For each test case, extract and map fields:
   - `id` → prefix with "TC-" (e.g., `id: 1234` becomes `"TC-1234"`)
   - `title` → test case title
   - `custom_desc` or `custom_preconds` → combine into description
   - `custom_steps_separated` → structured test steps (array of `{content, expected}`)
   - `custom_jama_req_id` → parse into `linked_ids` (see Linking section below)
   - `section_id` → resolve to section name using the lookup from step 2

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

## Field Mapping

| TestRail Field | Canonical Field |
|----------------|----------------|
| `id` | `id` (prefixed with "TC-") |
| `title` | `title` |
| `custom_desc` + `custom_preconds` | `description` |
| `section_id` → section name via lookup | `feature` |
| `custom_jama_req_id` | parsed into `linked_ids` |
| `custom_steps_separated` | `raw_fields.steps` |

## Pagination

- `mcp__testrail__testrail_get_cases` returns max 250 cases per call
- Check if `size == limit` in the response — if true, there are more pages
- Call again with `offset += limit` until all cases are collected

## Linking

The `custom_jama_req_id` field contains Jama requirement references. Parse this field to extract requirement IDs (e.g., "CWA-REQ-85") and store them in `linked_ids`. This field may contain:
- A single ID: "CWA-REQ-85"
- Multiple IDs: "CWA-REQ-85, CWA-REQ-86"
- IDs with prefixes or formatting variations — normalize to consistent format
