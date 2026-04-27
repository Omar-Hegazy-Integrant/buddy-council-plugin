# Jira Issues Fetch — Provider Skill

Fetch Jira issues using the `jira` MCP server.

## Prerequisites

The `jira` MCP server must be configured in `.mcp.json` and running. Check if the MCP tools are available by looking for tools prefixed with `mcp__jira__` (e.g., `mcp__jira__jira_get_issue`).

If the MCP tools are NOT available:
- Tell the user to run `/bc:setup` to configure the Jira MCP server
- Then restart Claude Code
- Do NOT fall back to curl — the MCP server is the required data access method

## Input

- `project_key`: Jira project key (from `config/sources.json`)
- `scope`: Optional — a specific issue key (e.g., "PROJ-123"), JQL query, or "all"

## Fetching Strategy

**IMPORTANT: Always use the MCP tools below. Never use curl or Bash to call the Jira API directly.**

**Choose the appropriate fetch strategy based on scope:**

### Strategy 1: Single Issue (scope is an issue key like "PROJ-123")
- Call `mcp__jira__jira_get_issue` with `issue_key`
- Done — return a single issue

### Strategy 2: All Issues (scope is "all" or no specific issue key)
This provider currently does NOT support fetching all issues from a project. For large-scale issue fetching, consider:
1. Using JQL search (requires additional MCP tool implementation)
2. Exporting issues from Jira UI and using the Excel provider
3. Asking the user to provide specific issue keys

**Note:** The current Jira MCP server is optimized for ticket creation (write operations), not bulk reads. Future enhancements can add JQL search capabilities.

## Field Mapping

For each issue returned, extract and map fields:
- `key` → issue key (e.g., "PROJ-123")
- `fields.summary` → title
- `fields.description` → description (extract text from ADF format)
- `fields.issuetype.name` → issue type (Story, Task, Bug, etc.)
- `fields.status.name` → status
- `fields.priority.name` → priority
- `fields.labels` → tags/labels array
- `fields.issuelinks` → parse into `linked_ids` (see Linking section below)

## Output

Return a JSON array of issue objects in the canonical schema:

```json
[
  {
    "type": "requirement",
    "id": "PROJ-123",
    "title": "Add login button to checkout",
    "description": "As a user, I want a login button on the checkout page...",
    "feature": "Checkout",
    "status": "In Progress",
    "linked_ids": ["PROJ-456", "PROJ-789"],
    "raw_fields": {
      "issue_type": "Story",
      "priority": "High",
      "labels": ["authentication", "checkout"]
    }
  }
]
```

## Linking

The `fields.issuelinks` array contains links to other Jira issues. Parse this field to extract linked issue keys and store them in `linked_ids`. Each link object contains:
- `type.name` — link type (e.g., "Blocks", "Relates to", "Duplicates")
- `inwardIssue.key` OR `outwardIssue.key` — the linked issue key

Example link extraction:
```json
{
  "issuelinks": [
    {
      "type": {"name": "Relates"},
      "outwardIssue": {"key": "PROJ-456"}
    },
    {
      "type": {"name": "Blocks"},
      "inwardIssue": {"key": "PROJ-789"}
    }
  ]
}
```
Extract `["PROJ-456", "PROJ-789"]` into `linked_ids`.

## Feature Extraction

Since Jira issues don't have a native "feature" field, derive it from:
1. `fields.components[0].name` — use the first component as feature name
2. `fields.labels` — extract a feature-like label (e.g., "feature:checkout" → "Checkout")
3. `fields.customfield_*` — check for custom "Feature" or "Epic" fields
4. Fallback: Use project key as feature (e.g., "PROJ")

## Description Format Conversion

Jira Cloud uses Atlassian Document Format (ADF) for descriptions. Convert ADF to plain text:
- Extract all text nodes recursively from `content` array
- Preserve paragraph breaks
- Convert headings, lists, and code blocks to markdown where possible

If the description is in plain text format (older Jira or Jira Server), use it directly.

## Error Handling

- **Issue not found (404)**: Return empty array `[]`
- **Permission denied (403)**: Tell user to verify API token has "Browse Projects" and "View Issues" permissions
- **Invalid project key**: Prompt user to run `/bc:setup` and choose correct project

## Future Enhancements

To support bulk issue fetching, add these tools to the Jira MCP server:
- `jira_search_issues(jql, max_results, start_at)` — JQL search with pagination
- `jira_get_issues_by_project(project_key, max_results)` — fetch all issues in a project

For now, this provider focuses on single-issue reads and write operations (ticket creation via the validate agent).
