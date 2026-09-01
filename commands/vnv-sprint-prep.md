---
description: V&V (Validation and Verification) sprint preparation — check cross-platform parity on the dev board, clone sprint stories to the V&V board, validate them against requirements and test cases, drive them through scenario review, and write the approved scenarios into TestRail as test cases.
---

# /bc:vnv-sprint-prep — V&V Sprint Preparation

Walk a sprint's dev stories through the V&V team's pipeline: platform-parity check → clone to the V&V
board → validate against requirements and test cases → high-level scenarios → reviewer approval → TestRail
test cases.

The workflow is **resumable**. Run it once to start a sprint, then re-run it as the dev team answers your
questions and the reviewer approves your scenarios — each run picks up where the last left off and advances
only the tickets whose blocking condition has cleared.

## Usage

```
/bc:vnv-sprint-prep                          # Dry run — report what would happen, write nothing
/bc:vnv-sprint-prep --apply                  # Perform the writes, after one batch confirmation per phase
/bc:vnv-sprint-prep --board <url>            # Use/record a different V&V board for this project
/bc:vnv-sprint-prep PROJ-123                 # Restrict the run to one story (and its platform counterpart)
```

## Arguments: $ARGUMENTS

- **Scope** (optional): a story key to restrict the run to. Default: every story in the dev board's current sprint.
- **Flags**:
  - `--apply`: perform the writes. **Without it nothing is created, commented, or labelled** — the run reports the full plan and stops.
  - `--board <url>`: supply the V&V board URL. Required on the first run if `/bc:setup` never recorded one; otherwise it overrides and updates the stored value.

## Safety Posture

This workflow writes to tickets other people own. Three rules hold at all times:

1. **Dry run is the default.** A plain `/bc:vnv-sprint-prep` performs reads only. Every intended write is printed as a plan line.
2. **`--apply` still confirms.** Each phase (clone / comment / label / description) shows its batch and asks once before executing. Never execute a phase the user has not confirmed in this session.
3. **Reads are always allowed.** Re-reading dev-story comments to see whether your questions were answered happens on every run, dry or not — that is how the workflow knows a ticket can advance.

## Execution

