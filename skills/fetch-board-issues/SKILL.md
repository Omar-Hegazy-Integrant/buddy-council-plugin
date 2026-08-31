---
description: Internal (used by /bc:contradiction, /bc:coverage, /bc:ask, /bc:validate) — Fetch the in-flight issues on the configured Jira dev board via the Agile board API, normalized to the canonical schema. Reads jira.board from .buddy-council/sources.json.
user-invocable: false
---

# Fetch Board Issues — Router Skill

Fetch the issues currently on the team's Jira **dev board** — the in-flight work — so analysis can compare
what is being built against what the requirements and test cases say.

## This reads the board itself

The Dockerized `sooperset/mcp-atlassian` server exposes the real Agile API: `jira_get_agile_boards`,
`jira_get_board_issues`, `jira_get_sprints_from_board`, `jira_get_sprint_issues` and
`jira_add_issues_to_sprint`. The board's contents come from the board, by id.

Earlier versions reconstructed board scope from a project-wide JQL guess and had to warn that the result was
approximate for boards whose filter spanned projects or excluded issue types. **That caveat is gone** — do
not repeat it, and do not fall back to project-wide JQL when a board id is configured.

These tools live in the **`jira_agile` toolset, which is not enabled by default**. If they are missing
entirely, `TOOLSETS` in `~/.buddy-council/atlassian.env` is missing `jira_agile` — say that explicitly
rather than reporting a generic failure, because the tools vanish silently rather than erroring.

## Input

- `scope`: Optional — a feature name, a set of requirement IDs, or "all" (default). Narrows the JQL filter.
- `include_done`: Optional, default `false`. When true, drops the "not done" restriction.

## How It Works

1. Read `.buddy-council/sources.json` from the project root.
2. If there is no `jira` section, or `jira.pending` is `true`, or `jira.board` is missing → **do not fail
   the run**. Print `Fetch: board issues → skipped (Jira board not configured — run /bc:setup)` and return
   an empty array. The caller treats this as a configured-but-unavailable source, not a hard error.
3. Fetch the board's issues with `jira_get_board_issues` (below).
4. Normalize each issue to the canonical schema with `type: "board_issue"`.
5. Print one line: `Fetch: board issues → <N> fetched from board <id>`.

## Calling `jira_get_board_issues`

```
board_id: "<jira.board.id>"        # string, from config
jql:      "<filter, or empty>"     # REQUIRED parameter — see below
fields:   "summary,description,status,issuetype,priority,labels,components,parent,assignee,issuelinks"
limit:    50                       # max 50; page with start_at
start_at: 0
```

Three parameters bite:

- **`jql` is required, not optional.** It has no default. Pass an **empty string** to mean "everything on
  the board" — the board's own filter still applies, so an empty JQL is the whole board, not the whole site.
  If the server rejects an empty string, **do not give up and do not fall back to a project-wide search**:
  retry with `ORDER BY updated DESC`, which narrows nothing, and only then with
  `project = <jira.project_key> ORDER BY updated DESC`. Say which form worked — the third is a
  project-scoped approximation, not the board, and a reader deserves to know that.
- **`limit` caps at 50.** Page with `start_at` until fewer than `limit` rows come back. A board with 60
  issues silently returns 50 if you don't.
- **Name the `fields` you need; do not pass `*all` on this path.** A board fetch can return 50 issues, and
  `*all` pulls every custom field and full description on each — enough to crowd out the requirements and
  test cases this data is meant to be compared against.

**Custom fields are not in that default list, and two callers need them.** Append their ids explicitly:

| Caller | Extra fields | How to get the id |
|---|---|---|
| `/bc:vnv-sprint-prep` parity check | the platform (`OS`) field | `jira.platform.field_id`, or `jira_search_fields` with `keyword: "OS"` |
| Anything populating `raw_fields.sprint` | the sprint field | `jira_search_fields` with `keyword: "Sprint"` — never hardcode `customfield_10020` |

