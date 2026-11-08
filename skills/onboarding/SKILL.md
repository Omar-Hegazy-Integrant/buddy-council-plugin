---
name: onboarding
description: Walk a new team member through the product feature-by-feature with paced demos, optional code mapping, and an assessment. Resumes across sessions.
disable-model-invocation: true
---

# /bc:onboarding — Interactive Onboarding Journey

Walk a new team member through the product feature-by-feature, pairing requirements with test cases. Each feature includes a guided demo, step-by-step confirmations, and an assessment with citations to requirement IDs. Progress is logged to a project-local file so the journey can be resumed across sessions.

**Plugin root:** two folders above this skill's base directory, which is `<plugin root>/skills/onboarding/`. Every file these instructions name — `howtos/…`, `providers/…`, `scripts/…`, `com.github.copilot/agents/…` — is a path from the plugin root, and so is every file those files name.

## Usage

```
/bc:onboarding                        # Start a new onboarding journey (or resume if log exists)
/bc:onboarding resume                 # Explicitly resume from the saved progress log
/bc:onboarding status                 # Show current progress, completed features, missed questions
/bc:onboarding feature "Login"        # Re-learn a single feature (does not affect main progress)
/bc:onboarding reset                  # Archive existing log and start fresh
/bc:onboarding --quick                # One summary per feature instead of step-by-step
/bc:onboarding --review               # Recap of completed features only, no new content
```

## Arguments

The text the user typed after the command, if any. Step 3 below parses it.

## Execution

1. Follow `howtos/load-settings.md` with `--reads test_cases`: it finds the project folder — the nearest folder at or above the current one that holds `.buddy-council/` — and stops when the settings aren't ready. The progress log lives there, at `.buddy-council/onboarding-progress.json`.

2. Parse the arguments:
   - No args or `start` → start new journey, or resume if a log already exists (ask the user)
   - `resume` → load log and continue from `current_feature_index` / `current_step_index`
   - `status` → load log and print summary; do not proceed
   - `feature "<name>"` → run only that feature in isolation (do not mutate main progress)
   - `reset` → archive existing log to `onboarding-progress.<timestamp>.json` and start fresh
   - `--quick` / `--review` → mode flags passed to the agent

3. Run the onboarding agent's steps in this session: follow `com.github.copilot/agents/onboarding-agent.agent.md`, passing the parsed arguments and the project folder.

## What the Agent Does

- **First run**: builds an ordered onboarding plan grouping requirements + test cases by feature, writes initial progress log, ensures `.buddy-council/` is ignored via the repo-local `.git/info/exclude` (zero git footprint — no `.gitignore` change), and starts feature 1.
- **Each feature**: explains the purpose, walks through small steps with do/don't pairs from test cases, waits for the user's `next` / `back` / `explain more` between steps, then runs an assessment with citations (e.g., *"per CWA-REQ-85, step 3"*).
- **Adaptive remediation**: if the user gets multiple questions wrong, pauses and re-explains weak concepts before continuing. Caps re-demos to avoid infinite loops.
- **Context discipline**: at feature boundaries, suggests `/clear && /bc:onboarding resume`. Mid-feature, if the conversation grows long (many user questions), implicitly invokes `/strategic-compact` to keep working memory tight while preserving the log as source-of-truth.
- **Resume**: reads the log, fetches only the current and next feature's data, picks up exactly where the user left off.

## Inline Controls (during a feature)

The user can type any of these at any prompt:

| Input | Effect |
|-------|--------|
| `next` / Enter | Advance to next step |
| `back` | Repeat the previous step |
| `explain more` | Get a deeper explanation of the current step |
| `skip step` | Skip the current step (logged as skipped) |
| `skip feature` | Mark feature as skipped, jump to next feature |
| `quiz me now` | Jump to the assessment phase early |
| `note: <text>` | Append a personal note to the progress log |
| `pause` | Save state and exit cleanly |

## Progress Log

Two files are written to `<user-project>/.buddy-council/`:

- `onboarding-progress.json` — structured state (features completed, current position, assessment scores, missed questions). Source of truth for resume.
- `onboarding-notes.md` — append-only human-readable transcript of what was covered.

Both are kept out of git via the repo-local `.git/info/exclude` (no change to the tracked `.gitignore`).

## Important

- The progress log lives in the **user's project root**, not in the plugin directory. This keeps the log close to the codebase the user is learning, and supports future commands that want to relate code changes to learning history.
- The `feature_order` array in `.buddy-council/sources.json` (optional) controls feature ordering. If absent, features appear in the order their requirements appear in the source. The chosen order is persisted in the log on first run.
- `feature "<name>"` mode is for re-learning one feature later; it does NOT mutate the main progress log.
