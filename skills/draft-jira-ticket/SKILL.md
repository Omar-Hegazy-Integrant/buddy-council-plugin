---
description: Generate a structured Jira ticket draft with summary, description, and acceptance criteria from a validated ticket description and user answers.
---

# Draft Jira Ticket — Skill

Generate a well-structured Jira ticket draft including summary (title), description, and acceptance criteria after validation and gap-filling.

## When to Use

In the ticket validation workflow, after contradictions are resolved and gaps are filled. This skill synthesizes all information into a draft ready for Jira creation.

## Input

- `original_description`: The initial ticket description provided by the user
- `contradiction_resolutions`: Array of user answers to contradiction questions (if any)
- `gap_answers`: Array of user answers to gap-filling questions (if any)
- `related_requirements`: Array of related requirement objects (for traceability and context)

## Output Fields

Generate three fields matching Jira's expected structure:

### 1. Summary (Title)
- **Format**: Single line, concise, action-oriented
- **Length**: 50-100 characters (Jira best practice)
- **Pattern**: `[Action] [Object] [Context]`
  - Action: Add, Update, Remove, Fix, Implement, Create, etc.
  - Object: What feature/component is being changed
  - Context: Where or for what purpose

**Examples:**
- ✅ "Add real-time heart rate monitor to vitals dashboard"
- ✅ "Update patient search to support wildcard matching"
- ✅ "Fix logout button visibility for mobile users"
- ❌ "Heart rate monitoring" — too vague, not action-oriented
- ❌ "As a clinician, I want to see real-time heart rate data on the vitals dashboard" — too long, user story format belongs in description

### 2. Description
- **Format**: Markdown (Jira-compatible)
- **Structure**:
  1. **Brief Description** (2-4 sentences, no header): What the ticket aims to do and why
  2. **Acceptance Criteria**: Bulleted list under `## Acceptance Criteria` heading
  3. **Related Requirements**: Bulleted list under `## Related Requirements` heading (if any requirements were matched)

**Jira Markdown Support:**
- Headings: `## Heading`
- Bold: `*bold text*`
- Italic: `_italic text_`
- Code: `{{monospace}}`
- Bullets: `- item` or `* item`
- Numbered lists: `1. item`
- Line breaks: Two spaces + newline, or blank line between paragraphs

### 3. Acceptance Criteria
- **Format**: Bulleted list embedded in the description under `## Acceptance Criteria` heading
- **Content**: Specific, testable conditions derived from:
  - Explicit criteria in original description
  - Answers to gap-filling questions
  - Constraints from related requirements (if mentioned)
- **Pattern**: Each criterion should be independently verifiable
  - ✅ "Button displays 'Sign In' text"
  - ✅ "Clicking button redirects to /login page"
  - ✅ "Button is disabled when user is already authenticated"
  - ❌ "Button works correctly" — too vague

## Draft Generation Approach

### Step 1: Generate Summary
1. Identify the main action from the description (add, update, remove, etc.)
2. Extract the feature/component being changed
3. Optionally add context (where or for whom)
4. Keep to 50-100 characters

### Step 2: Structure the Description

The description should have a **clean, simple structure** with exactly three sections:

**Section 1: Description (no header)**  
A brief paragraph (2-4 sentences) explaining what the ticket aims to do. Synthesize:
- The main feature/change being implemented
- Why it's needed (if context is important)
- What will be different after this is done

Example:
```markdown
This ticket adds a real-time heart rate monitor widget to the patient vitals dashboard. The widget will display current heart rate in BPM and update automatically every 2 seconds when a device is connected. It will show connection status and handle disconnection scenarios gracefully.
```

**Section 2: Acceptance Criteria**  
A bulleted list under the `## Acceptance Criteria` heading. Compile all testable conditions from:
- Explicit criteria in original description
- Answers to gap-filling questions
- Constraints from related requirements (if applicable)

```markdown
## Acceptance Criteria
- Widget displays current heart rate in BPM
- Widget updates every 2 seconds when device is connected
- Widget shows "Connecting..." during initial connection
- Widget shows "Disconnected" when device is unplugged
- Widget shows "No device connected" when no device is available
- Widget is positioned in top-right corner of vitals dashboard
- Heart rate value is displayed in large, readable font (minimum 24px)
```

**Section 3: Related Requirements**  
A bulleted list under the `## Related Requirements` heading. Link to requirement IDs for traceability:

```markdown
## Related Requirements
- CWA-REQ-85: Patient monitoring view displays vital signs with refresh rate <= 3 seconds
- CWA-REQ-120: All patient data must be encrypted at rest and in transit
```

**Important**: Do NOT include these sections:
- ❌ "Context" or "Problem" section
- ❌ "Solution" section
- ❌ "Contradictions Resolved" section
- ❌ "Notes" section
- ❌ Any implementation details or technical notes

Keep the description focused, concise, and actionable.

### Step 3: Format for Jira
Convert the structured description to Jira-compatible markdown:
- Use `##` for headings
- Use `- ` for bullets
- Use `*bold*` for emphasis
- Avoid HTML tags (Jira uses ADF, not HTML)

## Output Format

Return a JSON object with three fields:

