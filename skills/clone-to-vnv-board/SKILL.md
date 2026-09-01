---
description: Internal (used by /bc:vnv-sprint-prep) — Clone dev sprint stories onto the V&V board as new issues, idempotently, linked back to the source story and stamped with whatever the V&V board's filter keys off.
user-invocable: false
---

# Clone to V&V Board — Skill

Create a matching issue on the V&V board for each in-scope dev sprint story.

There is no "clone" operation in Jira's API — this is `jira_create_issue` with the source story's content,
plus a back-reference, plus whatever makes the new issue land on the V&V board. Two things shape everything
below:

- **A board is a saved filter, not a container.** You cannot create an issue "on board 77". You create it in
  a project and it appears wherever a filter matches. The `discriminator` in step 2 is what makes the match
  happen — and when the V&V board shares a project with the dev board, it is the *only* thing that does.
- **Creating is not idempotent by itself.** Running twice would happily make two clones. The duplicate guard
  in step 1 is not optional.

## Input

- `stories`: dev stories to clone, canonical schema, with their parity records from `check-platform-parity`.
- Config: `jira.vnv_board.{id, project_key, discriminator}`, `jira.default_issue_type`, `jira.base_url`.
- `apply`: boolean. When false, produce the plan and create nothing.

## Step 1: Duplicate guard (always, before any create)

For each dev story, in order:

1. **Progress log** — if `.buddy-council/vnv-progress.json` already has this `dev_key` with a `vnv_key`,
   confirm that issue still exists with `jira_get_issue`. Exists → skip, it is already cloned. Gone (deleted
   in Jira) → clear the stale `vnv_key` and fall through to step 2.
2. **The V&V board itself** — call `jira_get_board_issues` with `jira.vnv_board.id` and
   `jql: 'labels = "src-<DEV-KEY>"'`. Any hit → adopt the first into the progress log and skip creating. This
   catches clones made by a teammate, on another machine, or before the log existed.

   Search the **board**, not the project. When both boards share a project, a project-wide search would also
   match dev-board issues and could make a real dev story look like an existing clone.

Only stories surviving both checks are candidates. **Prefer a false "already cloned" over a false "create"** —
a missed clone costs one re-run, a duplicate costs manual cleanup on a shared board.

## Step 2: Build the issue

| Field | Value |
|---|---|
| `project_key` | `jira.vnv_board.project_key`. This **may** equal the dev project — that is a supported layout, and the discriminator below is what keeps the clone off the dev board |
| `issue_type` | `jira.default_issue_type`, falling back to `"Task"`. Verify it exists in the target project with `jira_get_project_issue_types` |
| `summary` | The source summary, unchanged — keep it recognizable |
| `description` | Back-reference block + the source description (below) |
| `additional_fields` | `{"labels": [...]}` — see below. Also carries the discriminator when its `kind` is `label`, `component`, or a custom field |

`jira_create_issue` takes no `labels` parameter of its own; labels go inside `additional_fields`.

**Labels always include** `src-<DEV-KEY>`, the platform label, and
`jira.vnv_workflow.labels.pending_validation` (default `pending-validation`) — the pipeline's entry state.
Applying it at create time is what makes a clone visible on a label-filtered board even if the run stops
before validation. Phase 4 **replaces** it rather than adding alongside; see *Label Discipline* in
`${CLAUDE_PLUGIN_ROOT}/agents/vnv-sprint-prep-agent.agent.md`.

### Applying the discriminator

Read `jira.vnv_board.discriminator`. It is `null` when the V&V board has its own project — nothing extra to
do. Otherwise apply it by `kind`:

| `kind` | How to apply |
|---|---|
| `label` | Add `value` to the labels array in `additional_fields` |
| `component` | Pass `components: "<value>"` to `jira_create_issue` |
| `issue_type` | Use `value` as `issue_type`, overriding `jira.default_issue_type` |
| `sprint` | Create the issue first, then `jira_get_sprints_from_board` (`board_id: jira.vnv_board.id`, `state: "active"`) → `jira_add_issues_to_sprint` with the returned `sprint_id` and the new key |