If a needed custom field id cannot be resolved, say so and continue with an empty value for it. **Do not
silently fall back to `*all`** to paper over a failed lookup — that trades a named gap for an unbounded
response. A missing platform value is handled downstream (`check-platform-parity` falls back to title
prefixes and flags lower confidence); an oversized fetch is not handled anywhere.

The `jql` narrows *within* the board:

- **Default (`include_done: false`)** → `statusCategory != Done`
- **`include_done: true`** → empty string
- **Feature name** → append `AND (component = "<feature>" OR labels = "<feature-slug>" OR text ~ "<feature>")`
- **Requirement IDs** → append `AND text ~ "<REQ-ID>"` per ID, OR-joined. Requirement IDs usually appear in
  the summary or description of the implementing ticket.

### Sprint-scoped reads

When the caller wants only the active sprint rather than the whole board:

1. `jira_get_sprints_from_board` with `board_id` and `state: "active"`.
2. `jira_get_sprint_issues` with the returned `sprint_id`, `limit: 50` and `start_at`, paging until fewer
   than `limit` rows come back.

**`jira_get_sprint_issues` has the same 1–50 cap as `jira_get_board_issues`, and the same consequence.** A
sprint with more than 50 issues silently returns the first 50 — and in `/bc:vnv-sprint-prep` the overflow is
never cloned, never validated, and never reported as missing. Page it every time; never accept the default.

A Kanban board has no sprints — `jira_get_sprints_from_board` returns an empty list. That is a normal
result, not an error: fall back to `jira_get_board_issues` and say the board is Kanban.

### If the board id is stale

`jira_get_board_issues` returns 404 when the board was deleted or renumbered. Re-resolve with
`jira_get_agile_boards` (filter by `project_key`, optionally `board_name`), report the mismatch, and tell the
user to re-run `/bc:setup` to update `jira.board.id`. Do not silently adopt a different board — picking the
wrong one would misrepresent what the team is working on.

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

Field mapping, description handling, `issuelinks` → `linked_ids`, and feature derivation are all identical
to `${CLAUDE_PLUGIN_ROOT}/providers/jira/fetch.md` — follow that file rather than duplicating the rules
here. One addition specific to board issues: also scan `summary` and `description` for requirement-ID
patterns (`project.id_patterns` in the config, e.g. `CWA-REQ-\d+`) and merge any matches into
`linked_ids`. That is what lets coverage and contradiction analysis tie a sprint ticket back to a
requirement when no formal Jira link exists.

## Placing a New Ticket in the Active Sprint (for `/bc:validate`)

Sprint assignment is a real operation now — no custom-field guesswork:

1. `jira_get_sprints_from_board` with `board_id` and `state: "active"` → the active `sprint_id`. Page this
   too if the board has many sprints; `limit` caps at 50 here as well.
2. Create the issue with `jira_create_issue`.
3. `jira_add_issues_to_sprint` with `sprint_id` and `issue_keys` (comma-separated, e.g. `"PROJ-150"`).

Step 3 is a **write** and follows the same rules as any other write: it prompts, and it does not run at all
in a dry run.

If there is no active sprint, the board is Kanban, or step 3 fails — **keep the created ticket** and tell the
user it landed in the backlog rather than the active sprint. Never fail or roll back ticket creation over
sprint placement.

## Error Handling

- **No `jira` section / `jira.pending` true / no `jira.board`** → skip with the visible line above, return `[]`.
- **Board tools missing entirely** → `jira_agile` is not in `TOOLSETS`. Name that as the cause and point at
  `~/.buddy-council/atlassian.env`. Treat as a failed source.
- **401** → bad or revoked API token; tell the user to re-run `/bc:setup`. Treat as a failed source.
- **403** → the account cannot browse this project or board. A Jira permission problem, not a config one.
- **404 on the board** → stale `jira.board.id`; handle as described above.
- **Malformed JQL** → report the exact server error and the query that produced it. Do not silently
  broaden the query; a wider result set would misrepresent the board.
- **Empty result with a working query** → acceptable. Report `0` and continue; an empty sprint is a real
  state, not an error.

Never fabricate board issues. If the fetch fails, report it and let the caller's Data Contract handling
decide whether to continue with partial data.
