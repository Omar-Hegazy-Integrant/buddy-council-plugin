---
description: Identify gaps and missing information in a ticket description to ensure it is complete and actionable before creating the ticket.
---

# Detect Ticket Gaps — Skill

Analyze a ticket description to identify missing information, unclear specifications, and gaps that would make the ticket difficult to implement or verify.

## When to Use

In the ticket validation workflow, after detecting and resolving contradictions. This skill ensures the ticket is complete and actionable before generating the final draft.

## Input

- `ticket_description`: The raw ticket description text (possibly updated after contradiction resolution)
- `related_requirements`: Array of requirement objects to provide context

## Gap Types to Detect

Based on user preferences (from planning), focus on:
1. **Missing acceptance criteria**
2. **Unclear success conditions**

Future enhancements can add: technical details, UI/UX specs, performance requirements, security considerations, error handling.

### 1. Missing Acceptance Criteria

Acceptance criteria define when the ticket is "done" — specific, testable conditions that must be met.

**Check for:**
- Does the ticket have explicit acceptance criteria listed?
- If criteria exist, are they testable and specific?
- Are edge cases or error conditions covered?

**Examples of gaps:**
- ❌ "Add login button" — no criteria for what "done" means
- ❌ "Button should work properly" — vague, not testable
- ✅ "Button displays 'Sign In' text, redirects to /login on click, is disabled when user is logged in" — specific and testable

**How to detect:**
- Look for keywords: "acceptance criteria", "definition of done", "success criteria", bulleted lists of conditions
- If missing or vague, flag as gap
- Suggest specific questions to fill the gap (see Output Format below)

### 2. Unclear Success Conditions

Success conditions define WHEN and HOW to verify the ticket is implemented correctly.

**Check for:**
- Are there measurable outcomes? (e.g., "displays within 2 seconds", "handles 1000 records")
- Is the expected behavior clear under different conditions? (logged in vs logged out, empty state vs populated, error states)
- Are validation steps described? (how will QA test this?)

**Examples of gaps:**
- ❌ "Update dashboard to be faster" — no measure of "faster"
- ❌ "Add error handling" — no clarity on which errors or how to handle them
- ✅ "If API call fails, display error message 'Unable to load data. Please try again.' and log error to console" — clear and specific

**How to detect:**
- Look for ambiguous terms: "better", "improved", "faster", "properly", "correctly"
- Check if behavior is described for edge cases (empty data, errors, loading states)
- If unclear, flag and suggest questions to clarify

## Analysis Approach

For each ticket description:

1. **Parse structure:**
   - Is there a clear problem statement? ("As a user, I want...", "Currently X happens, but it should...")
   - Is there a solution described?
   - Are there explicit acceptance criteria sections?

2. **Check for acceptance criteria:**
   - Look for sections labeled "Acceptance Criteria", "Definition of Done", "Success Criteria"
   - Look for bulleted or numbered lists of conditions
   - If missing or fewer than 2 criteria, flag as gap

3. **Check for clarity:**
   - Scan for ambiguous terms (see list above)
   - Check if behavior is described for multiple scenarios (happy path, error path, edge cases)
   - Check for measurable outcomes (numbers, times, specific UI elements)

4. **Cross-reference with requirements:**
   - If related requirements specify acceptance criteria or constraints, check if the ticket mentions them
   - Flag if the ticket is missing criteria that the requirement emphasizes

5. **Generate suggested questions:**
   - For each gap, formulate a specific question that would fill it
   - Questions should be answerable and actionable (see examples below)

## What NOT to Flag as Gaps

- **Implementation details**: Ticket doesn't need to specify HOW to implement (that's for the developer), just WHAT and WHEN it's done
- **Technical architecture**: Unless it's a technical ticket, no need to specify which classes/functions to use
- **Minor stylistic choices**: Color, exact wording, icon choice (unless those are part of requirements)

## Output Format

Return a JSON array of gaps with suggested questions (empty if none found):