**When the discriminator is `null` and the two boards share a project, say so before creating** — in the dry
run and again in the apply confirmation. The clone will land in the shared project and may appear on the dev
board. That is the user's call to accept, not yours to hide.

After creating, **verify placement**: call `jira_get_board_issues` on `jira.vnv_board.id` with
`jql: 'key = <new-key>'`. If the new issue is not on the board, the discriminator is wrong. Report it
prominently, keep the issue (deleting is not something this workflow does), and stop applying to further
stories until the user decides — a wrong discriminator repeated across a sprint is a lot of manual cleanup.

Description template:

```markdown
> **V&V clone of [PROJ-123](https://jira.company.com/browse/PROJ-123)** · platform: iOS · sprint: Sprint 14
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

## Step 3: Link the clone to its source

`mcp-atlassian` can create real Jira issue links, so **create one** rather than relying on the label alone:

1. `jira_get_link_types` → pick a non-directional relationship, preferring `"Relates"`. Sites rename these
   freely; match case-insensitively on the returned names and never hardcode an id.
2. `jira_create_issue_link` with that `link_type`, `inward_issue_key: <vnv_key>`, and
   `outward_issue_key: <dev_key>`.

Record `link_created: true` on success.

**The `src-<DEV-KEY>` label stays regardless.** It is not redundant: the duplicate guard in step 1 searches by
label because a label search is one board query, while link traversal costs a fetch per issue. The link is
for humans, the label is for the guard.

If linking fails — the site restricts link creation, or no usable type exists — **do not fail the clone**.
Record `link_created: false` with the reason, and note that traceability rests on the label and the
description back-reference. Report it once at the end, not per story.

## Step 4: Record

Write into `.buddy-council/vnv-progress.json`: `dev_key`, the returned `vnv_key`, `platform`,
`platform_source`, `counterpart`, `parity`, `state: "cloned"`, `link_created`, and `last_checked_at`
(ISO 8601 UTC).

Record only keys Jira actually returned. If a create failed, record the failure with its error — never invent
a key to keep the loop tidy.

## Dry run

With `apply: false`, emit the plan and stop. Show the discriminator so the user can veto it before anything
is written:

```
Would clone 4 stories → project CWA, board 1568 (V&V)
  placement: label "vnv"  (shared project with dev board 1656)

  PROJ-150  [iOS]      "Add offline banner"    labels: src-PROJ-150, ios, vnv, pending-validation
  PROJ-151  [Android]  "Add offline banner"    labels: src-PROJ-151, android, vnv, pending-validation
  PROJ-152  [iOS]      "Retry failed sync"     labels: src-PROJ-152, ios, vnv, pending-validation
  PROJ-153  [Android]  "Retry failed sync"     labels: src-PROJ-153, android, vnv, pending-validation

  each also linked "Relates" → its dev story

Already cloned, skipping: PROJ-123 → CWA-945, PROJ-124 → CWA-946
```

## Output

```json
{
  "created": [{ "dev_key": "PROJ-150", "vnv_key": "CWA-961", "url": "https://jira.company.com/browse/CWA-961", "link_created": true, "on_board": true }],
  "skipped": [{ "dev_key": "PROJ-123", "vnv_key": "CWA-945", "reason": "already cloned" }],
  "failed":  [{ "dev_key": "PROJ-152", "error": "issue type 'Story' does not exist in project VV" }]
}
```

Always surface `failed` in the caller's report. A silently dropped story is a story that never gets tested.

## Error Handling

- **V&V board id equals the dev board id** → refuse. The caller checks this first, but check again rather
  than trusting it: cloning onto the source board is the one mistake that is genuinely hard to undo on a
  shared board. A shared *project* is fine and is not an error.
- **Issue type missing in the target project** → list the types that do exist and suggest the closest; do not
  silently substitute one.
- **Clone created but not on the V&V board** → the discriminator is wrong. Stop, report, and ask; see step 2.
- **403 on create** → the account lacks Create Issues on that project. Say so plainly.
- **Partial batch failure** → keep going through the remaining stories, then report created/skipped/failed
  separately. Never roll back successful creates; deleting issues is not something this workflow does.
