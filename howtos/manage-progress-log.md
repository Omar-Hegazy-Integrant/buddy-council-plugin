# Manage Progress Log

Manage the onboarding progress log that lives in the project folder. The log is the source of truth for resuming an onboarding journey across sessions.

## When to Use

Invoked by the onboarding agent at every state transition: starting a journey, advancing a step, completing a feature, recording assessment results, pausing, and resuming.

## File Locations

The log lives in the **project folder** — the one `howtos/load-settings.md` found — not the plugin directory:

```
<project>/.buddy-council/
├── onboarding-progress.json    # structured state — source of truth for resume
└── onboarding-notes.md         # append-only human-readable transcript
```

## Operations

### 1. Saving

Every change to either file is one plain command through the plugin's run-folder script, which the bash hook
approves, so a save never prompts. The script keeps `.buddy-council/` out of git through the clone's own
`info/exclude`, writes atomically, and refuses to overwrite a log that isn't valid JSON:

```bash
python3 '<plugin root>/scripts/bc_run.py' progress --project '<project>' --log onboarding --patch '<json>'
python3 '<plugin root>/scripts/bc_run.py' note --project '<project>' --title '<feature> — <event>' --text '<what happened>'
python3 '<plugin root>/scripts/bc_run.py' archive --project '<project>' --log onboarding
```

`--patch` is JSON on one line, with every `'` written as `\u0027`. It merges into the log: `features` merge by
`name`, so send only the features that changed, each with its `name` and the fields that changed; anything else
replaces what was there, and a feature's own lists (`user_notes`, `requirement_ids`, `test_case_ids`) are replaced
whole, so send each one complete. The script stamps `last_updated`.

### 2. Initialize the Progress File

When starting a new journey (no existing `onboarding-progress.json`, or after `reset`), save this whole document as
the first patch:

```json
{
  "version": 1,
  "started_at": "<ISO 8601 UTC timestamp>",
  "last_updated": "<ISO 8601 UTC timestamp>",
  "user": "<email or username if known, else null>",
  "mode": "interactive",
  "feature_order": ["<feature 1>", "<feature 2>", "..."],
  "current_feature_index": 0,
  "current_step_index": 0,
  "current_phase": "demo",
  "features": [
    {
      "name": "<feature name>",
      "requirement_ids": ["<req id>"],
      "test_case_ids": ["<tc id>"],
      "status": "pending",
      "completed_at": null,
      "assessment": null,
      "user_notes": []
    }
  ]
}
```

Field constraints:

| Field | Type | Allowed values |
|-------|------|----------------|
| `version` | integer | `1` for now |
| `started_at`, `last_updated`, `completed_at` | string \| null | ISO 8601 UTC, e.g. `"2026-05-01T10:00:00Z"` |
| `mode` | string | `"interactive"`, `"quick"`, `"review"` |
| `current_phase` | string | `"demo"`, `"assessment"`, `"completed"` |
| `current_feature_index`, `current_step_index` | integer | zero-based |
| `features[].status` | string | `"pending"`, `"in_progress"`, `"completed"`, `"skipped"`, `"needs_review"` |
| `features[].assessment` | object \| null | see assessment schema below |

Assessment schema (when populated):

```json
{
  "questions_asked": 4,
  "correct": 3,
  "missed_questions": [
    {
      "q": "<question text>",
      "correct_answer": "<correct answer text>",
      "user_answer": "<what user answered>",
      "cite": "<requirement id, e.g. CWA-REQ-85>"
    }
  ]
}
```

Optional `code_mapping` schema (populated by `howtos/map-feature-to-code.md` when an onboarding session runs from inside a code repo):

```json
{
  "computed_at": "<ISO 8601 UTC>",
  "git_sha": "<full SHA at compute time, or null if not a git repo>",
  "files": [
    { "path": "src/features/x.tsx", "role": "<one-line description>" }
  ],
  "flow": "<prose narrative of how files communicate>",
  "requirement_locations": [
    { "req_id": "CWA-REQ-85", "files": [{ "path": "...", "lines": "42-78" }] }
  ],
  "notes": ["<observations or unlocated requirements>"]
}
```

`code_mapping` is **optional**. Absence means either the cwd wasn't a code repo on this run, the user disabled `project.enabled`, or the field hasn't been computed yet for that feature. The map-feature-to-code how-to manages all reads and writes — when updating the log, preserve `code_mapping` unchanged unless that how-to is the writer.

### 3. Read the Log (Resume)

When resuming an existing journey:

1. Read `<user-project>/.buddy-council/onboarding-progress.json`.
2. Validate the JSON structure. If parsing fails, do NOT delete the file — tell the user the log appears corrupted and offer two choices: archive and restart, or open the file for manual repair.
3. Return the parsed object. The agent will use `current_feature_index`, `current_step_index`, and `current_phase` to determine where to resume.

### 4. Update the Log

After every meaningful state change — a step advance, a feature completed, an assessment recorded, a note added —
save a patch with what changed: `current_step_index`, `current_phase`, the feature's `status`, and so on.

### 5. Append to Notes File

For the human-readable transcript, `note` appends one entry — `## <timestamp> — <title>`, the text, then `---`. Use
it for: feature start, feature completion, assessment summary, user notes, skipped steps — not every minor state
change, so the file stays readable.

### 6. Archive on Reset

When the user runs `/bc:onboarding reset`, `archive` renames `onboarding-progress.json` and `onboarding-notes.md` to
dated names (`onboarding-progress.<YYYY-MM-DDTHHMMSSZ>.json`); they stay in `.buddy-council/` until removed by hand.

### 7. Status Summary

For `/bc:onboarding status`, read the log and produce a summary:

```
Onboarding progress for <user>
Started: <relative time>, last updated: <relative time>

Features: X / Y completed
  ✓ <Feature 1> — completed (assessment: 4/4)
  ✓ <Feature 2> — completed (assessment: 2/4, needs_review)
  → <Feature 3> — in progress (step 3 of 7, phase: demo)
  · <Feature 4> — pending
  · <Feature 5> — pending

Open items:
  - 2 missed assessment questions to revisit
  - 1 user note: "want to revisit lockout behavior"
```

## Error Handling

| Situation | Behavior |
|-----------|----------|
| A save fails | Show the script's `error`, and let the user decide whether to retry or pause |
| `onboarding-progress.json` is corrupted | The script leaves it as it is. Offer archive-and-restart or manual repair |
| User runs `resume` but no log exists | Tell the user there's no saved progress, offer to start a new journey |
| User runs `start` but a log exists | Ask whether to resume, reset, or cancel |

## Guidelines

- **Never block the user's progress on a write failure.** If a write fails, surface the error and let the user decide whether to retry or pause.
- **Keep user notes** — `user_notes` only grows: send the whole list, with the new note added.
- **Treat the JSON as the source of truth.** The Markdown notes file is auxiliary and human-readable; never read it back to determine state.
- **Preserve archived files.** Reset archives but does not delete.
