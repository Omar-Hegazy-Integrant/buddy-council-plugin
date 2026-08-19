---
description: Internal (used by /bc:vnv-sprint-prep) — Clone dev sprint stories onto the V&V board as new issues, idempotently, carrying a src-<DEV-KEY> back-reference since Atlassian's MCP server cannot create Jira issue links.
user-invocable: false
---

# Clone to V&V Board — Skill

Create a matching issue on the V&V board for each in-scope dev sprint story.

There is no "clone" operation in Atlassian's MCP server — this is `createJiraIssue` in the V&V project with
the source story's content plus a back-reference. Two consequences shape everything below:

- **No Jira link is possible.** `update.issuelinks` is silently ignored and there is no `createIssueLink`
  tool. Traceability rides on a `src-<DEV-KEY>` label and a line in the description.
- **Creating is not idempotent by itself.** Running twice would happily make two clones. The duplicate guard
  in step 1 is not optional.

## Input

- `stories`: dev stories to clone, canonical schema, with their parity records from `check-platform-parity`.
- Config: `jira.cloud_id`, `jira.vnv_board.project_key`, `jira.default_issue_type`, `jira.base_url`.
- `apply`: boolean. When false, produce the plan and create nothing.

## Step 1: Duplicate guard (always, before any create)

For each dev story, in order:

1. **Progress log** — if `.buddy-council/vnv-progress.json` already has this `dev_key` with a `vnv_key`,
   confirm that issue still exists with `getJiraIssue`. Exists → skip, it is already cloned. Gone (deleted in
   Jira) → clear the stale `vnv_key` and fall through to step 2.
2. **The V&V project itself** — search
   `project = <vnv_project_key> AND labels = "src-<DEV-KEY>"` via `searchJiraIssuesUsingJql`. Any hit → adopt
   the first into the progress log and skip creating. This catches clones made by a teammate, on another
   machine, or before the log existed.

Only stories surviving both checks are candidates. **Prefer a false "already cloned" over a false "create"** —
a missed clone costs one re-run, a duplicate costs manual cleanup on a shared board.

## Step 2: Build the issue

| Field | Value |
|---|---|
| `cloudId` | `jira.cloud_id` |
| `projectKey` | `jira.vnv_board.project_key` — **never** the dev project |
| `issueTypeName` | `jira.default_issue_type`, falling back to `"Task"`. Verify it exists in the V&V project with `getJiraProjectIssueTypesMetadata`; the V&V project may not have the dev project's types |
| `summary` | The source summary, unchanged — keep it recognizable; the differing project key already distinguishes it |
| `description` | Back-reference block + the source description (below) |
| `contentFormat` | `"markdown"` |
| `additional_fields` | `{"labels": ["src-<DEV-KEY>", "<platform>", "<pending_validation>"]}` |

The third label is `jira.vnv_workflow.labels.pending_validation` (default `pending-validation`) — the
pipeline's entry state. Applying it here is what makes a clone visible on a label-filtered board even if the
run stops before validation. Phase 4 **replaces** it rather than adding alongside; see *Label Discipline* in
`${CLAUDE_PLUGIN_ROOT}/agents/vnv-sprint-prep-agent.agent.md`.

Description template:

```markdown
> **V&V clone of [PROJ-123](https://yourorg.atlassian.net/browse/PROJ-123)** · platform: iOS · sprint: Sprint 14
> Created by Buddy-Council `/bc:vnv-sprint-prep`. The dev story is the source of truth — raise questions there, not here.

---

<the source story's description, verbatim>
```

Keep the source description **verbatim**. This is a clone, not a rewrite: paraphrasing here would silently
change what the V&V team validates against. If the source description is empty, say so explicitly in the
clone (`_The source story has no description._`) rather than leaving a bare block.

If `additional_fields` labels are rejected by the site, create without them and immediately follow with
`editJiraIssue` to set the labels. If *that* also fails, the clone exists but is untraceable by label —
report it prominently, because the next run's duplicate guard will not find it.

## Step 3: Record

Write into `.buddy-council/vnv-progress.json`: `dev_key`, the returned `vnv_key`, `platform`,
`platform_source`, `counterpart`, `parity`, `state: "cloned"`, and `last_checked_at` (ISO 8601 UTC).

Record only keys Jira actually returned. If a create failed, record the failure with its error — never
invent a key to keep the loop tidy.

## Dry run

With `apply: false`, emit the plan and stop:

```
Would clone 4 stories → VV project:
  PROJ-150  [iOS]      "Add offline banner"              labels: src-PROJ-150, ios
  PROJ-151  [Android]  "Add offline banner"              labels: src-PROJ-151, android
  PROJ-152  [iOS]      "Retry failed sync"               labels: src-PROJ-152, ios
  PROJ-153  [Android]  "Retry failed sync"               labels: src-PROJ-153, android

Already cloned, skipping: PROJ-123 → VV-45, PROJ-124 → VV-46
```

## Output

```json
{
  "created": [{ "dev_key": "PROJ-150", "vnv_key": "VV-61", "url": "https://yourorg.atlassian.net/browse/VV-61" }],
  "skipped": [{ "dev_key": "PROJ-123", "vnv_key": "VV-45", "reason": "already cloned" }],
  "failed":  [{ "dev_key": "PROJ-152", "error": "issue type 'Story' does not exist in project VV" }]
}
```

Always surface `failed` in the caller's report. A silently dropped story is a story that never gets tested.

## Error Handling

- **V&V project key equals the dev project key** → refuse; the caller checks this first, but check again
  rather than trusting it. Clones landing on the dev board are hard to undo on a shared board.
- **Issue type missing in the V&V project** → list the types that do exist and suggest the closest; do not
  silently substitute one.
- **403 on create** → the account lacks Create Issues on the V&V project, or the site admin has not enabled
  the Rovo MCP server. Name both.
- **Partial batch failure** → keep going through the remaining stories, then report created/skipped/failed
  separately. Never roll back successful creates; deleting issues is not something this workflow does.
