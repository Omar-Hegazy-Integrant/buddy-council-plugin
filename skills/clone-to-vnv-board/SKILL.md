---
description: Internal (used by /bc:vnv-sprint-prep) — Clone dev sprint stories onto the V&V board as new issues, idempotently, linked back to the dev story with a real Jira issue link plus a src-<DEV-KEY> label.
user-invocable: false
---

# Clone to V&V Board — Skill

Create a matching issue on the V&V board for each in-scope dev sprint story.

There is no "clone" operation — this is `jira_create_issue` in the V&V project with the source story's
content, followed by a real `jira_create_issue_link` back to the dev story. Two things shape everything below:

- **Creating is not idempotent by itself.** Running twice would happily make two clones. The duplicate guard
  in step 1 is not optional.
- **The link and the label are both required, and they do different jobs.** Earlier versions used the
  `src-<DEV-KEY>` label *because* issue links were impossible. Links work now — but the label stays, because
  it is the duplicate guard's search key (`labels = "src-PROJ-150"` is one fast JQL hit, whereas re-reading
  every V&V issue's links is not). Keep both: the link is for humans, the label is for idempotency.

## Input

- `stories`: dev stories to clone, canonical schema, with their parity records from `check-platform-parity`.
- Config: `jira.vnv_board.project_key`, `jira.default_issue_type`, `jira.base_url`.
- `apply`: boolean. When false, produce the plan and create nothing.

## Step 1: Duplicate guard (always, before any create)

For each dev story, in order:

1. **Progress log** — if `.buddy-council/vnv-progress.json` already has this `dev_key` with a `vnv_key`,
   confirm that issue still exists with `jira_get_issue`. Exists → skip, it is already cloned. Gone (deleted
   in Jira) → clear the stale `vnv_key` and fall through to step 2.
2. **The V&V project itself** — search
   `project = <vnv_project_key> AND labels = "src-<DEV-KEY>"` via `jira_search`. Any hit → adopt
   the first into the progress log and skip creating. This catches clones made by a teammate, on another
   machine, or before the log existed.

Only stories surviving both checks are candidates. **Prefer a false "already cloned" over a false "create"** —
a missed clone costs one re-run, a duplicate costs manual cleanup on a shared board.

## Step 2: Build the issue

Call `jira_create_issue` with:

| Parameter | Value |
|---|---|
| `project_key` | `jira.vnv_board.project_key` — **never** the dev project |
| `issue_type` | `jira.default_issue_type`, falling back to `"Task"`. Verify it exists in the V&V project with `jira_get_project_issue_types`; the V&V project may not have the dev project's types |
| `summary` | The source summary, unchanged — keep it recognizable; the differing project key already distinguishes it |
| `description` | Back-reference block + the source description (below), as Markdown |
| `additional_fields` | JSON **string**: `{"labels": ["src-<DEV-KEY>", "<platform>", "<pending_validation>"]}` |

`additional_fields` takes a JSON-encoded string, not an object. Passing a bare object is a common cause of a
create that fails with an opaque parse error.

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
`jira_update_issue` to set the labels. If *that* also fails, the clone exists but is untraceable by label —
report it prominently, because the next run's duplicate guard will not find it.

## Step 3: Link the clone to its dev story

After a successful create, call `jira_create_issue_link`:

```
link_type:          "Relates"        # resolve first — see below
inward_issue_key:   "<vnv_key>"      # the clone
outward_issue_key:  "<dev_key>"      # the source story
```

**Resolve the link type name once per run, don't hardcode it.** Call `jira_get_link_types` and pick the first
match for `Relates`/`Relates to`; sites rename and remove link types, and an invalid name fails the call.
Cache the resolved name for the rest of the batch.

This is a **write**: it runs only under `--apply` and it prompts, like every other write in this workflow.

**A failed link is not a failed clone.** If linking fails — the link type doesn't exist, the account can't
link across projects, `jira_links` isn't in `TOOLSETS` — keep the issue, record `linked: false`, and report
it. The `src-<DEV-KEY>` label and the description back-reference still provide traceability, which is exactly
what earlier versions ran on. Never delete a created issue because its link failed.

## Step 4: Record

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
  Each linked back to its dev story with a "Relates" issue link.

Already cloned, skipping: PROJ-123 → VV-45, PROJ-124 → VV-46
```

## Output

```json
{
  "created": [{ "dev_key": "PROJ-150", "vnv_key": "VV-61", "url": "https://yourorg.atlassian.net/browse/VV-61", "linked": true }],
  "skipped": [{ "dev_key": "PROJ-123", "vnv_key": "VV-45", "reason": "already cloned" }],
  "failed":  [{ "dev_key": "PROJ-152", "error": "issue type 'Story' does not exist in project VV" }]
}
```

Always surface `failed` in the caller's report, and list any `linked: false` clones separately so someone can
link them by hand. A silently dropped story is a story that never gets tested.

## Error Handling

- **V&V project key equals the dev project key** → refuse; the caller checks this first, but check again
  rather than trusting it. Clones landing on the dev board are hard to undo on a shared board.
- **Issue type missing in the V&V project** → list the types that do exist and suggest the closest; do not
  silently substitute one.
- **403 on create** → the account lacks Create Issues on the V&V project. A Jira permission problem.
- **`jira_create_issue_link` missing entirely** → `jira_links` is not in `TOOLSETS` in
  `~/.buddy-council/atlassian.env`. Report that as the cause, continue with label-only traceability, and
  suggest re-running `/bc:setup`.
- **Partial batch failure** → keep going through the remaining stories, then report created/skipped/failed
  separately. Never roll back successful creates; deleting issues is not something this workflow does.
