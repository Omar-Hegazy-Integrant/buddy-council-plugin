---
description: Internal (used by /bc:contradiction, /bc:coverage, /bc:ask, /bc:validate) — Fetch the in-flight issues on the configured Jira dev board via JQL, normalized to the canonical schema. Reads jira.board from .buddy-council/sources.json.
user-invocable: false
---

# Fetch Board Issues — Router Skill

Fetch the issues currently on the team's Jira **dev board** — the in-flight work — so analysis can compare
what is being built against what the requirements and test cases say.

## Why this is JQL and not a board API

Atlassian's official MCP server exposes **no board, sprint, backlog, or epic tools**. Its entire Jira
surface is `getJiraIssue`, `getVisibleJiraProjects`, `getJiraProjectIssueTypesMetadata`,
`getJiraIssueTypeMetaWithFields`, `getJiraIssueRemoteIssueLinks`, `getIssueLinkTypes`,
`getTransitionsForJiraIssue`, `lookupJiraAccountId`, `searchJiraIssuesUsingJql`, and the write tools.
There is no `/rest/agile/1.0/board` equivalent.

A board is a saved filter plus sprint state, so its contents are reachable through
`searchJiraIssuesUsingJql`. This skill reconstructs the board's scope from `jira.project_key`. **Say so
when it matters**: for a standard single-project board the reconstruction is exact, but for a board whose
filter spans multiple projects or excludes issue types, the result is the project's in-flight work rather
than literally the board's rows. Never present a reconstructed scope as if it were read from the board.

## Input

- `scope`: Optional — a feature name, a set of requirement IDs, or "all" (default). Narrows the JQL.
- `include_done`: Optional, default `false`. When true, drops the "not done" restriction.

## How It Works

1. Read `.buddy-council/sources.json` from the project root.
2. If there is no `jira` section, or `jira.pending` is `true`, or `jira.board` is missing → **do not fail
   the run**. Print `Fetch: board issues → skipped (Jira board not configured — run /bc:setup)` and return
   an empty array. The caller treats this as a configured-but-unavailable source, not a hard error.
3. Resolve `cloudId` exactly as `providers/jira/fetch.md` describes (use `jira.cloud_id`; otherwise
   `getAccessibleAtlassianResources`, then cache it back).
4. Build the board-scope JQL (below) and call `searchJiraIssuesUsingJql`, paging until exhausted.
5. Normalize each issue to the canonical schema with `type: "board_issue"`.
6. Print one line: `Fetch: board issues → <N> fetched from board <id>`.

## Board-Scope JQL

Try these in order, stopping at the first that the server accepts:

1. **Scrum board — active sprint** (the common case):
   ```
   project = <project_key> AND sprint in openSprints() AND statusCategory != Done ORDER BY updated DESC
   ```
2. **Kanban, or team-managed without sprints.** If step 1 is rejected because the `sprint` field does not
   exist on this site, or returns zero issues while the project demonstrably has open work:
   ```
   project = <project_key> AND statusCategory != Done ORDER BY updated DESC
   ```
3. **`include_done: true`** drops the `statusCategory` clause from whichever form was used.

Narrow further when the caller passed a scope:

- **Feature name** → append `AND (component = "<feature>" OR labels = "<feature-slug>" OR text ~ "<feature>")`
- **Requirement IDs** → append `AND text ~ "<REQ-ID>"` per ID, OR-joined. Requirement IDs usually appear in
  the summary or description of the implementing ticket.

Report which form was used. If step 1 was rejected and you fell back, say so — the user should know the
result is project-scoped rather than sprint-scoped.

## Output

A JSON array in the canonical schema:

```json
[
  {
    "type": "board_issue",
    "id": "PROJ-123",
    "title": "Add login button to checkout",
    "description": "As a user, I want a login button on the checkout page...",
    "feature": "Checkout",
    "status": "In Progress",
    "linked_ids": ["CWA-REQ-85", "PROJ-456"],
    "raw_fields": {
      "issue_type": "Story",
      "priority": "High",
      "labels": ["authentication"],
      "sprint": "Sprint 14",
      "assignee": "Jane Doe",
      "board_id": 42
    }
  }
]
```

Field mapping, ADF→text conversion, `issuelinks` → `linked_ids`, and feature derivation are all identical
to `${CLAUDE_PLUGIN_ROOT}/providers/jira/fetch.md` — follow that file rather than duplicating the rules
here. One addition specific to board issues: also scan `summary` and `description` for requirement-ID
patterns (`project.id_patterns` in the config, e.g. `CWA-REQ-\d+`) and merge any matches into
`linked_ids`. That is what lets coverage and contradiction analysis tie a sprint ticket back to a
requirement when no formal Jira link exists.

## Resolving the Active Sprint (for `/bc:validate`)

`createJiraIssue` cannot set a sprint by name, and there is no sprint tool. To file a new ticket into the
board's active sprint:

1. Run the step-1 JQL above with a small `maxResults` and read the sprint field off any returned issue —
   that yields the numeric **sprint id** and the field id carrying it (commonly `customfield_10020`, but
   never hardcode it; take it from the issue payload).
2. Confirm the same field id is settable on create via `getJiraIssueTypeMetaWithFields`.
3. Pass it through `createJiraIssue`'s `additional_fields`, e.g. `{"customfield_10020": 42}`.

If any step fails — no open sprint, a Kanban board, the field not settable on create — **create the ticket
anyway without the sprint** and tell the user it landed in the backlog rather than the active sprint. Never
fail ticket creation over sprint placement.

## Error Handling

- **No `jira` section / `jira.pending` true / no `jira.board`** → skip with the visible line above, return `[]`.
- **401** → the Atlassian OAuth grant is missing or expired. Tell the user to re-authorize (`/mcp` →
  **atlassian** → Authenticate on Claude Code; restart Copilot CLI). Treat as a failed source.
- **403** → the account cannot browse this project, or the site admin has not enabled the Rovo MCP server.
  Name both possibilities; you cannot distinguish them from the response.
- **Malformed JQL** → report the exact server error and the query that produced it. Do not silently
  broaden the query; a wider result set would misrepresent the board.
- **Empty result with a working query** → acceptable. Report `0` and continue; an empty sprint is a real
  state, not an error.

Never fabricate board issues. If the fetch fails, report it and let the caller's Data Contract handling
decide whether to continue with partial data.
