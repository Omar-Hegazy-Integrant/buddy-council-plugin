---
description: Internal (used by /bc:contradiction, /bc:coverage, /bc:ask, /bc:validate) — Fetch the in-flight issues on the configured Jira dev board by board id, normalized to the canonical schema. Reads jira.board from .buddy-council/sources.json.
user-invocable: false
---

# Fetch Board Issues — Router Skill

Fetch the issues currently on the team's Jira **dev board** — the in-flight work — so analysis can compare
what is being built against what the requirements and test cases say.

## This reads the board, not an approximation of it

`mcp-atlassian` exposes real agile tools — `jira_get_agile_boards`, `jira_get_board_issues`,
`jira_get_sprints_from_board`, `jira_get_sprint_issues` — so the board is addressed **by id** and the result
is the board's actual rows. That holds even for a board whose filter spans several projects or excludes
issue types, and it is what makes two boards in the *same* project distinguishable.

Versions before 0.21.1 reconstructed board scope from `project = <key>` JQL, because Atlassian's hosted
server had no board tools. Nothing here should reconstruct anything any more. If you find yourself building
a `project = …` query to answer "what is on the board", you are on the old path — use `jira_get_board_issues`
with `jira.board.id`.

## Input

- `scope`: Optional — a feature name, a set of requirement IDs, or "all" (default). Narrows the JQL applied
  *within* the board.
- `include_done`: Optional, default `false`. When true, drops the "not done" restriction.
- `board_id`: Optional — defaults to `jira.board.id`. `/bc:vnv-sprint-prep` passes `jira.vnv_board.id` to read
  the V&V board with the same code path.

## How It Works

1. Read `.buddy-council/sources.json` from the project root.
2. If there is no `jira` section, or `jira.pending` is `true`, or `jira.board` is missing → **do not fail the
   run**. Print `Fetch: board issues → skipped (Jira board not configured — run /bc:setup)` and return an
   empty array. The caller treats this as a configured-but-unavailable source, not a hard error.
3. Call `jira_get_board_issues` with the board id and the JQL below, paging with `start_at` until exhausted
   (`limit` caps at 50 per call).
4. Normalize each issue to the canonical schema with `type: "board_issue"`.
5. Print one line: `Fetch: board issues → <N> fetched from board <id>`.

No `cloudId` is involved anywhere — the server is bound to one site by its env file.

## The JQL passed to the board

`jira_get_board_issues` takes a **required** `jql` argument that filters *within* the board's own filter. It
narrows; it never widens. So the base query is deliberately permissive:

| Case | `jql` |
|---|---|
| Default | `statusCategory != Done ORDER BY updated DESC` |
| `include_done: true` | `ORDER BY updated DESC` |
| Active sprint only | `sprint in openSprints() AND statusCategory != Done ORDER BY updated DESC` |

Prefer the default. Reach for the sprint form only when the caller explicitly wants the active sprint — on a
Kanban board or a site without the `sprint` field it is rejected, and the correct response is to fall back to
the default form and say you did.

Narrow further when the caller passed a scope:

- **Feature name** → append `AND (component = "<feature>" OR labels = "<feature-slug>" OR text ~ "<feature>")`
- **Requirement IDs** → append `AND text ~ "<REQ-ID>"` per ID, OR-joined. Requirement IDs usually appear in
  the summary or description of the implementing ticket.

Do not add `project = <key>` — the board already constrains that, and adding it silently drops rows on a
multi-project board.

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

Field mapping, `issuelinks` → `linked_ids`, and feature derivation are all identical to
`${CLAUDE_PLUGIN_ROOT}/providers/jira/fetch.md` — follow that file rather than duplicating the rules here.
One addition specific to board issues: also scan `summary` and `description` for requirement-ID patterns
(`project.id_patterns` in the config, e.g. `CWA-REQ-\d+`) and merge any matches into `linked_ids`. That is
what lets coverage and contradiction analysis tie a sprint ticket back to a requirement when no formal Jira
link exists.

Request `fields` explicitly — `summary,status,issuetype,priority,labels,components,assignee,description` plus
the sprint field when you need it. The default field set is narrower than this skill's output.

## Sprints (for `/bc:validate` and `/bc:vnv-sprint-prep`)

Sprints are first-class here; do not scrape a sprint id off an arbitrary issue.

1. `jira_get_sprints_from_board` with `board_id` and `state: "active"` → the open sprint, with its numeric id.
   An empty result means a Kanban board or a scrum board between sprints; both are normal.
2. To place an issue in that sprint, create it first and then call `jira_add_issues_to_sprint` with
   `sprint_id` and `issue_keys`. That is a **write** tool and prompts for permission like any other.
3. `jira_get_sprint_issues` with `sprint_id` reads a sprint's contents directly when you want the sprint
   rather than the whole board.

If there is no active sprint, **create the ticket anyway without one** and tell the user it landed in the
backlog. Never fail ticket creation over sprint placement.

## Error Handling

- **No `jira` section / `jira.pending` true / no `jira.board`** → skip with the visible line above, return `[]`.
- **401** → the token in `~/.buddy-council/atlassian.env` is wrong or expired. Tell the user to re-run
  `/bc:setup`. Treat as a failed source.
- **403** → the account cannot browse this project or view this board. Say both; the response does not
  distinguish them.
- **404 on the board id** → the board was deleted or the id is wrong. Report the id you used and point at
  `/bc:setup`; do not silently fall back to a project-wide query, which would quietly change what "the board"
  means.
- **Malformed JQL** → report the exact server error and the query that produced it. Do not silently broaden
  the query; a wider result set would misrepresent the board.
- **Connection refused / timeout** → the Jira host is unreachable, usually a missing corporate VPN on a
  self-hosted site. Say that rather than reporting a generic network error.
- **Empty result with a working query** → acceptable. Report `0` and continue; an empty sprint is a real
  state, not an error.

Never fabricate board issues. If the fetch fails, report it and let the caller's Data Contract handling
decide whether to continue with partial data.
