# Jira Issues Fetch — Provider Skill

Fetch Jira issues using the **official Atlassian Remote MCP Server** (`atlassian`).

## Prerequisites

The `atlassian` MCP server is bundled with the plugin — it is Atlassian's hosted server at
`https://mcp.atlassian.com/v1/mcp/authv2`, not a vendored local process.

- **Claude Code** registers it automatically from `.claude-plugin/plugin.json` when the plugin is installed.
- **Copilot CLI** gets it from `~/.copilot/mcp-config.json`, which `/bc:setup` writes.

Check availability by looking for tools named `mcp__atlassian__*` under Claude Code (e.g.
`mcp__atlassian__getJiraIssue`) or `getJiraIssue` under Copilot CLI.

If the MCP tools are NOT available:

- Tell the user to authorize the server: `/mcp` → **atlassian** → Authenticate (Claude Code), or fully
  restart Copilot CLI after `/bc:setup`.
- If the server is present but every call returns 401/403, the user has not completed the browser OAuth
  consent, or a site admin has not enabled the Rovo MCP server for the site.
- Do NOT fall back to curl — the MCP server is the required data access method.

## Input

- `project_key`: Jira project key (from `.buddy-council/sources.json` → `jira.project_key`)
- `cloud_id`: Atlassian site cloud ID (from `.buddy-council/sources.json` → `jira.cloud_id`)
- `scope`: Optional — a specific issue key (e.g., "PROJ-123"), a JQL query, or "all"

## Resolving `cloudId` (required by every tool)

Every Atlassian MCP tool takes a `cloudId` identifying which Atlassian site to act on.

1. If `jira.cloud_id` is present in `.buddy-council/sources.json`, use it — no extra call.
2. Otherwise call `getAccessibleAtlassianResources`, which returns the sites the authorized user can reach:
   ```json
   [{ "id": "00000000-0000-0000-0000-000000000000", "url": "https://yourorg.atlassian.net", "name": "yourorg" }]
   ```
   Match on `jira.base_url` when set; if exactly one site is returned, use it. If several match and
   `base_url` does not disambiguate, ask the user which site to use.
3. Cache the resolved value back into `jira.cloud_id` so later runs skip the lookup.

## Fetching Strategy

**IMPORTANT: Always use the MCP tools below. Never use curl or Bash to call the Jira API directly.**

### Strategy 1: Single issue (scope is an issue key like "PROJ-123")

Call `getJiraIssue` with `cloudId` and `issueIdOrKey`. Return a single issue.

### Strategy 2: The configured dev board

When the caller wants the board's contents, do not reimplement the scoping here — follow
`${CLAUDE_PLUGIN_ROOT}/skills/fetch-board-issues/SKILL.md`, which owns the board-scope JQL, the
Scrum→Kanban fallback, and the active-sprint lookup. This file owns the per-issue field mapping that
skill reuses.

### Strategy 3: A set of issues (scope is "all", a feature name, or a JQL string)

Call `searchJiraIssuesUsingJql` with `cloudId` and a `jql` string:

- **All issues in the project**: `project = PROJ ORDER BY created DESC`
- **Open work only**: `project = PROJ AND statusCategory != Done ORDER BY created DESC`
- **Scoped to a feature/component**: `project = PROJ AND component = "Checkout"`
- **Caller supplied raw JQL**: pass it through unchanged

Use `maxResults` (the server caps it; 50 is a safe page size) and page with `nextPageToken` — or
`startAt` on older responses — until the result set is exhausted or the caller's scope is satisfied.
Report how many issues were fetched so the Data Contract line is accurate.

If a JQL query is rejected as malformed, report the exact server error and the query that produced it.
Do not silently retry with a broadened query — a wider result set would misrepresent the scope.

## Field Mapping

For each issue returned, extract and map fields:

- `key` → issue key (e.g., "PROJ-123")
- `fields.summary` → title
- `fields.description` → description (extract text from ADF format — see below)
- `fields.issuetype.name` → issue type (Story, Task, Bug, etc.)
- `fields.status.name` → status
- `fields.priority.name` → priority
- `fields.labels` → tags/labels array
- `fields.issuelinks` → parse into `linked_ids` (see Linking below)

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

The `fields.issuelinks` array contains links to other Jira issues. Parse it to extract linked issue keys
into `linked_ids`. Each link object contains:

- `type.name` — link type (e.g., "Blocks", "Relates to", "Duplicates")
- `inwardIssue.key` OR `outwardIssue.key` — the linked issue key

Example link extraction:

```json
{
  "issuelinks": [
    { "type": { "name": "Relates" }, "outwardIssue": { "key": "PROJ-456" } },
    { "type": { "name": "Blocks" }, "inwardIssue": { "key": "PROJ-789" } }
  ]
}
```

Extract `["PROJ-456", "PROJ-789"]` into `linked_ids`.

`getJiraIssueRemoteIssueLinks` returns links to systems outside Jira (Confluence pages, external URLs).
Fetch it only when the caller specifically needs external traceability — it costs an extra call per issue.

## Feature Extraction

Jira issues have no native "feature" field. Derive it, in order:

1. `fields.components[0].name` — first component as feature name
2. `fields.labels` — a feature-like label (e.g., "feature:checkout" → "Checkout")
3. `fields.parent.fields.summary` — the parent Epic's summary
4. Fallback: the project key (e.g., "PROJ")

## Description Format Conversion

Jira Cloud stores descriptions in Atlassian Document Format (ADF). Convert ADF to plain text:

- Extract all text nodes recursively from the `content` array
- Preserve paragraph breaks
- Convert headings, lists, and code blocks to markdown where possible

If the description is already a plain string (Jira Server / older payloads), use it directly.

## Error Handling

- **Not authorized (401)**: the OAuth grant is missing or expired — tell the user to re-authorize via `/mcp`
  (Claude Code) or to restart Copilot CLI and complete the browser consent.
- **Permission denied (403)**: the account lacks "Browse Projects"/"View Issues" on that project, or the
  site admin has not enabled the Rovo MCP server. Name which of the two you cannot distinguish.
- **Issue not found (404)**: return an empty array `[]`.
- **Unknown `cloudId`**: re-resolve with `getAccessibleAtlassianResources` and update `jira.cloud_id`.
- **Invalid project key**: list valid keys with `getVisibleJiraProjects` and prompt the user to re-run `/bc:setup`.

Never fabricate issues when a fetch fails. Report the failure and let the caller's Data Contract handling
decide whether to continue with partial data.
