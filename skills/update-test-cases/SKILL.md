---
description: Internal (used by /bc:ask, /bc:coverage, /bc:contradiction, and /bc:vnv-sprint-prep) — Update existing TestRail test cases safely — fresh reads, field-level diffs, requirement links that are never silently dropped, a conflict check, and read-back verification. The plugin's only TestRail update path.
user-invocable: false
---

# Update Test Cases — Skill

Change existing TestRail cases. This is the plugin's **only** path for updating test cases, the way
`draft-test-cases` is its only path for creating them; every caller below goes through it.

An update is riskier than a create. A bad create adds a case someone can delete; a bad update quietly
destroys what was there — a step the V&V team wrote by hand, or the requirement link that makes a case count
as coverage. Everything below exists to make that impossible to do by accident.

## Four entry paths, one updater

| Caller | Path | What it may change | Who decided |
|---|---|---|---|
| **`/bc:ask`** (edit request) | `edits` | exactly what the user asked, on the cases they named | the user, in their request |
| **`/bc:coverage`** | `orphans` | requirement links only — `refs` and the requirement field | mechanical for stranded links; the user confirms every proposed link |
| **`/bc:contradiction`** | `contradictions` | the conflicting precondition, step, or expected result — nothing else | the user picks which findings to fix |
| **`/bc:vnv-sprint-prep` phase 7** | `scenarios` | the skeleton content regenerated from revised approved scenarios | the reviewer (re-approval) or the V&V team's own edit of the approved block |

**Never call `testrail_update_case` or `testrail_update_cases` from anywhere else.** The moment two code paths
can update cases, one of them forgets the requirement field.

## The rules every path obeys

1. **Read before you write.** Fetch each case fresh with `testrail_get_case` and build the change from that
   read — never from a case held in memory, and never from normalized analysis output, which has flattened
   the fields you are about to write back.
2. **Change only what the path is for.** Each path's field limits are in its section. A contradiction fix
   never touches links or priority; an orphan repair never touches text.
3. **Never remove a requirement link** from `refs` or the requirement field unless the user explicitly asked
   for that removal on the `edits` path — then set `allow_unlink` on that case only, and name the IDs leaving
   in the confirmation. The server blocks every other removal.
4. **Add a link to both fields, every time.** A requirement ID goes into `refs` **and** the requirement field
   (`test_cases.authoring.requirement_field`), for the reasons in `draft-test-cases` step 3: a case linked in
   `refs` alone comes back an orphan, and its requirement reads untested. The requirement field gets
   requirement IDs only; match the delimiter already in the case, else the one the corpus uses. Only link IDs
   that exist in a freshly fetched requirement set — never one the user or a model typed from memory.
5. **Steps are a whole list.** `steps_separated` replaces every step. To change one step, send the full list
   with only that step changed, copying the others verbatim from the fresh read.
6. **Pass `requirement_field` on every call**, so the server protects it like `refs`. A path that writes
   links (`orphans`, `scenarios`, an `edits` request that adds a link) **stops** when it is `null` or unset —
   `/bc:setup` records `null` when it could not find the field, and writing links without it creates exactly
   the orphan this skill exists to prevent. A path that changes only text uses `custom_jama_req_id` when the
   setting is absent, the same fallback the TestRail provider reads with.
7. **Never change a case's template or folder.** The tool refuses both; they belong in TestRail's own UI.

## Plan, confirm, apply

Every path ends the same way:

1. **Plan.** Call `testrail_update_cases` with `dry_run: true`, the batch, and `requirement_field`. Show the
   result as a plan — per case: its id and title, then each field as before → after (quote short values in
   full; for long text show the changed sentence; for steps show only the steps that differ), followed by its
   `warnings`. List `unchanged`, `blocked`, and `failed` cases under their own headings:

   ```
   Would update 3 test cases in TestRail

     C1234  Sync retries on network loss
       Step 2 expected:  "Retries every 10 seconds"  →  "Retries every 3 seconds"
     C1240  Offline banner clears
       custom_jama_req_id:  (empty)  →  "CWA-REQ-86"
     C1251  Rate limiting
       preconditions: + "Platform: iOS only"

     Unchanged: C1236 (already matches)
     Blocked:   C1239 — would remove CWA-REQ-85 from refs
   ```