```json
[
  {
    "gap_type": "missing_acceptance_criteria",
    "severity": "HIGH",
    "explanation": "The ticket describes adding a login button but does not specify what 'done' looks like. No acceptance criteria listed.",
    "suggested_questions": [
      "What text should the login button display?",
      "Where should the button redirect users when clicked?",
      "Should the button be visible when a user is already logged in?",
      "Are there any error states to handle (e.g., if login service is unavailable)?"
    ],
    "allow_skip": true
  },
  {
    "gap_type": "unclear_success_condition",
    "severity": "MEDIUM",
    "explanation": "The ticket mentions 'updates every 2 seconds' but doesn't clarify what happens if the device is disconnected or if updates fail.",
    "suggested_questions": [
      "What should happen if the device disconnects during monitoring?",
      "Should there be a visual indicator when updates are not being received?",
      "How should the system handle failed update attempts (retry, show error, etc.)?"
    ],
    "allow_skip": true
  }
]
```

## Severity Classification

| Severity | Criteria | Allow Skip? |
|----------|----------|-------------|
| **HIGH** | Missing acceptance criteria entirely, or no way to verify "done" | Yes — user can skip all questions |
| **MEDIUM** | Unclear success conditions, ambiguous terms, missing edge case handling | Yes — user can skip if they plan to clarify later |
| **LOW** | Minor gaps or nice-to-haves | Yes — user can skip |

**Note**: All gap-filling questions can be skipped. The user may choose to fill gaps later or create the ticket with minimal information.

## Edge Cases

### Ticket Already Has Acceptance Criteria
If the ticket includes a well-structured "Acceptance Criteria" section with 3+ specific, testable conditions:
- Do NOT flag "missing acceptance criteria"
- Still check for clarity gaps (ambiguous terms, missing edge cases)

### Generic Ticket Types
Some ticket types are inherently less specific (e.g., "Research X", "Investigate Y", "Spike: Evaluate Z"):
- Adjust expectations: research tickets don't need implementation acceptance criteria
- Flag if missing research success criteria (e.g., "Document findings in wiki", "Present options to team")

### Requirements Are Too Vague
If related requirements are themselves vague or incomplete:
- Do NOT penalize the ticket for that
- Flag gaps that are NOT covered by requirements
- Suggest questions that would make the ticket more actionable even if requirements are unclear

## Question Formulation Guidelines

Good questions are:
- **Specific**: "What text should the button display?" not "How should it look?"
- **Answerable**: User can provide a concrete answer
- **Actionable**: Answer directly translates to acceptance criteria
- **Multiple-choice when possible**: Offer options to make answering easier

**Examples:**

❌ Bad: "Is this feature important?"  
✅ Good: "Should the button be visible to guest users, or only logged-in users?"

❌ Bad: "How should it work?"  
✅ Good: "When the API call fails, should the system: (a) display an error message, (b) retry automatically, (c) fail silently, or (d) log the user out?"

## Example

**Ticket Description:**
```
Add a real-time heart rate monitor to the patient vitals dashboard. The monitor should update when connected to a device.
```

**Related Requirements:**
- CWA-REQ-85: "Patient monitoring view displays vital signs with refresh rate <= 3 seconds"

**Expected Output:**
```json
[
  {
    "gap_type": "missing_acceptance_criteria",
    "severity": "HIGH",
    "explanation": "The ticket describes the feature but does not specify concrete acceptance criteria. It's unclear what 'done' looks like.",
    "suggested_questions": [
      "What specific vitals should the heart rate monitor display (BPM only, or include other metrics)?",
      "Where on the dashboard should the monitor be positioned?",
      "How should the monitor indicate when a device is NOT connected?",
      "Should there be any visual alerts (e.g., if heart rate goes outside normal range)?"
    ],
    "allow_skip": true
  },
  {
    "gap_type": "unclear_success_condition",
    "severity": "MEDIUM",
    "explanation": "The ticket says 'update when connected' but doesn't specify the refresh rate or what happens during disconnections.",
    "suggested_questions": [
      "What refresh rate should be used for updates? (Note: CWA-REQ-85 requires <= 3 seconds)",
      "What should happen if the device disconnects during monitoring? (hide monitor, show 'disconnected' status, show last known value?)",
      "Should there be a visual indicator for 'connecting', 'connected', and 'disconnected' states?"
    ],
    "allow_skip": true
  }
]
```

The agent would then use `vscode_askQuestions` to present these questions to the user, collect answers, and incorporate them into the ticket draft.
