# Normalize Artifacts — Skill

Normalize fetched requirements and test cases into a consistent canonical schema, then link them together by cross-referencing IDs.

## When to Use

After fetching raw data from providers, before passing to analysis skills.

## Canonical Schema

Every artifact must conform to this shape:

```json
{
  "type": "requirement" | "test_case",
  "id": "string",
  "title": "string",
  "description": "string",
  "feature": "string",
  "status": "string",
  "linked_ids": ["string"],
  "raw_fields": {}
}
```

## Normalization Steps

### 1. Clean Text Fields

For `title`, `description`, and any text in `raw_fields`:
- Strip HTML tags (replace `<br>`, `<p>`, `<li>` with newlines first, then remove remaining tags)
- Normalize whitespace (collapse multiple spaces/newlines)
- Trim leading/trailing whitespace
- Replace `nan`, `None`, `null` string literals with empty string

### 2. Normalize IDs

- Requirement IDs: ensure consistent format like `CWA-REQ-85` (uppercase prefix, hyphenated, numeric suffix)
- Test case IDs: ensure consistent `TC-{number}` format
- Strip any whitespace or extra characters from IDs

### 3. Parse Linked IDs

For test cases, parse the `custom_jama_req_id` or equivalent field:
- Split on commas, semicolons, or newlines
- Trim each ID
- Normalize to the standard requirement ID format
- Store in `linked_ids`

For requirements from Excel, `linked_ids` will initially be empty.

### 4. Cross-Link (Bidirectional)

After both requirements and test cases are normalized:
- For each test case that has requirement IDs in `linked_ids`:
  - Find the matching requirement
  - Add the test case ID to that requirement's `linked_ids`
- This produces bidirectional links: requirements know their test cases, test cases know their requirements

### 5. Feature Normalization

Ensure feature names are consistent between requirements and test cases:
- Trim and normalize casing
- Map TestRail section names to Jama folder names where possible (fuzzy match on feature name)

## Output

Return two arrays:
1. `requirements` — normalized requirement objects with populated `linked_ids`
2. `test_cases` — normalized test case objects with populated `linked_ids`

Both arrays contain objects in the canonical schema.
