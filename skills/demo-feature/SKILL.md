---
description: Walk the user through one feature step by step with do/don't pairs from test cases, waiting for confirmation between steps. Honors interactive controls (next, back, skip, explain more, note, pause). Updates the progress log after every advance.
---

# Demo Feature — Skill

Walk the user through one feature step-by-step. Each step covers one requirement and uses linked test cases as concrete do/don't examples. The user controls the pace.

## When to Use

Invoked by the onboarding agent for the `current_feature_index` feature, after the plan has been built and the log updated to mark the feature as `"in_progress"`. Returns control to the agent when the demo phase is complete (so the agent can move to the assessment phase).

## Inputs

- The feature plan object produced by `build-onboarding-plan` (`{ name, purpose, requirement_ids, test_case_ids, steps[] }`)
- The current `step_index` from the progress log (resume support — start from this step, not from 0)
- The mode (`"interactive"` for full pacing, `"quick"` for one summary, `"review"` for recap only)

## Phase 1 — Feature Introduction

Before the first step, present a short feature card:

```
─────────────────────────────────────────────
  Feature N of M:  <Feature Name>
─────────────────────────────────────────────

What this feature is for:
  <purpose statement from the plan, 1-2 sentences>

Source requirements: <REQ-001, REQ-002, REQ-003>
Test coverage: <TC-1001, TC-1002, ...>

I'll walk through this in <K> small steps. After each step, type:
  next         — continue
  back         — repeat last step
  explain more — deeper explanation
  skip step    — skip this one
  skip feature — jump to the next feature
  note: ...    — add a note to your log
  quiz me now  — jump straight to the assessment
  pause        — save and exit cleanly

Ready? (Press Enter or type "next")
```

Wait for confirmation before the first step.

## Phase 2 — Step Loop

For each step starting at the resume index:

### 2a. Present the step

```
Step <i+1>/<K> — <step title>
Source: <REQ-XYZ>

<plain-language explanation, 2-4 sentences>

✅ DO
  • <do rule 1>  (TC-1001)
  • <do rule 2>  (TC-1003)

❌ DON'T
  • <don't rule 1>  (TC-1002)
  • <don't rule 2>  (TC-1004)

[next / back / explain more / skip step / skip feature / note: ... / quiz me now / pause]
```

### 2b. Wait for user input

Honor the input semantics:

| Input | Behavior |
|-------|----------|
| `next` or empty Enter | Advance: `step_index++`, write log, present next step |
| `back` | If `step_index > 0`, decrement and re-present; else say "this is the first step" |
| `explain more` | Produce a deeper explanation citing the requirement description and 1-2 specific test case steps. Do not advance. |
| `skip step` | Mark this step skipped in `user_notes`, advance |
| `skip feature` | Mark `features[i].status = "skipped"`, return control to agent so it can move to next feature |
| `note: <text>` | Append `<text>` to `features[i].user_notes`, write log, ask again |
| `quiz me now` | Jump to assessment phase — return control to agent with phase signal |
| `pause` | Write log with current state, tell the user how to resume, exit cleanly |

After every advance, call `manage-progress-log` to write `current_step_index` and `last_updated`.

### 2c. After the last step

When `step_index == K` (all steps shown):

1. Update log: `current_phase = "assessment"`.
2. Tell the user:
   ```
   That's all <K> steps for <Feature Name>. Confirm you're ready for the assessment?
     yes — start the assessment
     review — show a quick recap of the feature first
     pause — save and exit
   ```
3. On `yes`, return control to the agent so it can invoke `assess-feature`.
4. On `review`, produce a one-page summary (just the do/don't bullets, no walkthrough) and re-ask.

## Quick Mode

If `mode == "quick"`:

- Skip Phase 1's full intro; present a one-screen summary of the entire feature with all do/don't pairs collapsed.
- Single confirmation gate: "Ready for the assessment?"
- Don't update `current_step_index` per step — just mark the demo phase complete.

## Review Mode

If `mode == "review"`:

- Don't run the step loop at all.
- Print a recap: feature name, purpose, requirements covered, do/don't summary.
- Skip the assessment entirely.
- Used by `/bc:onboarding --review`.

## Resume Behavior

When invoked with a non-zero `step_index`:

- Skip Phase 1's intro (the user has seen it).
- Print a one-line orienting message: `"Resuming <Feature Name> at step <i+1>/<K>."`
- Start the step loop at `step_index`.

## Error Handling

| Situation | Behavior |
|-----------|----------|
| User input is unrecognized | Ask the user to pick a known control; do not advance |
| Step has no linked test cases (no do/don't pairs) | Present the step with explanation only and a note: "no test cases linked to this requirement" |
| User stays silent on a prompt | Treat as no input; do not auto-advance |

## Guidelines

- **Stay short.** Each step should fit on one screen. If a requirement is too dense for one step, split it locally — don't force a wall of text.
- **Always cite.** Every step header lists its source requirement ID. Every do/don't bullet lists its source test case ID. Citations are non-negotiable.
- **Don't bullet-list everything.** Use the do/don't sections only where test cases actually provide concrete rules. If there are no test cases, present plain explanation.
- **Respect pacing.** Never advance without explicit user input.
- **Persist after every advance.** A user who quits mid-feature should be able to resume on the exact next step.
