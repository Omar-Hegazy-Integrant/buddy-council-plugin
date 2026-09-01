# Jira Issues Fetch — Provider Skill

Fetch Jira issues using the **`mcp-atlassian`** MCP server (`atlassian`).

## Prerequisites

The `atlassian` server is [`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian), launched with `uvx`
as a local stdio process and pinned to `mcp-atlassian@0.23.1`. It supports **Atlassian Cloud and Jira
Server/Data Center** alike, and `/bc:setup` writes its entry into both runtime configs:

- **Claude Code** reads the project `.mcp.json`.
- **Copilot CLI** reads `~/.copilot/mcp-config.json`.

Credentials live in `~/.buddy-council/atlassian.env` (`chmod 600`), passed to the server with `--env-file`.
The plugin never reads that file itself — it is the server's own configuration.

Check availability by looking for tools named `mcp__atlassian__jira_*` under Claude Code (e.g.
`mcp__atlassian__jira_get_issue`) or `jira_*` under Copilot CLI.

If the MCP tools are NOT available:

- Tell the user to restart the runtime — the server is registered in config but only loads at startup. The
  first launch also downloads the package through `uvx`, which takes a few seconds.
- If the server is present but every call fails, the cause is almost always in `atlassian.env`: a wrong or
  expired token, a `JIRA_USERNAME` set alongside a Server/DC PAT (which forces basic auth), or a `JIRA_URL`
  the machine cannot reach without a corporate VPN.
- Do NOT fall back to curl for data fetches — the MCP server is the required data access method. (`/bc:setup`
  uses curl once, deliberately, to test the credential before the server is registered.)

## No `cloudId`, ever

`mcp-atlassian` is bound to a single site by `JIRA_URL` in its env file, so **no tool takes a `cloudId`
argument**. Configs written before 0.21.1 may still carry `jira.cloud_id`; ignore it, and let `/bc:setup`
drop it on the next run.

## Input

- `project_key`: Jira project key (from `.buddy-council/sources.json` → `jira.project_key`)
- `scope`: Optional — a specific issue key (e.g., "PROJ-123"), a JQL query, or "all"

## Fetching Strategy

**IMPORTANT: Always use the MCP tools below. Never use curl or Bash to call the Jira API directly.**

### Strategy 1: Single issue (scope is an issue key like "PROJ-123")

Call `jira_get_issue` with `issue_key`. Return a single issue. Useful extras:

- `fields` — comma-separated, or `"*all"`. Default to a narrow list; `*all` on a large issue is expensive.
- `expand: "renderedFields"` when you need the description as HTML rather than raw markup.
- `comment_limit` — comments come back with the issue, so there is no separate comment fetch.

### Strategy 2: The configured dev board

When the caller wants a board's contents, do not reimplement the scoping here — follow
`${CLAUDE_PLUGIN_ROOT}/skills/fetch-board-issues/SKILL.md`, which owns board resolution, sprint selection,
and paging. This file owns the per-issue field mapping that skill reuses.

### Strategy 3: A set of issues (scope is "all", a feature name, or a JQL string)

Call `jira_search` with a `jql` string:

- **All issues in the project**: `project = PROJ ORDER BY created DESC`
- **Open work only**: `project = PROJ AND statusCategory != Done ORDER BY created DESC`
- **Scoped to a feature/component**: `project = PROJ AND component = "Checkout"`
- **Caller supplied raw JQL**: pass it through unchanged

`limit` caps at 50 per call. Page with `start_at`, or with `page_token` when the response carries one
(Cloud's newer enhanced-search endpoint returns tokens instead of offsets — use whichever the response
provides, and never mix the two in one traversal). Report how many issues were fetched so the Data Contract
line is accurate.

If a JQL query is rejected as malformed, report the exact server error and the query that produced it. Do not
silently retry with a broadened query — a wider result set would misrepresent the scope.

## Field Mapping

`mcp-atlassian` returns a simplified issue shape rather than raw Jira JSON, but the mapping targets are the
same. For each issue:

- `key` → issue key (e.g., "PROJ-123")
- `summary` → title
- `description` → description (already flattened to text — see below)
- `issue_type.name` → issue type (Story, Task, Bug, etc.)
- `status.name` → status
- `priority.name` → priority
- `labels` → tags/labels array
- `components` → component names
- `issuelinks` → parse into `linked_ids` (see Linking below)

Field names in the simplified payload are snake_case and may be flattened one level compared to raw Jira.
**Read defensively**: check the flattened key first, then the nested Jira path, and never assume a field is
present. Custom fields (`customfield_*`) come through under their raw ids unless the server resolved a name.

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

The `issuelinks` array contains links to other Jira issues. Parse it to extract linked issue keys into
`linked_ids`. Each link object contains:

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

Unlike Atlassian's hosted server, this one can **create** links too — `jira_create_issue_link` with
`link_type`, `inward_issue_key`, and `outward_issue_key`; `jira_get_link_types` lists what the site accepts.
That is a write tool and always prompts. Remote links (Confluence pages, external URLs) come back inside
`jira_get_issue` when requested, and `jira_create_remote_issue_link` creates them.

## Feature Extraction

Jira issues have no native "feature" field. Derive it, in order:

1. `components[0].name` — first component as feature name
2. `labels` — a feature-like label (e.g., "feature:checkout" → "Checkout")
3. `parent.fields.summary` — the parent Epic's summary
4. Fallback: the project key (e.g., "PROJ")

## Description Format

`mcp-atlassian` flattens descriptions for you: Cloud's Atlassian Document Format arrives as markdown-ish
text, and Server/DC wiki markup arrives as its raw string. Use `description` as given.

Only if a payload still carries a raw ADF object — nested `content` arrays instead of a string — walk it
yourself: extract text nodes recursively, preserve paragraph breaks, and convert headings, lists, and code
blocks to markdown. Server/DC wiki markup (`h2.`, `{code}`, `*bold*`) is readable as-is; do not try to
convert it.

## Error Handling

- **401** — the token in `~/.buddy-council/atlassian.env` is wrong or expired. On Cloud, also check that
  `JIRA_USERNAME` matches the account the API token belongs to. Tell the user to re-run `/bc:setup`.
- **403** — the account lacks "Browse Projects"/"View Issues" on that project, or an IP allowlist is blocking
  the request. Name both; you cannot distinguish them from the response.
- **404** on an issue — return an empty array `[]`.
- **Connection refused / timeout** — the Jira host is unreachable. For a self-hosted site this is usually a
  missing corporate VPN. Say so rather than reporting a generic network error.
- **TLS/certificate error** — a self-signed certificate on a Server/DC host. `JIRA_SSL_VERIFY=false` in the
  env file bypasses it; mention that it disables certificate checking.
- **Invalid project key** — list valid keys with `jira_get_all_projects` and prompt the user to re-run
  `/bc:setup`.

Never fabricate issues when a fetch fails. Report the failure and let the caller's Data Contract handling
decide whether to continue with partial data.
