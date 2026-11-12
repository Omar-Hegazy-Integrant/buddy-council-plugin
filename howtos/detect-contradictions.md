# Detect Contradictions

The rules `skills/contradiction/SKILL.md` applies to each feature of a run, and the format of its report.

## What you read

A feature's bundle lists every requirement exactly as written — status, folder, description, rationale,
custom fields, linked docs — and, under each one, the test cases that link to it, steps and expected results
included. Code resolved the links, so a requirement's `Test cases:` line is exact, and the feature's
`untested` and `orphans` lists from `bc_run.py summary --feature` are complete. Quote from the bundle.

## The seven types

1. **Direct conflict** — a requirement states X, and a linked test case validates the opposite.
   *Example:* the requirement says "time out after 30 seconds"; the test expects 60.
2. **Behavioral conflict** — different expected behavior for the same trigger or condition.
   *Example:* the requirement says "display a warning dialog"; the test expects a silent log entry.
3. **Scope overlap** — two requirements cover the same behavior with incompatible constraints.
   *Example:* REQ-10 says "support up to 100 users"; REQ-45 says "handle unlimited concurrent sessions".
4. **Test case vs requirement** — a test case's steps or expected results contradict the requirement it
   claims to verify. *Example:* a test linked to a read-only requirement has steps that change data.
5. **Cross-feature tension** — requirements in different features set conflicting expectations.
   *Example:* one feature logs out after 5 minutes idle; another keeps the session open during monitoring.
6. **Missing alignment** — a requirement with no test case or a test case with no requirement, both taken
   from the `untested` and `orphans` lists; or a linked test case whose steps don't verify its requirement.
7. **Temporal or state conflict** — conflicting expectations about the system's state or timing.
   *Example:* the requirement assumes data is there at once; the test waits for it to load.

A board ticket is checked as proposed behavior, against the requirements, with the same types.

## Severity

| Severity | Types | Meaning |
|---|---|---|
| **CRITICAL** | 1 | The test validates the opposite of what the requirement states |
| **HIGH** | 2, 4 | The test and the requirement describe meaningfully different behavior |
| **MEDIUM** | 3, 5, 7 | A possible conflict that needs a person's judgment |
| **LOW** | 6 | Missing alignment |

When you are unsure, rate the finding MEDIUM and say what is ambiguous.

## Every finding

- Quote the exact words from both sides.
- Explain why they conflict, in a sentence or two.
- Recommend a concrete change.
- For types 1, 2, 4, and 7, add a **Fix** line naming the side to change:
  - `test case — TC-1234, step 3 expected result`
  - `requirement — CWA-REQ-85`
  - `decide — <why neither side is clearly right>`

  The requirement is the specification, so a test case that contradicts a clear requirement is the side to
  fix. Name the requirement only on evidence that it is out of date, such as a newer requirement or in-flight
  board work that replaces it, and write `decide` when you can't tell. The fix offer rewrites exactly the test
  cases a `test case` line names, so name the side with care.
- Report conflicts in meaning. Two wordings of the same behavior agree.

## Report

```markdown
# Contradiction Analysis Report

<PARTIAL — <missing source>, when the user chose to continue without one>
<every line from load-settings and the run's Fetch:, Enrichment:, and Readiness: lines, exactly as printed>

## Summary
- **Scope**: <what was analyzed>
- **Requirements analyzed**: <count from the run summary>
- **Test cases analyzed**: <count from the run summary>
- **Issues found**: <count per severity>

## Critical Issues

### [CRITICAL] Direct conflict between CWA-REQ-85 and TC-1234
**Requirement (CWA-REQ-85)**: "The system shall display patient vitals with a refresh rate of 3 seconds"
**Test Case (TC-1234), Step 3**: Expects "vitals update every 10 seconds"
**Explanation**: The test passes a system that refreshes every 10 seconds, which breaks the 3-second requirement.
**Fix**: test case — TC-1234, step 3 expected result
**Recommendation**: Change TC-1234 step 3 to expect a 3-second refresh.

## High Issues
...

## Medium Issues
...

## Low Issues
...

## In-flight Work
<only when board tickets were checked>
Checked <N> board tickets: <the board scope the fetch used, such as every ticket not done on board 42>.

### [HIGH] PROJ-123 vs CWA-REQ-85 — <the ticket's link>
...

## Missing Alignment
### Requirements without test cases
- CWA-REQ-92: "System shall support audit logging"

### Test cases without a requirement
- TC-1456: "Verify backup schedule"
```