```json
{
  "summary": "Add real-time heart rate monitor to vitals dashboard",
  "description": "This ticket adds a real-time heart rate monitor widget to the patient vitals dashboard. The widget will display current heart rate in BPM and update automatically every 2 seconds when a device is connected. It will show connection status and handle disconnection scenarios gracefully.\n\n## Acceptance Criteria\n- Widget displays current heart rate in BPM\n- Widget updates every 2 seconds when device is connected\n- Widget shows 'Connecting...' during initial connection\n- Widget shows 'Disconnected' when device is unplugged\n- Widget shows 'No device connected' when no device is available\n- Widget is positioned in top-right corner of vitals dashboard\n- Heart rate value is displayed in large, readable font (minimum 24px)\n\n## Related Requirements\n- CWA-REQ-85: Patient monitoring view displays vital signs with refresh rate <= 3 seconds\n- CWA-REQ-120: All patient data must be encrypted at rest and in transit",
  "acceptance_criteria": [
    "Widget displays current heart rate in BPM",
    "Widget updates every 2 seconds when device is connected",
    "Widget shows 'Connecting...' during initial connection",
    "Widget shows 'Disconnected' when device is unplugged",
    "Widget shows 'No device connected' when no device is available",
    "Widget is positioned in top-right corner of vitals dashboard",
    "Heart rate value is displayed in large, readable font (minimum 24px)"
  ]
}
```

**Note**: `acceptance_criteria` is a separate array for easy reference, but it's also embedded in the `description` field under the "## Acceptance Criteria" heading.

## Best Practices

### Make Acceptance Criteria SMART
- **Specific**: Not "works well" but "displays within 2 seconds"
- **Measurable**: Include numbers, states, or observable outcomes
- **Achievable**: Realistic given the scope
- **Relevant**: Directly related to the ticket's purpose
- **Testable**: QA can verify each criterion independently

### Include Edge Cases
Based on gap answers, ensure edge cases are covered:
- Empty states (no data, no connection)
- Error states (API failure, timeout)
- Loading states (connecting, fetching data)
- Boundary conditions (max/min values, limits)

### Link Requirements for Traceability
Always include a "Related Requirements" section if any requirements were identified:
- Helps reviewers understand the context
- Enables traceability for compliance/audit
- Makes it easier to update requirements later if implementation differs

### Keep Summary Action-Oriented
The summary should clearly state what will change, not just describe a problem:
- ✅ "Add export button to reports page"
- ❌ "Users can't export reports" — problem statement, not action

## Example

**Inputs:**
- **Original description**: "Add a real-time heart rate monitor to the patient vitals dashboard. The monitor should update when connected to a device."
- **Gap answers**:
  - Q: "What specific vitals should the monitor display?" → A: "Heart rate in BPM only"
  - Q: "Where should the monitor be positioned?" → A: "Top-right corner of the dashboard"
  - Q: "How should it indicate when a device is NOT connected?" → A: "Show 'No device connected' message"
  - Q: "What refresh rate?" → A: "Every 2 seconds"
  - Q: "What happens during disconnections?" → A: "Show 'Disconnected' status with last known value"
- **Related requirements**: CWA-REQ-85 (refresh rate <= 3 seconds), CWA-REQ-120 (encryption)

**Expected Output:**
```json
{
  "summary": "Add real-time heart rate monitor to vitals dashboard",
  "description": "This ticket adds a real-time heart rate monitor widget to the patient vitals dashboard. The widget will display current heart rate in BPM and update automatically every 2 seconds when a device is connected. It will show connection status (connecting, connected, disconnected) and handle device disconnection gracefully.\n\n## Acceptance Criteria\n- Widget displays current heart rate in BPM\n- Widget updates every 2 seconds when device is connected (meets CWA-REQ-85 requirement of <= 3 seconds)\n- Widget is positioned in top-right corner of vitals dashboard\n- Widget shows 'Connecting...' during initial device connection\n- Widget shows 'Disconnected' with last known value when device is unplugged\n- Widget shows 'No device connected' when no device is available\n- Heart rate data is encrypted in transit (per CWA-REQ-120)\n\n## Related Requirements\n- CWA-REQ-85: Patient monitoring view displays vital signs with refresh rate <= 3 seconds\n- CWA-REQ-120: All patient data must be encrypted at rest and in transit",
  "acceptance_criteria": [
    "Widget displays current heart rate in BPM",
    "Widget updates every 2 seconds when device is connected (meets CWA-REQ-85 requirement of <= 3 seconds)",
    "Widget is positioned in top-right corner of vitals dashboard",
    "Widget shows 'Connecting...' during initial device connection",
    "Widget shows 'Disconnected' with last known value when device is unplugged",
    "Widget shows 'No device connected' when no device is available",
    "Heart rate data is encrypted in transit (per CWA-REQ-120)"
  ]
}
```

## Edge Cases

### Minimal Description with No Gaps
If the original description is already complete and no gaps were found:
- Generate summary from the description
- Use the description as-is (possibly reformatted)
- Extract acceptance criteria if present, or synthesize from description

### Contradiction Resolutions Changed the Scope
If user chose to update the ticket during contradiction resolution:
- Use the UPDATED description (post-contradiction resolution)
- Briefly mention the adjustment in the description paragraph if it's significant:
  ```markdown
  This ticket adds a real-time heart rate monitor to the patient vitals dashboard. The monitor updates every 2 seconds (adjusted from the original 10-second proposal to comply with CWA-REQ-85). It displays current heart rate in BPM and handles connection states gracefully.
  ```
- Or reference it in acceptance criteria:
  ```markdown
  - Widget updates every 2 seconds when device is connected (per CWA-REQ-85 requirement of <= 3 seconds)
  ```

### No Related Requirements
If no requirements were found or matched:
- Omit the "## Related Requirements" section entirely
- Still generate complete acceptance criteria based on gap answers
