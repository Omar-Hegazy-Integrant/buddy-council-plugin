---
name: contradiction
description: Detect contradictions, inconsistencies, and alignment gaps between requirements and test cases.
disable-model-invocation: true
---

# /bc:contradiction — Detect Contradictions Between Requirements and Test Cases

Find where requirements and the test cases that verify them disagree, and where in-flight board tickets
disagree with the requirements.

**Plugin root:** two folders above this skill's base directory, which is `<plugin root>/skills/contradiction/`. Every file these instructions name — `howtos/…`, `scripts/…` — is a path from the plugin root, and so is every file those files name.

## Scope

The text the user typed after the command:

- a requirement ID, such as `CWA-REQ-85` → that requirement, its test cases, and the other requirements in its
  feature;
- a feature name or folder path, such as `"Patient Monitoring"` → everything in that feature;
- nothing → the whole project, feature by feature.

`/bc:ask` runs these steps too, with the scope taken from the user's question.

## Steps

### 1. Load the settings

Follow `howtos/load-settings.md` with `--need test_cases --reads board`.

**Done when** you have the project folder, the board's state, and the `lines` to print — or the how-to has
ended the command.

### 2. Collect the run

Follow `howtos/collect-artifacts.md` with `command: contradiction` and the scope. The data collector reads every
requirement and test case into a run folder, and code links and counts them, so the run is the data: read it,
take its counts as given, and quote its text exactly.

**Done when** you hold `run_dir`, you have printed the run's `lines`, and every missing source has the user's
answer.

### 3. Read the board

When the board is ready, follow `howtos/fetch-board-issues.md` once, for the whole scope. Read it for every
scope, a single requirement included: scope narrows the read, never skips it. Keep each ticket's `id`, `title`,
`feature`, `status`, `linked_ids`, link, and the text that says what it should do.

- **The board isn't ready** (not used in this project, or not verified yet) → print its line from step 1 and go
  on with no tickets. The run stays complete.
- **A ready board fails** → it is a missing source: name it and its error, and ask whether to continue without
  it.

**Done when** you have printed the board's `Fetch:` line or its skip line.

### 4. Check that there is something to compare

Use the run summary's `counts`:

- **`requirements` is 0** → the scope matched nothing. Relay the summary's `warnings`, which name the features
  that exist, and ask whether to continue or stop.
- **`test_cases_in_scope` is 0** → name the likely cause, a scope name that matches no test case or an error from
  the test case tool, and ask whether to continue with requirements only or stop.

One feature with no test cases, while others have some, is a missing-alignment finding for that feature: carry
on.

**Done when** both counts are above zero, or the user chose to continue.

### 5. Check each feature

In the order of the summary's `features`, for each feature:

1. Run `python3 '<plugin root>/scripts/bc_run.py' summary --run '<run_dir>' --feature '<feature>'`. Read every
   bundle part it lists, in order, one part per view, and each linked doc you have not read yet in this run.
2. Apply `howtos/detect-contradictions.md`, types 1–4, 6, and 7. When the scope is one feature, check type 5
   inside it too.
3. Check each board ticket that touches this feature, because its `linked_ids` name one of the feature's
   requirements or its `feature` matches. Treat the ticket as proposed behavior, check it against the feature's
   requirements with the same types, and name the ticket and its link in each finding.
4. Note each requirement as `{id, feature, title, key_constraint}`, its core constraint in one line, for step 6.

**Done when** every feature in `features` has its findings and its one-line constraints.

### 6. Check across features

Skip this step when the scope is one feature.

From the one-line constraints, list the pairs of requirements in different features whose constraints may
clash (type 5). Read each pair in full with
`python3 '<plugin root>/scripts/bc_run.py' show --run '<run_dir>' --ids '<A>,<B>'`, and keep a pair only when
the full text confirms the clash.

**Done when** every listed pair is confirmed or dropped.

### 7. Report

Write one report in the format of `howtos/detect-contradictions.md` → *Report*, holding every finding from steps
5 and 6. Mark it **PARTIAL**, naming the missing source, when the user chose to continue without one. With no
findings, say so, with the scope and the counts.

**Done when** every finding appears once, in one report.

### 8. Offer to fix test cases

When at least one finding's **Fix** line names a test case, end with one question:

```
3 findings are test cases that contradict their requirement. I can update those cases to match —
you'll see every change before it's written. Want me to? (all / pick some / no)
```

- Offer only the findings whose **Fix** line names a test case. Findings that fix the requirement or need a
  decision, board ticket findings, and requirement-versus-requirement findings (types 3 and 5) stay in the
  report: this plugin changes no requirement.
- `pick some` → list the offered findings with numbers, and let the user choose.
- On a yes, follow `howtos/update-test-cases.md` → *Path `contradictions`*, passing the chosen findings, each
  with the requirement text it conflicts with. Write only what the user chose.
- With nothing to offer, ask nothing: an offer on every run teaches people to skip it.

**Done when** the user has answered, or there was nothing to offer.

## Follow-up questions

- **About this run** ("explain the critical one", "what does CWA-REQ-85 say?") → answer from the findings and
  the run: the bundles hold the exact text, and `bc_run.py show --ids` prints one record with its link.
- **About a requirement or feature outside this run** → say it wasn't part of this run, and offer a run for it:
  "CWA-REQ-48 wasn't part of this run, which covered Patient Monitoring. Want me to run
  `/bc:contradiction CWA-REQ-48`?"

## When something fails

- **No connector tools at all** → the plugin's connectors didn't start, usually because `uv` isn't installed
  (README → *Troubleshooting*). The user installs it and restarts the assistant.
- **A tool refuses or can't be reached** → follow `howtos/load-settings.md` → *When a tool says no*.

## What this command changes

Nothing, unless the user says yes in step 8, and then only the conflicting text of the test cases they chose,
through `howtos/update-test-cases.md`. Requirements and a test case's requirement links stay as they are. New
test cases come from `/bc:coverage`; general questions go to `/bc:ask`.