1. Verify `.buddy-council/sources.json` exists. If not: tell the user to run `/bc:setup` first.
2. Verify the dev board is configured and not `pending` (`jira.board`, `jira.pending`). If it is pending, stop — the workflow reads the dev sprint and cannot proceed without a verified board.
3. Resolve the V&V board: `--board` if given, else `jira.vnv_board`. If neither exists, ask for the URL now, parse it, and record it. **Refuse only if it resolves to the same board *id* as the dev board.** A shared project is supported — resolve `vnv_board.discriminator` (what the V&V board's filter keys off) so clones land on the right board.
4. Follow `${CLAUDE_PLUGIN_ROOT}/agents/vnv-sprint-prep-agent.agent.md`, passing the scope and flags.

## The Seven Phases

| # | Phase | What it does | Writes? |
|---|---|---|---|
| 1 | **Board** | Accept or confirm the V&V board link; validate it is a different board from the dev board, and resolve the clone discriminator when they share a project | config only |
| 2 | **Sprint + parity** | Fetch the dev board's current-sprint stories; group by the `OS` field; flag any story whose platform counterpart is missing or unlinked | no |
| 3 | **Clone** | Create a matching story on the V&V board for each in-scope dev story, carrying summary, description, a `src-<DEV-KEY>` back-reference, and label `pending-validation` | yes |
| 4 | **Validate** | Check each clone against requirements and test cases for gaps, conflicts, and contradictions. Concerns → comment on the **original dev story**, label the clone `pending-questions` | yes |
| 5 | **Scenarios** | Write high-level scenarios into the V&V ticket's description; label `pending-scenario-validation` | yes |
| 6 | **Ready** | Once the designated reviewer approves in a comment, relabel `ready-for-test-case-creation` | yes |
| 7 | **Test cases** | Turn each approved ticket's scenarios into TestRail cases in the configured suite folder, with `refs` pointing back at the requirements and the dev story, then comment the case links onto the V&V ticket | yes |

Phase 7 writes **skeleton** cases — title, preconditions, one step per Given/When/Then — for the V&V team to
expand. It creates nothing from a scenario that traces to no requirement, and nothing from a scenario phase 5
already marked as covered by an existing case. It skips itself with a visible line when
`test_cases.authoring` is unconfigured, because the template alone decides which body fields a case has and
guessing it writes the steps into a field nobody reads. Run `/bc:setup` to fill it in.

## Resuming

Re-running is the normal mode of operation. On every run, before doing new work, the agent re-reads the
tickets already in flight and advances the ones that can move:

- **`pending-questions`** → re-read the comments on the **original dev story**. If the dev team answered the
  concerns raised, fold the answers in, re-validate, and if nothing is outstanding, move the ticket on to
  phase 5. If the questions are still open, report how long they have been waiting and leave it.
- **`pending-scenario-validation`** → re-read the comments on the **V&V ticket**. If the designated reviewer
  approved, relabel to `ready-for-test-case-creation`.
- **`ready-for-test-case-creation`** → terminal for this workflow. Reported in the summary, never re-processed.

- **`pending-validation`** → cloned but never reviewed, because a previous run stopped there or was a dry
  run. Goes into validation on this run.

### The label convention

Every V&V ticket carries **exactly one** of these four at any time — they are mutually exclusive, and each
transition removes the previous one rather than stacking:

| Label | Meaning | Set at |
|---|---|---|
| `pending-validation` | Cloned, not yet reviewed | Phase 3 |
| `pending-questions` | Concerns posted on the dev story, waiting on answers | Phase 4 |
| `pending-scenario-validation` | Scenarios written, waiting on the reviewer | Phase 5 |
| `ready-for-test-case-creation` | Approved — terminal | Phase 6 |

Rename any of them in `jira.vnv_workflow.labels` in `.buddy-council/sources.json`; the workflow reads the
config, never the hardcoded strings. Clones additionally carry a permanent `src-<DEV-KEY>` label and their
platform (`ios` / `android`) — those are not pipeline states and are never stripped, and neither is any
label a human adds.

State lives in `.buddy-council/vnv-progress.json` in your project root, alongside the onboarding log. Jira
labels remain the source of truth — if someone changes a label in Jira, the next run follows Jira, not the
local file, and repairs the file. A ticket found with no pipeline label is treated as `pending-validation`;
one found with several is repaired to the furthest-along, and the repair is reported.

## Known Platform Limits

One thing `mcp-atlassian` cannot do, surfaced here so the output is never misleading:

- **It cannot attach files.** `jira_download_attachments` has no upload counterpart, so scenarios go into the
  V&V ticket's description rather than an attached `.md`.

Issue links **do** work (`jira_create_issue_link`), so each clone is linked to its dev story as well as
carrying the `src-<DEV-KEY>` label. If a site restricts link creation the clone still succeeds and the run
reports which pairs are label-only, so someone can link them in the UI in one pass.

## Error Handling

- **Dev board pending or unconfigured** → stop; run `/bc:setup`.
- **V&V board missing** → ask for the URL, or accept `--board`.
- **V&V board id equals the dev board id** → stop and explain; this is a configuration error, not something to work around. A shared *project* is supported — see the discriminator below.
- **V&V board shares a project with the dev board and no discriminator is known** → derive one by sampling both boards, or ask. If it stays unresolved, warn that clones may surface on the dev board and let the user decide.
- **No `OS` field on the project** → phase 2 falls back to the title prefix and says so loudly; parity results are marked lower-confidence.
- **No active sprint** → report it and stop. There is nothing to clone.
- **401 / 403 from Atlassian** → 401 means the token in `~/.buddy-council/atlassian.env` is wrong or expired (re-run `/bc:setup`); 403 means the account lacks permission on that project, or an IP allowlist is blocking the request.
