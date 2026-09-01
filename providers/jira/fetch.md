# Jira Issues Fetch — Provider Skill

Fetch Jira issues using the **`sooperset/mcp-atlassian` server** (`atlassian`), launched with `uvx`.

## Prerequisites

The `atlassian` MCP server runs as a `uvx` subprocess (`mcp-atlassian@0.23.1`). It is not vendored and not
declared in the plugin manifest — `/bc:setup` registers it in both runtimes' MCP configs, pointing at
`~/.buddy-council/atlassian.env` for credentials.

Check availability by looking for tools named `mcp__atlassian__*` under Claude Code (e.g.
`mcp__atlassian__jira_get_issue`) or `jira_get_issue` under Copilot CLI.

If the MCP tools are NOT available, the cause is almost always one of these, in order of likelihood:

- **`command` is not an absolute `uvx` path.** The spawned server does not inherit the shell's `PATH`, so a
  bare `"uvx"` fails to start and the server never registers.
- **Setup has not been run**, or was run before 0.20.0 — the config may still hold the retired
  `mcp.atlassian.com` HTTP entry. Tell them to run `/bc:setup`.
- **The CLI was not restarted** after setup.

There is **no authorization step** — the server uses the API token in the env file, not browser OAuth. If a
user asks where to authenticate, tell them there is nothing to approve.

Do NOT fall back to curl — the MCP server is the required data access method. (`/bc:setup` uses curl once,
to verify credentials before the server is registered; that is the sole exception and it does not apply here.)

## Input

- `project_key`: Jira project key (from `.buddy-council/sources.json` → `jira.project_key`)
- `scope`: Optional — a specific issue key (e.g., "PROJ-123"), a JQL query, or "all"

**There is no `cloudId` to resolve.** The server is bound to one site by `JIRA_URL` in its env file, so no
tool takes a site identifier. If you find a `cloud_id` in `sources.json`, it is a leftover from a pre-0.20.0
config — ignore it.

## Fetching Strategy

**IMPORTANT: Always use the MCP tools below. Never use curl or Bash to call the Jira API directly.**

### Strategy 1: Single issue (scope is an issue key like "PROJ-123")

Call `jira_get_issue` with `issue_key`. Useful parameters:

- `fields` — comma-separated; defaults to a set of essentials. Pass `"*all"` when you need custom fields.
- `comment_limit` — number of comments to include (default 10, max 100, `0` to suppress).
- `include` — comma-separated extras: `comments`, `remote_links`, `transitions`, `changelog`, `watchers`.
- `use_display_names` — return human-readable custom field names instead of `customfield_NNNNN` keys.

### Strategy 2: The configured dev board

When the caller wants the board's contents, do not reimplement the scoping here — follow
`${CLAUDE_PLUGIN_ROOT}/skills/fetch-board-issues/SKILL.md`, which owns the board and sprint calls. This file
owns the per-issue field mapping that skill reuses.

### Strategy 3: A set of issues (scope is "all", a feature name, or a JQL string)

Call `jira_search` with a `jql` string:

- **All issues in the project**: `project = PROJ ORDER BY created DESC`
- **Open work only**: `project = PROJ AND statusCategory != Done ORDER BY created DESC`
- **Scoped to a feature/component**: `project = PROJ AND component = "Checkout"`
- **Caller supplied raw JQL**: pass it through unchanged

`limit` defaults to **10** — low enough that accepting the default will silently truncate almost any real
result set. Always set it explicitly (50 is a safe page size) and page with `start_at`, or `page_token` on
Cloud, until the set is exhausted or the caller's scope is satisfied. Report how many issues were fetched so
the Data Contract line is accurate.

If a JQL query is rejected as malformed, report the exact server error and the query that produced it.
Do not silently retry with a broadened query — a wider result set would misrepresent the scope.

## Field Mapping

For each issue returned, extract and map fields:

- `key` → issue key (e.g., "PROJ-123")
- `fields.summary` → title
- `fields.description` → description (see format note below)
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

**Remote links** (Confluence pages, external URLs) are not a separate tool. Request them inline with
`jira_get_issue` using `include: "remote_links"`, which adds a `remote_links` key to the response. Do this
only when the caller specifically needs external traceability — it costs an extra API round trip per issue.

To create a link rather than read one, use `jira_create_issue_link` (`link_type`, `inward_issue_key`,
`outward_issue_key`); `jira_get_link_types` lists the valid `link_type` names for the site. Both live in the
`jira_links` toolset, which the env file must opt into.

## Feature Extraction

Jira issues have no native "feature" field. Derive it, in order:

1. `fields.components[0].name` — first component as feature name
2. `fields.labels` — a feature-like label (e.g., "feature:checkout" → "Checkout")
3. `fields.parent.fields.summary` — the parent Epic's summary
4. Fallback: the project key (e.g., "PROJ")

## Description Format

**Markdown in both directions, on both deployments.** The server normalizes the storage format for you, so
you neither parse nor build the native one:

- **Cloud** stores Atlassian Document Format (ADF) — a JSON tree.
- **Server / Data Center** stores **wiki markup** (`h1.`, `*bold*`, `{code}`), not ADF. ADF does not exist there.

Reads arrive as a Markdown string either way. Writes (`jira_create_issue`, `jira_update_issue`,
`jira_add_comment`) take Markdown and are converted to whichever format the deployment uses. So never
hand-build ADF, and never hand-write wiki markup — both would be double-encoded on the deployment that
doesn't use them.

If a raw ADF object does come back (older payloads, unusual field configurations), extract text nodes
recursively from the `content` array, preserving paragraph breaks.

## Error Handling

- **401** — bad or revoked credential. Tell the user to re-run `/bc:setup`, or to check
  `~/.buddy-council/atlassian.env` directly. Two common causes: quoting a value in that file (the quotes
  become part of the token), and a mismatched auth shape — `JIRA_USERNAME`+`JIRA_API_TOKEN` is Cloud only,
  `JIRA_PERSONAL_TOKEN` is Server/Data Center only. Carrying the wrong pair across a Cloud migration looks
  exactly like a bad token.
- **403** — the account lacks "Browse Projects"/"View Issues" on that project. This is a Jira permission
  problem, not a configuration one.
- **404** — issue not found; return an empty array `[]`.
- **Tool missing entirely** — the tool's toolset is not enabled. Board tools need `jira_agile`, link tools
  need `jira_links`, project/field metadata needs `jira_projects`, user lookup needs `jira_users`. Missing
  toolsets fail *silently* — the tool simply does not exist rather than erroring — so if a tool you expect is
  absent, check `TOOLSETS` in `~/.buddy-council/atlassian.env` before assuming anything else is wrong.
- **Server fails to start** — `command` is not an absolute `uvx` path, or the `--env-file` path is wrong or non-absolute.
- **Invalid project key** — list valid keys with `jira_get_all_projects` and prompt the user to re-run `/bc:setup`.

Never fabricate issues when a fetch fails. Report the failure and let the caller's Data Contract handling
decide whether to continue with partial data.
