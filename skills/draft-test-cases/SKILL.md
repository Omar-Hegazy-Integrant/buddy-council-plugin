---
description: Internal (used by /bc:vnv-sprint-prep, /bc:coverage, and /bc:ask) — Turn high-level test scenarios into TestRail test cases in the right suite folder, idempotently, with requirement refs that keep coverage analysis honest. The plugin's only TestRail authoring path.
user-invocable: false
---

# Draft Test Cases — Skill

Create TestRail cases from high-level scenarios. This is the plugin's **only** path for writing test cases;
every caller below goes through it.

On the V&V path it runs at the **end** of the pipeline, on tickets carrying
`jira.vnv_workflow.labels.ready_for_test_cases` (default `ready-for-test-case-creation`). The scenarios were
drafted in phase 5 and approved by the configured reviewer in phase 1a — this skill does not re-decide what
should be tested, it writes down what was already agreed. The other callers hand it scenarios they have
already traced to requirements and checked against existing cases; it does not redo that work either.

## These are skeletons, not finished cases

Every path produces **high-level scenarios**, so this produces **skeleton cases**: a title, preconditions, and
one step per Given/When/Then. Someone fleshes them out — the V&V team on the V&V path, the user on the
others. Say so in the report. Presenting them as finished test cases would overstate what the source material
supports and would discourage the review they still need.

The one exception is a user-supplied scenario that already carries explicit numbered steps (`steps`, below).
Those are written verbatim and not labelled a skeleton: the user wrote them, and collapsing five steps into
one When/Then would throw away exactly the detail they took the time to write.

## Three entry paths, one writer

Everything below the mapping step is shared. Only the source of the cases differs:

| Caller | Source | Reviewed? |
|---|---|---|
| **`/bc:vnv-sprint-prep` phase 7** | `stories[].scenarios` from the progress log, for tickets at `ready-for-test-case-creation` | Yes — by the configured reviewer |
| **`/bc:coverage`** | requirements the coverage run found with empty `linked_ids` | **No** — draft straight from the requirement text |
| **`/bc:ask` → `scenario-mapping-agent`** | scenarios the user already wrote (a file, pasted text, or a Jira ticket's description), traced and duplicate-checked by the agent | **By their author** — the user wrote them and confirmed every requirement trace and duplicate verdict before this skill runs |

All three paths obey the same linkage rule, platform rule, and folder rule. **Never write a second authoring
path**: the moment two code paths can create cases, one of them forgets the requirement field and quietly
degrades coverage reporting.

The unreviewed path carries one extra obligation: say so. Cases drafted from a requirement have had no human
judgement applied to whether they are the *right* tests, only that the requirement had none. Mark them in the
report, and never author more than the user explicitly selected.

The user-scenario path carries a smaller one: a trace the agent **matched** is still a judgement call, even
after the user confirmed it. Show each case's trace source (`explicit` or `matched`) in the plan and the
report so it stays visible.

## Input

- **Either** `stories` — progress-log entries at `ready-for-test-case-creation`, each with `dev_key`,
  `vnv_key`, `platform`, `counterpart`, and its `scenarios` array.
- **Or** `requirements` — canonical requirement artifacts the caller has already narrowed to the ones the
  user chose. Derive one scenario per requirement from its `description` and `rationale`: `traces_to` is the
  requirement `id`, `title` is a behavioural restatement of the requirement, `given`/`when`/`then` restate
  the requirement's own condition, action, and outcome without adding any it does not state, and `coverage`
  is `new`.
- **Or** `scenarios` — user-supplied scenarios, already traced and duplicate-checked by
  `scenario-mapping-agent` and confirmed by the user. Each carries `id`, `title`, `traces_to`, `trace_source`
  (`explicit` | `matched`), `given`, `when`, `then`, `coverage`, and `covered_by`, plus optionally `steps`
  (`[{"content": …, "expected": …}]`, only when the source had explicit numbered steps), `feature`,
  `platform`, and `platform_notes`. Alongside the list: `source` — a short label for where they came from
  (`scenarios.md`, `pasted text`, `PROJ-123`), used in provenance — and an optional `section_path` the user
  named, which overrides the folder strategy in step 4. There is no `dev_key` on this path — see step 3.
- Config at `.buddy-council/sources.json` → `test_cases`: `project_id`, `suite_id`, and the `authoring`
  block (`template_id`, `type_id`, `priority_id`, `requirement_field`, `section_strategy`, `section_root`,
  `create_missing_sections`).
- `apply` — when false, produce the plan and create nothing.

If `test_cases.authoring` is missing, **stop and say so**: the template alone decides which body fields a
case even has, and guessing it writes content into a field nobody reads. Point the user at `/bc:setup`.

If `test_cases.authoring.requirement_field` is `null` or empty, **stop too**. `/bc:setup` records `null` when
it could not find the instance's requirement field, and without it every case would be written with `refs`
only — exactly the silent orphan failure described in step 3. Tell the user to set it by hand or re-run
`/bc:setup`.

## Step 1: Get the scenarios

**`stories` path only.** The `requirements` path derives its scenarios as described under *Input*, and the
`scenarios` path receives them ready-made — both go straight to step 2.

**Prefer the progress log.** Phase 5 stores each scenario structurally in
`.buddy-council/vnv-progress.json` → `stories[].scenarios`. Read them from there.

**Only fall back to parsing the ticket description** when the log has no `scenarios` for a story — an entry
written before that field existed. Parsing is a fallback, not the primary path, because the description has
made a lossy round trip: `jira_update_issue` converts markdown to **wiki markup on Jira Server/Data Center**,
so `### S1 — …` can come back as `h3. S1 — …`, and the `<!-- bc-vnv:scenarios:start -->` HTML-comment
markers may not survive at all. When falling back, accept both markdown and wiki-markup headings, and locate
the block by the markers *or* by the `## High-Level Scenarios` / `h2. High-Level Scenarios` heading.

**If the block cannot be parsed cleanly, stop for that story.** Report what you found and create nothing.
Half a scenario set becomes half a test suite, and the missing half is invisible.

## Step 2: Decide which scenarios become cases

Each scenario carries a `coverage` verdict — `"new"` or `"covered"`, with the covering case id in
`covered_by` — from phase 5 or from the calling agent. Honour it:

| `coverage` | Action |
|---|---|
| `"new"` | Create a case. |
| `"covered"` (`covered_by: "TC-1234"`) | **Skip.** The caller had the existing cases in context and judged it already covered. Record the skip with the case it points at. |

When falling back to parsing a ticket description in step 1, the prose form `covered by TC-1234` means the
same as `"covered"`.

On the `scenarios` path the verdict is the one the user confirmed — including a `covered` they chose to
override to `new`. Do not second-guess it here; the agent already showed them the case it overlapped.

Then two guards on the scenario itself:

- **No `traces_to`** → do not create. Phase 5 treats an untraceable scenario as a signal that the
  requirement set is stale or the story does something unspecified. Creating a case from it buries exactly
  the thing worth surfacing. List these under `untraced` in the report.
- **Empty title, or neither Given/When/Then nor `steps`** → record as failed for that scenario, keep going
  with the rest. A scenario with explicit `steps` needs no When/Then — the steps replace it.

## Step 3: Map a scenario to a case

| Scenario field | TestRail field |
|---|---|
| `title` (with platform prefix — below) | `title` |
| `traces_to` + `dev_key` | `refs` **and** the requirement custom field — see below |
| `given` | `preconditions` → `custom_preconds` |
| `when` / `then` | `steps_separated`: `[{"content": <when>, "expected": <then>}]` |
| `steps` (`scenarios` path, when present) | `steps_separated` verbatim, **replacing** the `when`/`then` step |
| `platform_notes` | appended to `preconditions` under a `Platform:` line |
| config | `template_id`, `type_id`, `priority_id` from `test_cases.authoring` |

Append a provenance line to `preconditions` so a reader knows where the case came from and how finished it
is:

| Path | Provenance line |
|---|---|
| `stories` | `_Generated by Buddy-Council /bc:vnv-sprint-prep from V&V ticket VV-45 (scenario S1). High-level skeleton — expand before use._` |
| `requirements` | `_Generated by Buddy-Council /bc:coverage from requirement CWA-REQ-85. Unreviewed draft from requirement text — expand before use._` |
| `scenarios` | `_Generated by Buddy-Council /bc:ask from user-supplied scenario S1 (scenarios.md). High-level skeleton — expand before use._` — drop the second sentence when the scenario carried explicit `steps` |

### Requirement linkage — write it to BOTH fields, every time

This is the single most important thing this skill does. A test case that is not wired to a requirement is
worse than no test case at all: it makes TestRail *look* covered while the plugin's own analysis counts it
as an orphan.

Write the requirement IDs to **two** places on every case:

| Field | Why both are required |
|---|---|
| `refs` | TestRail's built-in References field. Visible in the UI, and what `testrail_get_cases_by_refs` searches — `providers/testrail/fetch.md` Strategy 3 uses it to find a requirement's cases. |
| `test_cases.authoring.requirement_field` (default `custom_jama_req_id`) | **The field this plugin actually reads.** `providers/testrail/fetch.md` maps it into `linked_ids`, and `normalize-artifacts` cross-links on that. |

**Writing only `refs` is a silent failure.** The case is created, looks correct in TestRail, and still comes
back with an empty `linked_ids` — so `/bc:coverage` reports it as an **orphan** *and* leaves its requirement
**untested**. The report gets strictly worse and the tool looks broken. Writing only the custom field loses
the built-in query path. Write both, always.

`refs` also carries the dev story key (`"CWA-REQ-85,PROJ-123"`) so a case can be traced back to the work that
prompted it — on the `stories` path only. The other two paths never put a Jira key in `refs`: step 5 guard 1
treats **any** case referencing a dev key as proof that phase 7 already authored that story, so a
user-scenario or gap-fill case carrying one would make phase 7 skip the story's reviewed scenarios — now, or
whenever the story reaches V&V later. The requirement custom field carries **requirement IDs only** — it is
what coverage parses, and a Jira key in there would read as a bogus requirement.

**Match the delimiter the existing corpus uses.** Sample one existing case in the target suite and copy its
formatting for the custom field (`"CWA-REQ-85, CWA-REQ-86"` is the common shape). A delimiter the parser
doesn't recognize is the same silent failure by another route.

Never create a case with neither field populated. A scenario with no `traces_to` was already excluded in
step 2 precisely so this cannot happen.

### Verify the linkage before writing the rest

`testrail_add_cases` runs its whole list before it returns, so a check after a batch call comes too late —
every case would already exist. **Send the run's first case on its own** (a one-entry `testrail_add_cases`
call), re-read it with `testrail_get_case`, and confirm both fields came back populated. Only then send the
rest (step 6). A custom field silently drops when its `system_name` is wrong or when the field is not
enabled for that project's template — TestRail accepts the payload and discards the value without error.

