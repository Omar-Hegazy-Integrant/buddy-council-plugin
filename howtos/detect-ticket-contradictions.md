# Detect Ticket Contradictions

The rules for whether a ticket contradicts a requirement — and whether a ticket already on the board will break a test
case. Every command that judges a ticket uses them, so a ticket gets the same answer from each.

## Input

| Caller | The ticket | Compare it with |
|---|---|---|
| `/bc:validate`, `/bc:vnv-sprint-prep` | `ticket_description`: the text the user typed for a new ticket, or a sprint story's | `related_requirements`: the requirements `howtos/find-related-requirements.md` found, each with a relevance score |
| `/bc:contradiction` | a **board ticket**: its key, title, link, and the text that says what it should do, from the session's board read | the requirements and test cases its slice lists under it: the ones it links to, and the ones code found on its subject |

## Contradiction Types to Detect

### 1. Behavioral Conflicts
The ticket proposes behavior that contradicts what requirements specify.

**Example:**
- **Requirement (CWA-REQ-85)**: "Patient vitals shall refresh every 3 seconds or less"
- **Ticket description**: "Add a vitals display that updates every 10 seconds"
- **Conflict**: The ticket proposes a 10-second refresh rate, violating the 3-second requirement

### 2. Constraint Violations
The ticket violates explicit constraints in requirements (timing, capacity, security, etc.).

**Example:**
- **Requirement (CWA-REQ-120)**: "All patient data must be encrypted at rest and in transit"
- **Ticket description**: "Store vitals data in local browser storage for quick access"
- **Conflict**: Browser localStorage is not encrypted, violating the security requirement

### 3. Feature Removal or Modification
The ticket proposes removing or significantly changing functionality that requirements mandate.

**Example:**
- **Requirement (CWA-REQ-45)**: "System shall support manual logout via button in header"
- **Ticket description**: "Remove logout button from header, auto-logout only"
- **Conflict**: The ticket removes a required feature

### 4. Scope Creep Beyond Requirements
The ticket adds functionality that contradicts existing architectural or design requirements.

**Example:**
- **Requirement (CWA-REQ-200)**: "Monitoring view is read-only, no data modification allowed"
- **Ticket description**: "Add edit button to monitoring view for updating patient info"
- **Conflict**: The ticket adds editing capability to a read-only view

### 5. Incompatible Implementation Details
The ticket proposes a specific implementation approach that conflicts with existing requirements.

**Example:**
- **Requirement (CWA-REQ-150)**: "System shall use REST API for all backend communication"
- **Ticket description**: "Implement real-time vitals using WebSocket polling"
- **Conflict**: WebSocket conflicts with the REST API requirement (unless WebSocket is for real-time data only)

### 6. Priority or Timing Conflicts
The ticket has dependencies or timing expectations that conflict with requirements.

**Example:**
- **Requirement (CWA-REQ-75)**: "Audit logging must be implemented before any patient data features"
- **Ticket description**: "Add patient search feature (no mention of audit logging)"
- **Conflict**: The ticket proposes a patient data feature without addressing the prerequisite

### 7. Breaking a Test Case — board tickets only
The ticket changes, removes, or replaces behavior a test case checks, so the test case fails once the ticket ships.

**Example** *(made-up)*:
- **Test case (TC-55)**: "Tap Help. Expected: the help page opens"
- **Ticket (PROJ-12)**: "Remove the Help button from the settings screen"
- **Conflict**: Once the ticket ships, TC-55 taps a button that is gone

## Analysis Approach

For each related requirement (starting with highest relevance scores):

1. **Extract key constraints from the requirement:**
   - Explicit behaviors ("shall", "must", "will")
   - Constraints (time limits, capacity, security, access control)
   - Mandatory features or components
   - Architectural decisions or patterns

2. **Extract proposed changes from the ticket:**
   - What feature/behavior is being added/modified/removed?
   - What technical approach is mentioned?
   - What constraints or timing is mentioned?
   - What dependencies exist?

3. **Compare ticket against requirement:**
   - Does the ticket propose opposite behavior?
   - Does the ticket violate any constraints?
   - Does the ticket remove required functionality?
   - Does the ticket add functionality to a restricted area?
   - Does the ticket use an incompatible implementation approach?

   **A board ticket against each test case:** does a step or an expected result rely on behavior the ticket
   changes, removes, or replaces?

4. **Weight by relevance:**
   - Conflicts with high-relevance requirements (score >= 0.8) are CRITICAL
   - Conflicts with medium-relevance requirements (score 0.5-0.8) are HIGH
   - Conflicts with low-relevance requirements (score < 0.5) are MEDIUM

   A board ticket's requirements come without scores: one the ticket links to counts as high relevance, and one
   code found on its subject as medium — high when its words govern exactly what the ticket changes.

## What NOT to Flag as Contradictions

- **Extensions that don't conflict**: Ticket adds a feature not mentioned in requirements (that's normal for new work)
- **Implementation details**: Requirement says "display data", ticket says "display in a table" (table is a specific implementation, not a contradiction)
- **Refinements**: Ticket makes something more specific without contradicting it
- **Stylistic differences**: Different wording that means the same thing
- **Test cases the ticket leaves alone**: A test case on the same subject that checks behavior the ticket doesn't change

## Output Format

### `/bc:validate` and `/bc:vnv-sprint-prep`