2. **Confirm once** for the batch — or per case where the path says so. Nothing is written without an
   explicit yes in this session.
3. **Apply.** Call `testrail_update_cases` with `dry_run: false` and the **same** entries, adding each
   case's `expected_updated_on` from the plan. If someone edits a case between the plan and the yes, the
   server blocks it instead of overwriting their change — report it, and offer to re-plan that case.
4. **Report** from the tool's response: `updated` (with links), `unchanged`, `blocked` with the reason and
   what to do, `failed` verbatim, and — loudest of all — any `dropped_fields`, `stopped`, and
   `not_attempted`. A dropped field means TestRail accepted the request and discarded the value; with the
   requirement field that is a silent coverage failure, so name the field, point at
   `testrail_get_case_fields`, and do not retry the batch blind.
5. **Undo.** Each updated case's `changes[*].before` is its previous value. Say so in the report: asking to
   revert in the same session re-applies those values through the `edits` path, with `expected_updated_on`
   set to the `updated_on` this update returned. TestRail also keeps the case's history.

## Path `edits` — what the user asked for (`/bc:ask`)

Input: the user's request, naming cases by id (`C1234`, `TC-1234`, `1234`) — or a folder or feature, which the
caller resolves to a case list through `fetch-test-cases` and shows before planning.

- **Data.** Read every target case fresh. When the request adds or replaces a requirement link, also fetch
  requirements narrowed to those IDs through `fetch-requirements`, and refuse any ID that does not exist.
  Print each attempt as a `Fetch:` line. An edit is not an analysis: it reads exactly what it changes and
  what it must validate, nothing more.
- **Translate the request into fields**, changing only the words the user asked about and keeping the rest
  verbatim. When the request is ambiguous — which step, which of two cases with the same title — ask rather
  than guess.
- **Links** follow rules 3 and 4: an added ID goes into both fields. A removal or replacement needs the user
  to have said so; then set `allow_unlink` on that case and repeat the leaving IDs in the confirmation.
- Any field the tool accepts may change on this path, because the user named it.

## Path `orphans` — repair links (`/bc:coverage`)

Input: the orphan cases the coverage run found (empty `linked_ids`), with their `raw_fields.refs`, and the
requirement set that run fetched. Classify each orphan:

| Kind | Looks like | Repair | Confirmation |
|---|---|---|---|
| **Stranded link** | `refs` names requirement IDs that exist in the requirement set; the requirement field lacks them | copy those IDs into the requirement field, after anything already there | once for the batch — this is mechanical |
| **Unlinked** | no requirement ID in either field | propose a link with `find-related-requirements`, passing the case title, preconditions, and steps as the description; when a match is accepted, add it to both fields | **per case** — a proposed link is a judgement call |
| **Unreadable** | the requirement field holds text that parses to no requirement ID | none — report the value so someone fixes it by hand | — |

Rules for this path:

- **Links only.** Never touch a title, step, or priority here.
- **Only IDs from the fetched requirement set.** A token in `refs` that is not a requirement — a Jira key,
  an unknown ID — is never copied into the requirement field.
- **Propose only on real evidence.** Show the requirement, the keywords that matched, and the case text they
  matched in. When no requirement is a strong match, say so and propose nothing; the case may test something
  the requirement set does not describe, or may be obsolete. Both are a person's call.
- **Repair before drafting.** A requirement named in a stranded link is not untested — its case lost its
  link. The coverage agent leaves those requirements out of its gap-fill offer, so a duplicate case is never
  drafted for them.

## Path `contradictions` — make a case agree with its requirement (`/bc:contradiction`)

Input: findings of types 1, 2, 4, or 7 whose **Fix** line names the test case side, each with the case id, the
step or field in conflict, and the requirement text it conflicts with.

