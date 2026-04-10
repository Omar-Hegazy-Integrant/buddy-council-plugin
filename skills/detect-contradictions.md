# Detect Contradictions — Skill

Analyze normalized requirements and test cases to find contradictions, inconsistencies, and missing alignment.

## When to Use

After artifacts have been fetched and normalized. This is the core analysis skill invoked by the contradiction agent.

## Contradiction Types

Detect these seven types of issues:

### 1. Direct Conflicts
A requirement states X, but a linked test case validates the opposite of X.
- Example: Requirement says "timeout after 30 seconds", test case expects "timeout after 60 seconds"

### 2. Behavioral Conflicts
Different expected behaviors for the same trigger or condition across linked artifacts.
- Example: Requirement says "display warning dialog", test expects "silent log entry"

### 3. Scope Overlaps
Two requirements cover overlapping functionality with incompatible constraints.
- Example: REQ-10 says "support up to 100 users", REQ-45 says "system handles unlimited concurrent sessions"

### 4. Test Case vs Requirement Conflicts
Test steps or expected results contradict the requirement they claim to verify.
- Example: Test case linked to a "read-only" requirement includes steps that modify data

### 5. Cross-Feature Tensions
Requirements in different features that create conflicting expectations.
- Example: Feature A requires "auto-logout after 5 min idle", Feature B requires "maintain persistent session during monitoring"

### 6. Missing Alignment
- Requirements with no linked test cases (untested requirements)
- Test cases with no linked requirements (orphan tests)
- Test cases whose steps don't actually verify the linked requirement

### 7. Temporal / State Conflicts
Conflicting expectations about system state or timing.
- Example: Requirement assumes data is available immediately, test case assumes async loading with delay

## Analysis Approach

### For Scoped Analysis (single requirement or feature)

1. Take the target requirement(s) and all their linked test cases
2. Also include sibling requirements in the same feature (for scope overlap detection)
3. For each requirement-test case pair:
   - Compare the requirement's description against each test step and expected result
   - Look for semantic mismatches, not just keyword differences
4. For each pair of sibling requirements:
   - Check for conflicting constraints, overlapping scope, or incompatible expectations
5. Flag any requirements with empty `linked_ids` as missing alignment

### For Full Analysis (all requirements)

Process feature by feature to manage context:
1. Group requirements and test cases by feature
2. Analyze within each feature (types 1-4, 6-7)
3. Then analyze across features for cross-feature tensions (type 5)
4. Aggregate findings

### Batching for Large Datasets

If the total artifact count exceeds ~50 items:
- Process one feature at a time
- Keep a running summary of key constraints from each feature for cross-feature analysis
- Never try to fit all artifacts into a single analysis pass

## Output Format

Produce a human-readable report structured as:

```markdown
# Contradiction Analysis Report

## Summary
- **Scope**: [what was analyzed]
- **Requirements analyzed**: [count]
- **Test cases analyzed**: [count]
- **Issues found**: [count by severity]

## Critical Issues

### [CRITICAL] Direct conflict between REQ-85 and TC-1234
**Requirement (CWA-REQ-85)**: "The system shall display patient vitals with a refresh rate of 3 seconds"
**Test Case (TC-1234), Step 3**: Expects "vitals update every 10 seconds"
**Explanation**: The test case validates a 10-second refresh interval, but the requirement specifies 3 seconds. The test would pass for a system that violates the requirement.
**Recommendation**: Update TC-1234 Step 3 to expect a 3-second refresh rate.

## High Issues
...

## Medium Issues
...

## Low Issues
...

## Missing Alignment
### Requirements without test coverage:
- CWA-REQ-92: "System shall support audit logging" — no linked test cases

### Orphan test cases:
- TC-1456: "Verify backup schedule" — no linked requirement
```

## Severity Classification

| Severity | Criteria |
|----------|----------|
| **CRITICAL** | Direct conflict — test validates the opposite of what requirement states |
| **HIGH** | Behavioral conflict — test and requirement describe meaningfully different behavior |
| **MEDIUM** | Scope overlap or cross-feature tension — potential conflict that needs human judgment |
| **LOW** | Missing alignment — untested requirement or orphan test case |

## Important Guidelines

- Be specific: quote exact text from requirements and test cases
- Explain WHY it's a contradiction, don't just state that one exists
- Provide actionable recommendations for each finding
- Do not report stylistic differences or wording variations as contradictions
- Only flag genuine semantic conflicts
- When uncertain, classify as MEDIUM and note the ambiguity
