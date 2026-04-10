# TestRail Test Cases Fetch — Provider Skill

Fetch test cases from TestRail via its REST API.

## Input

- `base_url`: TestRail instance URL (from `config/sources.json`)
- `project_id`: TestRail project ID (from `config/sources.json`)
- `suite_id`: Optional suite ID filter (from `config/sources.json`)
- `scope`: Optional — a specific test case ID, section name, or "all"

## Credentials

Read from `~/.buddy-council-secrets.json`:
```json
{
  "testrail": {
    "username": "user@company.com",
    "api_key": "xxx"
  }
}
```

## How to Fetch

1. Read `config/sources.json` for connection details
2. Read `~/.buddy-council-secrets.json` for credentials
3. Fetch test cases using the TestRail API with pagination:

```bash
# Fetch test cases (paginated — TestRail returns max 250 per request)
OFFSET=0
while true; do
  RESPONSE=$(curl -s -u "USERNAME:API_KEY" \
    "BASE_URL/index.php?/api/v2/get_cases/PROJECT_ID&suite_id=SUITE_ID&limit=250&offset=$OFFSET")

  # Extract cases from response
  # TestRail v2 API returns: {"offset":0,"limit":250,"size":N,"_links":{},"cases":[...]}
  # Check if "cases" array is empty or size < limit to know when to stop

  OFFSET=$((OFFSET + 250))
  # Break when no more results
done
```

4. For each test case, extract relevant fields:
   - `id` → TestRail case ID (prefixed as "TC-{id}")
   - `title` → test case title
   - `custom_desc` or `custom_preconds` → description/preconditions
   - `custom_steps_separated` → structured test steps (array of {content, expected})
   - `custom_jama_req_id` → linked Jama requirement ID (critical for linking)
   - `section_id` → section for grouping
   - `suite_id` → suite reference

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
| `section_id` → section name via API | `feature` |
| `custom_jama_req_id` | parsed into `linked_ids` |
| `custom_steps_separated` | `raw_fields.steps` |

## Pagination Notes

- TestRail limits responses to 250 items
- Use `offset` parameter to page through results
- Stop when returned `size` < `limit` or `cases` array is empty
- For large projects, consider fetching by section to reduce payload size

## Linking

The `custom_jama_req_id` field contains Jama requirement references. Parse this field to extract requirement IDs (e.g., "CWA-REQ-85") and store them in `linked_ids`. This field may contain:
- A single ID: "CWA-REQ-85"
- Multiple IDs: "CWA-REQ-85, CWA-REQ-86"
- IDs with prefixes or formatting variations — normalize to consistent format