- **Only the case side.** When a finding's fix is the requirement, or it needs a decision, propose no case
  change. Say that requirements live in Jama or the Excel export, and that this plugin never writes them.
- **Minimal edit.** Change only the conflicting precondition, step, or expected result, so it states what the
  requirement states. Quote the requirement next to the before → after. Leave every other step as it is.
- **Never on this path:** links, title (unless the title itself states the contradicted value), type,
  priority, or estimate.
- **Board findings and requirement-versus-requirement findings** (types 3 and 5) never become case updates.
- The user picks which findings to apply: `all`, `pick some`, or `no`.

## Path `scenarios` — refresh V&V cases (`/bc:vnv-sprint-prep` phase 7)

Input: a story at `ready-for-test-case-creation` whose approved scenarios were revised after its cases were
written, its progress-log entry, and its existing cases (found by `testrail_get_cases_by_refs` on the dev
key, confirmed by reading `refs`).

1. **Map cases to scenarios.** Use the log's `testrail_cases` (`scenario` → `case_id`) first. Cases written
   before that field existed map by the provenance line in their preconditions — `(scenario S1)` — then by
   exact title, platform prefix included. A case that maps to nothing is reported, never touched.
2. **Per scenario:**

   | Situation | Action |
   |---|---|
   | Its case is **untouched** since the plugin wrote it | Regenerate the case's fields with `draft-test-cases` step 3 (title with platform prefix, preconditions with the provenance line, `steps_separated`) and update with `expected_updated_on` = the case's `written_on` from the log — or, for a case with no `written_on`, its own `created_on`. |
   | Its case was **edited** by someone since | Never overwrite. The server blocks it on `expected_updated_on`; report it as `edited since generated — review against the revised scenario`, and print the revised scenario. |
   | It has **no case** | A new scenario. Hand it back to `draft-test-cases` (refresh mode) to create. |
   | A case maps to a scenario that **no longer exists** | Report `no longer an approved scenario — retire it in TestRail if obsolete`. Never delete. |

   "Untouched" is decided by TestRail, not guessed: `updated_on` still equal to what the plugin recorded when
   it wrote the case. The V&V team expands these skeletons by hand, and overwriting that work would be the
   worst thing this path could do.
3. **Links.** Build `refs` and the requirement field as **everything already on the case plus** the IDs the
   revised scenario newly traces to — never the scenario's list alone. A requirement the scenario no longer
   traces to stays linked and is flagged in the report, so someone can unlink it deliberately. Sending a
   shorter list would not remove it anyway: the server blocks the whole update, content included.
4. **Record** in the progress log, per updated case, the new `written_on` (the `updated_on` the update
   returned), plus `cases_updated_at` (ISO 8601 UTC) on the story. Record only what TestRail returned.
5. Under `--apply`, the phase-7 confirmation already covers these updates — show them in the same batch as
   the creates, and comment the refreshed links onto the V&V ticket together with any new cases.

## Output

Return the tool's response — `updated`, `unchanged`, `blocked`, `failed`, `not_attempted`, `stopped` — plus
the path, and on the `scenarios` path the per-story `edited`, `new`, `retired`, and `unlink_flagged` lists the
caller prints in its status table.

## Error Handling

- **`requirement_field` null on a link-writing path** → stop before planning; direct to `/bc:setup`.
- **Case not found (400)** → report it under `failed`; the rest of the batch continues.
- **`blocked` on a conflict** → someone edited the case after it was read. Offer to re-plan it from a fresh
  read; never re-send with the guard removed.
- **`blocked` on an unlink** → the change would drop a requirement link. On the `edits` path, ask whether the
  user meant to remove it; on every other path, drop that change and report it.
- **`dropped_fields` / `stopped`** → the update reached TestRail but a field did not stick. Report the field
  and every `not_attempted` case; do not retry until the field name is fixed.
- **403** → the TestRail account can read but not edit cases. Say so; the role needs case edit permission.