If either field did not stick — the requirement field empty or missing an expected requirement ID, or `refs`
missing one (or, on the `stories` path, missing the dev key) — **stop the run**. Report the field name you
used, what `testrail_get_case_fields` says exists, and the one case that was created, so it can be fixed or
deleted by hand. Continuing would produce a suite of orphans that someone has to unpick by hand.

When the first case is skipped because its title already exists, it proves nothing — verify the first case
that is actually created instead, still before sending the remainder.

### The platform prefix is a correctness requirement, not cosmetics

When `platform` is `ios` or `android`, prefix the title: `[iOS] Sync retries on transient network failure`.

This is not styling. A dev story and its counterpart (`counterpart` in the log) produce two V&V tickets with
near-identical scenarios. `testrail_add_cases` skips a case whose title already exists in the target folder —
so **without the prefix, the second platform's cases would be silently skipped as duplicates**, and half the
suite would quietly go missing. Use `both` → no prefix. On the `scenarios` path `platform` is set only when
the user's source states one; when it is absent, there is no prefix.

## Step 4: Choose the folder

Build `section_path` from `test_cases.authoring`:

- `section_strategy: "feature"` → the scenario's feature, e.g. `Sync/Offline handling`: the story's
  `feature` on the `stories` path, the requirement's `feature` on the `requirements` path, and the `feature`
  the agent took from the scenario's first traced requirement on the `scenarios` path.
