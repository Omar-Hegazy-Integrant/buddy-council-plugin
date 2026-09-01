---
description: Internal (used by /bc:vnv-sprint-prep) — Turn approved V&V scenarios into TestRail test cases in the right suite folder, idempotently, with requirement refs that keep coverage analysis honest.
user-invocable: false
---

# Draft Test Cases — Skill

Create TestRail cases from the high-level scenarios a reviewer has already approved on a V&V ticket.

This runs at the **end** of the V&V pipeline, on tickets carrying
`jira.vnv_workflow.labels.ready_for_test_cases` (default `ready-for-test-case-creation`). The scenarios were
drafted in phase 5 and approved by the configured reviewer in phase 1a — this skill does not re-decide what
should be tested, it writes down what was already agreed.

## These are skeletons, not finished cases

Phase 5 produces **high-level scenarios**, so this produces **skeleton cases**: a title, preconditions, and
one step per Given/When/Then. The V&V team fleshes them out. Say so in the report. Presenting them as
finished test cases would overstate what the source material supports and would discourage the review they
still need.

## Two entry paths, one writer

Everything below the mapping step is shared. Only the source of the cases differs:

| Caller | Source | Reviewed? |
|---|---|---|
| **`/bc:vnv-sprint-prep` phase 7** | `stories[].scenarios` from the progress log, for tickets at `ready-for-test-case-creation` | Yes — by the configured reviewer |
| **`/bc:coverage`** | requirements the coverage run found with empty `linked_ids` | **No** — draft straight from the requirement text |

Both paths obey the same linkage rule, platform rule, and folder rule. **Never write a second authoring
path**: the moment two code paths can create cases, one of them forgets the requirement field and quietly
degrades coverage reporting.

The unreviewed path carries one extra obligation: say so. Cases drafted from a requirement have had no human
judgement applied to whether they are the *right* tests, only that the requirement had none. Mark them in the
report, and never author more than the user explicitly selected.

## Input

- **Either** `stories` — progress-log entries at `ready-for-test-case-creation`, each with `dev_key`,
  `vnv_key`, `platform`, `counterpart`, and its `scenarios` array.
- **Or** `requirements` — canonical requirement artifacts the caller has already narrowed to the ones the
  user chose. Derive one scenario per requirement from its `description` and `rationale`: `traces_to` is the
  requirement `id`, `title` is a behavioural restatement of the requirement, and `coverage` is `new`.
- Config at `.buddy-council/sources.json` → `test_cases`: `project_id`, `suite_id`, and the `authoring`
  block (`template_id`, `type_id`, `priority_id`, `requirement_field`, `section_strategy`, `section_root`,
  `create_missing_sections`).
- `apply` — when false, produce the plan and create nothing.

If `test_cases.authoring` is missing, **stop and say so**: the template alone decides which body fields a
case even has, and guessing it writes content into a field nobody reads. Point the user at `/bc:setup`.

## Step 1: Get the scenarios

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

Each scenario carries a `coverage` verdict from phase 5. Honour it:

| `coverage` | Action |
|---|---|
| `new` | Create a case. |
| `covered by TC-1234` | **Skip.** Phase 5 had the existing cases in context and judged it already covered. Record the skip with the case it points at. |

Then two guards on the scenario itself:

- **No `traces_to`** → do not create. Phase 5 treats an untraceable scenario as a signal that the
  requirement set is stale or the story does something unspecified. Creating a case from it buries exactly
  the thing worth surfacing. List these under `untraced` in the report.
- **Empty title or no Given/When/Then** → record as failed for that scenario, keep going with the rest.

## Step 3: Map a scenario to a case

| Scenario field | TestRail field |
|---|---|
| `title` (with platform prefix — below) | `title` |
| `traces_to` + `dev_key` | `refs` **and** the requirement custom field — see below |
| `given` | `preconditions` → `custom_preconds` |
| `when` / `then` | `steps_separated`: `[{"content": <when>, "expected": <then>}]` |
| `platform_notes` | appended to `preconditions` under a `Platform:` line |
| config | `template_id`, `type_id`, `priority_id` from `test_cases.authoring` |

Append a provenance line to `preconditions` so a reader knows where the case came from and that it is a
skeleton:

```
_Generated by Buddy-Council /bc:vnv-sprint-prep from V&V ticket VV-45 (scenario S1). High-level skeleton — expand before use._
```

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
prompted it. The requirement custom field carries **requirement IDs only** — it is what coverage parses, and
a Jira key in there would read as a bogus requirement.

**Match the delimiter the existing corpus uses.** Sample one existing case in the target suite and copy its
formatting for the custom field (`"CWA-REQ-85, CWA-REQ-86"` is the common shape). A delimiter the parser
doesn't recognize is the same silent failure by another route.

Never create a case with neither field populated. A scenario with no `traces_to` was already excluded in
step 2 precisely so this cannot happen.

### Verify the linkage before writing the rest

After the **first** case in a run is created, re-read it with `testrail_get_case` and confirm both fields
came back populated. A custom field silently drops when its `system_name` is wrong or when the field is not
enabled for that project's template — TestRail accepts the payload and discards the value without error.

If the requirement field did not stick, **stop the run**. Report the field name you used, what
`testrail_get_case_fields` says exists, and how many cases were already created. Continuing would produce a
suite of orphans that someone has to unpick by hand.

### The platform prefix is a correctness requirement, not cosmetics

When `platform` is `ios` or `android`, prefix the title: `[iOS] Sync retries on transient network failure`.

This is not styling. A dev story and its counterpart (`counterpart` in the log) produce two V&V tickets with
near-identical scenarios. `testrail_add_cases` skips a case whose title already exists in the target folder —
so **without the prefix, the second platform's cases would be silently skipped as duplicates**, and half the
suite would quietly go missing. Use `both` → no prefix.

## Step 4: Choose the folder

Build `section_path` from `test_cases.authoring`:

- `section_strategy: "feature"` → the story's `feature`, e.g. `Sync/Offline handling`.
- `section_strategy: "fixed"` → everything under `section_root`.
- `section_root` set → prefix it, e.g. `V&V/Sync/Offline handling`, keeping generated cases separable from
  hand-written ones.

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

## Step 6: Write

One `testrail_add_cases` call per story, with all of that story's cases. Pass `project_id`, `suite_id`,
`section_path`, `create_missing_sections`, and the case list.

The tool loops `add_case` internally — TestRail has no bulk-create endpoint — so a failed case does not stop
the others, and a `429` is retried with its `Retry-After`. Take `created`, `skipped`, `failed`, and
`sections_created` straight from its response.

Then, **only for stories that got at least one case**, post one comment on the V&V ticket:

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

Per story, write `testrail_case_ids` (the ids TestRail returned) and `cases_created_at` (ISO 8601 UTC).
Record only ids TestRail actually returned — never invent one to keep the loop tidy.

## Dry run

With `apply: false`, resolve folders, run the step-5 checks, and print the plan without creating anything.
Pass `dry_run: true` to `testrail_add_cases` so its own folder resolution and duplicate-check run too:

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
