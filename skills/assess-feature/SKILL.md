---
description: Generate and grade a short assessment for one feature using its requirements and test cases. Cites requirement IDs in every answer. Adaptive remediation kicks in when the user gets multiple questions wrong. Records results to the progress log.
---

# Assess Feature — Skill

Test the user's understanding of one feature with 3–5 multiple-choice questions, grade each answer with citations, and adaptively remediate when the user is missing concepts.

## When to Use

Invoked by the onboarding agent after `demo-feature` returns control with `current_phase = "assessment"`. Returns control to the agent when the assessment is complete (so the agent can mark the feature done and move to the next).

## Inputs

- The feature plan object (`{ name, requirement_ids, test_case_ids, steps[] }`)
- Optional: missed questions from the previous feature's assessment (for spaced-repetition re-quiz)
- The mode (`"interactive"` runs the full assessment; `"quick"` runs 2 questions only; `"review"` skips assessment)

## Question Generation

### Source priority

Generate questions from, in order of preference:

1. **Negative test cases** — these expose edge cases users actually fail on (e.g., what happens when validation rejects input). Strongest signal.
2. **Acceptance criteria in requirement descriptions** — concrete behavioral rules.
3. **Positive test case expected results** — what the system *should* do in the happy path.

Do NOT generate questions from:
- Requirement metadata (status, ownership)
- UI styling details unless explicitly required
- General programming knowledge

### Quantity

| Mode | Questions per feature |
|------|------------------------|
| `interactive` | 3–5 (target 4) |
| `quick` | 2 |
| `review` | skip |

If the previous feature had missed questions, **re-quiz one** at the start of the current assessment as the first question. Mark it as a re-quiz in the question header (e.g., "*Returning to a question you missed last time...*").

### Question shape

Each question has:

- A clear, single-concept question (no compound questions)
- 3–4 options (avoid 2 — too easy; avoid 6+ — too noisy)
- One correct answer
- A free-text escape: option `"I'm not sure / explain again"` is always available
- A citation: which requirement and/or test case the answer comes from

Synthetic example:

```
Q1 of 4 — <Feature Name>

A clinician enters a patient ID with a typo and presses Save. What does the system do?

  a) Saves the record with the invalid ID and flags it for review
  b) Rejects the input and shows a validation error
  c) Auto-corrects the typo using fuzzy matching
  d) I'm not sure / explain again

Your answer:
```

## Grading

When the user answers:

### If correct

```
✅ Correct.

Per CWA-REQ-85, step 3 of TC-1234: the system must reject invalid patient IDs
with a validation error before the Save action completes.
```

Record: `correct++`. Move to the next question.

### If incorrect

```
❌ Not quite.

The correct answer is (b) — Rejects the input and shows a validation error.

This is required by CWA-REQ-85, validated by TC-1234. Your answer (<a>) would
allow corrupted records into the system.
```

Record: append to `missed_questions` with `{q, correct_answer, user_answer, cite}`. Move to the next question.

### If "I'm not sure" or free-text

Treat this as a clarification request, not a wrong answer:

```
No problem — let me re-explain.

<Re-explain the concept in different words, citing the source requirement and test case.>

Same question — give it another try?
```

Re-ask the same question. Allow a maximum of two clarification rounds per question; if still stuck, treat as wrong and move on.

## Adaptive Remediation

Track the running tally of wrong answers within this feature's assessment:

| Trigger | Behavior |
|---------|----------|
| 1st wrong answer | Normal — give correct answer + citation, advance |
| 2 wrong in a row | Pause and ask: *"Want me to re-explain the section about &lt;weak topic&gt; before we keep going?"* — user chooses `yes` (do mini re-demo) or `no` (continue) |
| 3 wrong total OR 3 wrong in a row | **Mandatory mini re-demo** of the specific weak step, then resume the assessment from where it stopped. Cap at one mandatory re-demo per feature. |
| Still &lt;50% after one mandatory re-demo | Stop the assessment. Tell the user honestly: *"You're scoring below 50% on this feature. I'd suggest taking a break or doing some hands-on time before revisiting. We can move to the next feature now, or pause here."* Set `features[i].status = "needs_review"` either way and let the user decide whether to continue or pause. |

The "weak topic" for a re-demo is the `requirement_id` cited by the most recent missed question (or the most-cited requirement_id across all missed questions if there's a tie).

## Output to Log

When the assessment completes (or is stopped early), write to `features[i].assessment`:

```json
{
  "questions_asked": 4,
  "correct": 3,
  "missed_questions": [
    {
      "q": "<question text>",
      "correct_answer": "<text>",
      "user_answer": "<text>",
      "cite": "<requirement id>"
    }
  ]
}
```

Then set:
- `features[i].status = "completed"` if `correct / questions_asked >= 0.75` and no `needs_review` flag was set
- `features[i].status = "needs_review"` otherwise
- `features[i].completed_at = <ISO 8601 UTC timestamp>`
- `current_phase = "completed"` (the agent will then increment `current_feature_index` and reset `current_step_index = 0`, `current_phase = "demo"`)

Append a summary to `onboarding-notes.md`.

## Final Summary (per feature)

```
Assessment complete: <Feature Name>
  Score: 3/4 correct (75%)
  Status: completed

  ✓ Q1 — correct
  ✗ Q2 — answered (a), correct was (b) [REQ-XYZ]
  ✓ Q3 — correct
  ✓ Q4 — correct

[Press Enter to continue to the next feature, or type "pause" to save and exit.]
```

For features marked `needs_review`, add a one-line follow-up:

```
This feature is marked "needs_review" — it'll show up in /bc:onboarding status
and you can revisit any time with /bc:onboarding feature "<Feature Name>".
```

## Guidelines

- **Always cite.** Every grading response must reference a specific requirement ID and ideally a test case ID.
- **Don't generate trick questions.** Negative paths are fair game (that's the point), but trivia about the source data isn't.
- **Don't penalize honest "not sure" answers.** Re-explain, don't punish.
- **Be honest about scores.** If the user is struggling, tell them — don't push them through with false-positive completions.
- **Spaced repetition is light.** One re-quiz at the start of the next feature is enough; don't carry every miss forever.
- **Free-text escape is always present.** Users should never feel forced to guess.