- `section_strategy: "fixed"` → everything under `section_root`.
- `section_root` set → prefix it, e.g. `V&V/Sync/Offline handling`, keeping generated cases separable from
  hand-written ones.

A `section_path` the user explicitly named on the `scenarios` path replaces all of the above, verbatim and
without the `section_root` prefix — they chose the folder.

Pass `create_missing_sections` through from config. When it is false and the folder does not exist, the tool
fails that case with the list of folders that do exist — surface that verbatim rather than inventing a
fallback folder. A case written to the wrong folder is worse than a case not written.

## Step 5: Check TestRail before writing

Three guards, in this order. The first is the one that matters:

1. **TestRail is the source of truth.** Call `testrail_get_cases_by_refs` with `refs: "<dev_key>"`. If cases
   already reference this story, it has been authored — skip it. This keeps the workflow's principle intact:
   the external system is the truth, the progress log only remembers *why*. It matters here because
   `ready-for-test-case-creation` is terminal and there is no further label to signal "cases written".
   TestRail's `refs` filter is a filter, not an exact match — confirm by reading `refs` off the returned
   cases rather than trusting the hit count.
2. **The progress log** — `testrail_case_ids` on the story entry.
3. **`skip_if_title_exists`** inside `testrail_add_cases`, left at its default, as a backstop.

Guards 1 and 2 belong to the `stories` path — the other paths have no story to look up. Their callers have
already done the equivalent from fresh TestRail data: on the `requirements` path, the requirement was
reported untested by the run that just fetched its cases; on the `scenarios` path, each `coverage` verdict
came from the cases linked to the scenario's traced requirements. Guard 3 applies to every path.

## Step 6: Write

Write in two calls, so the step-3 linkage check runs before most of the cases exist:

1. **The first case alone** — a one-entry `testrail_add_cases` call — then verify it as step 3 describes.
2. **Everything else**, once the check passes. **`stories` path:** the rest of the first story's cases, then
   one call per remaining story. **Other paths:** one call for the rest of the batch — each case carries its
   own `section_path`.

Pass `project_id`, `suite_id`, `section_path`, `create_missing_sections`, and the case list on every call.

The tool loops `add_case` internally — TestRail has no bulk-create endpoint — so a failed case does not stop
the others, and a `429` is retried with its `Retry-After`. Take `created`, `skipped`, `failed`, and
`sections_created` straight from its response.

Then, **`stories` path only, and only for stories that got at least one case**, post one comment on the V&V
ticket. The other paths never write to Jira:

```markdown
**Test cases created** — Buddy-Council `/bc:vnv-sprint-prep`

| Scenario | Case | Folder |
|---|---|---|
| S1 — Sync retries on transient network failure | [C1234](https://company.testrail.io/index.php?/cases/view/1234) | V&V/Sync |
| S3 — Data is encrypted in transit during retry | [C1235](https://company.testrail.io/index.php?/cases/view/1235) | V&V/Sync |

Skeletons generated from the approved scenarios — expand them before the test run.
```

