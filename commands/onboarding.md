# /bc:onboarding — Interactive Onboarding Journey

Walk a new team member through the product feature-by-feature, pairing requirements with test cases. Each feature includes a guided demo, step-by-step confirmations, and an assessment with citations to requirement IDs. Progress is logged to a project-local file so the journey can be resumed across sessions.

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

## Arguments: $ARGUMENTS

## Execution

1. First, verify that `${CLAUDE_PLUGIN_ROOT}/config/sources.json` exists. If not, tell the user:
   > Configuration not found. Please run `/bc:setup` to configure your data sources first.

2. Determine the user's project root. This is the **current working directory** where the user invoked Claude Code, not the plugin directory. The progress log lives there at `.buddy-council/onboarding-progress.json`.

3. Parse the arguments:
   - No args or `start` → start new journey, or resume if a log already exists (ask the user)
   - `resume` → load log and continue from `current_feature_index` / `current_step_index`
   - `status` → load log and print summary; do not proceed
   - `feature "<name>"` → run only that feature in isolation (do not mutate main progress)
   - `reset` → archive existing log to `onboarding-progress.<timestamp>.json` and start fresh
   - `--quick` / `--review` → mode flags passed to the agent

4. Invoke the onboarding agent by following the instructions in `${CLAUDE_PLUGIN_ROOT}/agents/onboarding-agent.agent.md`, passing the parsed arguments and the user's project root.

## What the Agent Does

- **First run**: builds an ordered onboarding plan grouping requirements + test cases by feature, writes initial progress log, ensures `.buddy-council/` is in `.gitignore`, and starts feature 1.
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

Both are gitignored.

## Important

- The progress log lives in the **user's project root**, not in the plugin directory. This keeps the log close to the codebase the user is learning, and supports future commands that want to relate code changes to learning history.
- The `feature_order` array in `config/sources.json` (optional) controls feature ordering. If absent, features appear in the order their requirements appear in the source. The chosen order is persisted in the log on first run.
- `feature "<name>"` mode is for re-learning one feature later; it does NOT mutate the main progress log.