Return a JSON array of contradictions (empty if none found):

```json
[
  {
    "severity": "CRITICAL",
    "requirement_id": "CWA-REQ-85",
    "requirement_quote": "Patient vitals shall refresh every 3 seconds or less",
    "ticket_quote": "Add a vitals display that updates every 10 seconds",
    "explanation": "The ticket proposes a 10-second refresh rate, but the requirement mandates 3 seconds or less. This would violate the real-time monitoring requirement.",
    "recommendation": "Change the ticket to specify a refresh rate of 3 seconds or less, or discuss with stakeholders if the 3-second requirement can be relaxed.",
    "relevance_score": 0.95
  },
  {
    "severity": "HIGH",
    "requirement_id": "CWA-REQ-120",
    "requirement_quote": "All patient data must be encrypted at rest and in transit",
    "ticket_quote": "Store vitals data in local browser storage for quick access",
    "explanation": "Browser localStorage does not provide encryption by default. Storing patient data there would violate the encryption requirement.",
    "recommendation": "Update the ticket to use encrypted storage (e.g., IndexedDB with encryption layer, or server-side caching with encrypted transport).",
    "relevance_score": 0.72
  }
]
```

### `/bc:contradiction`

Save each conflict as a finding of the run — `howtos/detect-contradictions.md` → *A finding* and *Saving*:

- **`items`** — the ticket's key with its words, and each requirement or test case with its own.
- **`severity`** — the level from *Severity Classification*, in lower case.
- **`why`** — the explanation, in one sentence.
- **`fix`** — the ticket's key, when it should change to agree with a requirement; `"decide"`, when the ticket may
  mean the requirement itself is changing; or the test cases the ticket breaks — one ID, or a list — to update when
  it ships. The ticket hasn't shipped and may still change, so code never offers these as test case fixes.

*(made-up)*

```json
{
  "items": [
    {"id": "PROJ-12", "quote": "Remove the Help button from the settings screen"},
    {"id": "TC-55", "quote": "Tap Help"},
    {"id": "TC-56", "quote": "Tap Help"}
  ],
  "severity": "high",
  "why": "Both test cases tap the Help button the ticket removes, so they fail once it ships.",
  "fix": ["TC-55", "TC-56"]
}
```

## Severity Classification

| Severity | Criteria |
|----------|----------|
| **CRITICAL** | Ticket directly violates a high-relevance requirement (score >= 0.8) — opposite behavior, explicit constraint violation |
| **HIGH** | Ticket conflicts with medium-relevance requirement (score 0.5-0.8), or removes required functionality |
| **MEDIUM** | Ticket conflicts with low-relevance requirement (score < 0.5), or potential conflict that needs human judgment |

A test case a board ticket breaks is **HIGH** when the ticket changes exactly what it checks, and **MEDIUM** when it
may.

## Edge Cases

### Ambiguous Requirements
If a requirement is vague or open to interpretation, and the ticket's approach is one reasonable interpretation:
- Do NOT flag as contradiction
- May flag as a "gap" (missing clarification) in the gap detection how-to

### Partial Conflicts
If the ticket description conflicts with PART of a requirement but aligns with another part:
- Flag it as MEDIUM severity
- Explain which parts conflict and which align
- Let the user decide whether to proceed

### No High-Relevance Requirements
If no related requirements have relevance score >= 0.5:
- Skip contradiction detection (too speculative)
- Return empty array
- The calling agent should note: "No strongly related requirements found — proceeding without contradiction check"

## Example

**Ticket Description:**
```
Add a real-time heart rate monitor to the patient vitals dashboard. The monitor should update every 10 seconds when connected to a device. Store the last 100 readings in browser localStorage for offline access.
```

**Related Requirements:**
1. CWA-REQ-85 (score: 0.95): "Patient monitoring view displays vital signs with refresh rate <= 3 seconds"
2. CWA-REQ-120 (score: 0.72): "All patient data must be encrypted at rest and in transit"
3. CWA-REQ-100 (score: 0.65): "Dashboard supports real-time updates via WebSocket connection"

**Expected Output:**
```json
[
  {
    "severity": "CRITICAL",
    "requirement_id": "CWA-REQ-85",
    "requirement_quote": "Patient monitoring view displays vital signs with refresh rate <= 3 seconds",
    "ticket_quote": "The monitor should update every 10 seconds",
    "explanation": "The ticket proposes a 10-second refresh rate, but the requirement mandates 3 seconds or less. This would violate the real-time monitoring requirement.",
    "recommendation": "Change the ticket to specify a refresh rate of 3 seconds or less.",
    "relevance_score": 0.95
  },
  {
    "severity": "HIGH",
    "requirement_id": "CWA-REQ-120",
    "requirement_quote": "All patient data must be encrypted at rest and in transit",
    "ticket_quote": "Store the last 100 readings in browser localStorage",
    "explanation": "Browser localStorage does not provide encryption by default. Storing patient data (heart rate readings) there would violate the encryption requirement.",
    "recommendation": "Use encrypted storage (IndexedDB with encryption, or server-side caching with encrypted transport), or remove offline access feature.",
    "relevance_score": 0.72
  }
]
```

No contradiction flagged for CWA-REQ-100 because "real-time updates" in the ticket aligns with "real-time updates via WebSocket" (implementation detail, not a conflict).
