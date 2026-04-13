---
description: Analyze normalized requirements and test cases to identify coverage gaps, untested requirements, orphan test cases, and weak coverage.
---

# Analyze Coverage — Skill

Identify coverage gaps between requirements and test cases after they have been fetched, normalized, and cross-linked.

## When to Use

After artifacts have been fetched and normalized. This is the core analysis skill invoked by the coverage agent.

## Coverage Categories

### 1. Untested Requirements
Requirements where `linked_ids` is empty — no test cases are linked to them.

### 2. Orphan Test Cases
Test cases where `linked_ids` is empty — no requirements are linked to them.

### 3. Weak Coverage
Requirements that have linked test cases, but the test cases do not meaningfully exercise the requirement's acceptance criteria. Examples:
- Test only covers the happy path but requirement specifies error handling
- Test checks a UI element but requirement defines a behavioral constraint
- Test validates a subset of conditions but requirement lists multiple

## Analysis Approach

### For Scoped Analysis (single requirement or feature)

1. Check each requirement in scope for empty `linked_ids` → untested
2. Check each test case in scope for empty `linked_ids` → orphan
3. For requirements with linked test cases: compare the requirement description against test case steps and expected results to assess coverage depth
4. Compute coverage metrics for the scope

### For Full Analysis (all requirements)

Process feature by feature to manage context:
1. Group requirements and test cases by feature
2. Analyze each feature independently
3. Aggregate into a summary with per-feature breakdown
4. Flag features with the lowest coverage percentages

### Weak Coverage Detection

For each requirement that has at least one linked test case:
- Read the requirement description for acceptance criteria, conditions, and constraints
- Read each linked test case's steps and expected results
- Identify gaps: criteria in the requirement that no test case exercises
- Only flag cases with clear gaps — do not flag stylistic differences

**Important**: Weak coverage findings require human judgment. Label them clearly as suggestions, not definitive gaps.

## Coverage Metrics

Compute these for every analysis:
- **Total requirements** in scope
- **Tested**: requirements with at least one linked test case
- **Untested**: requirements with no linked test cases
- **Coverage %**: `(tested / total) * 100`
- **Total test cases** in scope
- **Linked**: test cases with at least one linked requirement
- **Orphan**: test cases with no linked requirements

## Output Format

Produce a human-readable report:

```markdown
# Coverage Analysis Report

## Summary
- **Scope**: [what was analyzed]
- **Requirements**: [total] ([tested] tested, [untested] untested)
- **Test cases**: [total] ([linked] linked, [orphans] orphans)
- **Coverage**: [percentage]%

## Feature Coverage Breakdown

| Feature | Requirements | Tested | Untested | Coverage |
|---------|-------------|--------|----------|----------|
| [name]  | [n]         | [n]    | [n]      | [n]%     |

## Untested Requirements

### [Feature Name]
- **CWA-REQ-XX**: "[title]" — [brief note on what is untested]

## Orphan Test Cases
- **TC-XXXX**: "[title]" — no linked requirement found

## Weak Coverage
- **CWA-REQ-XX**: "[title]" — linked to TC-XXXX but test only covers [aspect]; requirement also specifies [missing aspect]

## Recommendations
1. [Highest priority: safety-critical untested requirements]
2. [Feature areas with lowest coverage %]
3. [Orphan test cases that may need linking or removal]
```

## Severity Classification

| Severity | Criteria |
|----------|----------|
| **HIGH** | Safety-critical or core functionality requirement with zero test coverage |
| **MEDIUM** | Non-critical requirement with zero coverage, or orphan test case |
| **LOW** | Weak coverage — test exists but is superficial |

## Guidelines

- Do NOT flag requirements with status "Deprecated" or "Deferred" as untested
- Do NOT flag documentation-only requirements (e.g., "Document Identification", "Referenced Documents") as gaps
- Be specific: quote requirement IDs and test case IDs
- Provide actionable recommendations ordered by priority
- When coverage is 100%, still check for weak coverage