**Do not change the label.** `ready-for-test-case-creation` stays terminal; there is no fifth pipeline state.
Adding one would change a mutually-exclusive state machine the whole workflow is built on.

## Step 7: Record

**`stories` path only.** Per story, write `testrail_case_ids` (the ids TestRail returned) and
`cases_created_at` (ISO 8601 UTC). Record only ids TestRail actually returned — never invent one to keep the
loop tidy. The other paths keep no log: TestRail itself, via the requirement field, is what the next run
reads.

## Dry run

With `apply: false`, resolve folders, run the step-5 checks, and print the plan without creating anything.
Pass `dry_run: true` to `testrail_add_cases` so its own folder resolution and duplicate-check run too. A
folder that does not exist yet comes back with `section_id: null` and a `would create folder "…"` entry in
`sections_created` — that is the plan, not a failure. It only fails when `create_missing_sections` is false.
The dry run is a single call; the step-6 split applies to the real write only:

```
Would create 5 cases in project 45, suite 9277 (Master)

  VV-45  PROJ-123 [iOS]   → V&V/Sync/Offline handling
    S1  [iOS] Sync retries on transient network failure    refs: CWA-REQ-85,PROJ-123
    S3  [iOS] Data is encrypted in transit during retry    refs: CWA-REQ-120,PROJ-123
    S2  skipped — covered by TC-1234

  VV-46  PROJ-124 [Android] → V&V/Sync/Offline handling
    S1  [Android] Sync retries on transient network failure  refs: CWA-REQ-85,PROJ-124
    ...

  Folders that would be created: V&V/Sync/Offline handling
  Untraced scenarios (not created): VV-47 S4 — "Rate limiting behaviour"

Already authored, skipping: VV-40 (3 cases found by refs PROJ-118)
```

On the `scenarios` path the plan is one batch, and each case shows where its trace came from:

```
Would create 2 cases in project 45, suite 9277 (Master) from scenarios.md

  S1  Sync retries on transient network failure   → V&V/Sync   refs: CWA-REQ-85 (explicit)
  S2  Offline banner clears on reconnect          → V&V/Sync   refs: CWA-REQ-86 (matched)
  S4  skipped — covered by TC-1234

  Untraced (not created): S3 — "Rate limiting behaviour"
```

## Output

```json
{
  "dry_run": false,
  "stories": [
    {
      "vnv_key": "VV-45",
      "dev_key": "PROJ-123",
      "section_path": "V&V/Sync/Offline handling",
      "created": [{ "scenario": "S1", "case_id": 1234, "title": "[iOS] Sync retries…", "refs": "CWA-REQ-85,PROJ-123" }],
      "skipped": [{ "scenario": "S2", "reason": "covered by TC-1234" }],
      "untraced": [],
      "failed": []
    }
  ],
  "sections_created": ["V&V/Sync/Offline handling"],
  "comment_posted": ["VV-45"]
}
```

On the `requirements` and `scenarios` paths, `stories` holds a single batch entry with `vnv_key` and `dev_key`
both `null`; `comment_posted` is always empty. On the `scenarios` path each
`created` entry also carries `trace_source`.

## Error Handling

- **No `test_cases.authoring`** → stop before writing anything; direct to `/bc:setup`.
- **Scenario block unparseable** → skip that story, report what was found, create nothing for it.
- **Required custom field missing (400)** → TestRail names the field in its error; surface it verbatim and
  tell the user to add it to `authoring` or run `testrail_get_case_fields` to see what the instance requires.
- **Folder missing with `create_missing_sections: false`** → report the tool's list of existing folders; do
  not substitute another folder.
- **Partial batch failure** → keep going, then report created/skipped/failed per story. Always surface
  `failed`: a silently dropped case is a test that never gets written.
- **Comment fails after cases were created** → the cases still exist. Say so plainly and print the case
  links in the session so the traceability is not lost.
