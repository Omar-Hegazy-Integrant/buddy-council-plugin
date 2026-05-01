---
description: Read, write, initialize, and resume the onboarding progress log stored in the user's project at .buddy-council/onboarding-progress.json. Source of truth for cross-session onboarding journeys.
---

# Manage Progress Log — Skill

Manage the onboarding progress log that lives in the user's project root. The log is the source of truth for resuming an onboarding journey across sessions.

## When to Use

Invoked by the onboarding agent at every state transition: starting a journey, advancing a step, completing a feature, recording assessment results, pausing, and resuming.

## File Locations

The log lives in the **user's project root** (where Claude Code was invoked), not the plugin directory:

```
<user-project>/.buddy-council/
├── onboarding-progress.json    # structured state — source of truth for resume
└── onboarding-notes.md         # append-only human-readable transcript
```

Both files are gitignored.

## Operations

### 1. Initialize the Log Directory

Before any read or write:

1. Determine the user's project root (current working directory at command invocation).
2. If `<user-project>/.buddy-council/` does not exist, create it.
3. Ensure `.buddy-council/` is in `<user-project>/.gitignore`:
   - If `.gitignore` does not exist, create it with `.buddy-council/` as the only entry.
   - If `.gitignore` exists and does not already contain `.buddy-council/` or `.buddy-council`, append `.buddy-council/` on a new line, after asking the user once for confirmation.
   - If the project is not a git repository, skip the gitignore step silently.

### 2. Initialize the Progress File

When starting a new journey (no existing `onboarding-progress.json`, or after `reset`):

Write an initial JSON document with this exact shape:

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

### 3. Read the Log (Resume)

When resuming an existing journey:

1. Read `<user-project>/.buddy-council/onboarding-progress.json`.
2. Validate the JSON structure. If parsing fails, do NOT delete the file — tell the user the log appears corrupted and offer two choices: archive and restart, or open the file for manual repair.
3. Return the parsed object. The agent will use `current_feature_index`, `current_step_index`, and `current_phase` to determine where to resume.

### 4. Write/Update the Log

After every meaningful state change (step advance, feature completion, assessment recorded, note added):

1. Update `last_updated` to the current ISO 8601 UTC timestamp.
2. Update the relevant fields (`current_step_index`, `current_phase`, `features[i].status`, etc.).
3. Write the entire object back to `onboarding-progress.json` atomically:
   - Write to `onboarding-progress.json.tmp` first
   - Rename to `onboarding-progress.json` (atomic on POSIX)
4. Never partially update. Always write the complete document.

### 5. Append to Notes File

For human-readable transcript entries, append to `onboarding-notes.md`:

```markdown
## <ISO 8601 timestamp> — <Feature name> — <event>

<short description of what happened: a step demoed, a question answered, a note added>

---
```

Use this for: feature start, feature completion, assessment summary, user notes, skipped steps. Do NOT append for every minor state change — keep the file readable.

### 6. Archive on Reset

When the user runs `/bc:onboarding reset`:

1. Rename existing `onboarding-progress.json` to `onboarding-progress.<YYYY-MM-DDTHHMMSSZ>.json`.
2. Rename existing `onboarding-notes.md` to `onboarding-notes.<YYYY-MM-DDTHHMMSSZ>.md`.
3. Confirm with the user before deleting; archived files remain in `.buddy-council/` until manually removed.

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
| `.buddy-council/` cannot be created (permission denied) | Tell the user the path failed and ask whether to fall back to `~/.buddy-council/<project-name>/` |
| `onboarding-progress.json` is corrupted | Do not delete. Offer archive-and-restart or manual repair |
| User runs `resume` but no log exists | Tell the user there's no saved progress, offer to start a new journey |
| User runs `start` but a log exists | Ask whether to resume, reset, or cancel |
| Project is not a git repo | Skip `.gitignore` mutation silently; still create the log directory |

## Guidelines

- **Never block the user's progress on a write failure.** If a write fails, surface the error and let the user decide whether to retry or pause.
- **Always use atomic writes** — losing the log mid-write would force the user to restart.
- **Don't overwrite user notes** — `user_notes` is append-only.
- **Treat the JSON as the source of truth.** The Markdown notes file is auxiliary and human-readable; never read it back to determine state.
- **Preserve archived files.** Reset archives but does not delete.
