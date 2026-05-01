---
description: Group normalized requirements and test cases by feature and order them into a logical onboarding sequence. Reads optional feature_order from config/sources.json. Produces the structure consumed by demo-feature and assess-feature.
---

# Build Onboarding Plan — Skill

Group normalized requirements and test cases by feature, then order the features into a sensible learning sequence.

## When to Use

Invoked once at the start of an onboarding journey, after `fetch-requirements`, `fetch-test-cases`, and `normalize-artifacts` have produced cross-linked canonical artifacts. The result is persisted in `feature_order` of the progress log and consumed by the demo and assessment skills.

## Inputs

- `requirements`: array of normalized requirement objects (canonical schema)
- `test_cases`: array of normalized test case objects (canonical schema)
- Optional: `feature_order` from `${CLAUDE_PLUGIN_ROOT}/config/sources.json`

## Steps

### 1. Group by Feature

Create a feature-keyed map:

- For each requirement, append it to `groups[requirement.feature].requirements`.
- For each test case, append it to `groups[test_case.feature].test_cases`.
- If a feature appears only in test cases (orphan section) or only in requirements (untested feature), still include it — onboarding may want to flag those.

### 2. Filter Out Non-Onboardable Features

Skip features that should not appear in onboarding:

- Documentation-only sections (e.g., `"Document Identification"`, `"Referenced Documents"`, `"Glossary"`)
- Features where every requirement has `status` of `"Deprecated"`, `"Deferred"`, or `"Removed"`
- Features with zero requirements (test-only orphan sections) — flag as a warning, do not include in the onboarding flow

### 3. Determine Feature Order

In priority order:

1. **Explicit override**: if `config/sources.json` contains a top-level `feature_order: ["Feature A", "Feature B", ...]`, use that order. Features in the array but not in the data → drop with a warning. Features in the data but not in the array → append at the end in source order with a note.
2. **Source order fallback**: if no override, use the order in which features first appear in the input requirements list. Jama and Excel exports usually have intentional ordering.
3. **Persist the chosen order** to the progress log's `feature_order` field on first run. Subsequent resumes use the persisted order, not a recomputation, to keep the journey deterministic.

### 4. Order Steps Within a Feature

For each feature, produce an ordered list of small demo steps:

- One step per requirement, in the order requirements appear in the source.
- For each requirement, attach a curated summary of its linked test cases as do/don't pairs (positive paths → ✅ DO, negative paths → ❌ DON'T).
- If a requirement has more than 3 linked test cases, prefer the most distinctive ones (different verdicts, different edge cases). Don't dump everything.

A feature's plan looks like this in memory:

```json
{
  "name": "Patient Monitoring",
  "purpose": "<1-2 sentence synthesis of feature intent from requirement descriptions>",
  "requirement_ids": ["CWA-REQ-85", "CWA-REQ-86"],
  "test_case_ids": ["TC-1234", "TC-1235", "TC-1236"],
  "steps": [
    {
      "step_index": 0,
      "requirement_id": "CWA-REQ-85",
      "title": "<short title>",
      "explanation": "<plain-language explanation>",
      "dos": [
        { "tc_id": "TC-1234", "rule": "DO: enter a valid patient ID before pressing Save" }
      ],
      "donts": [
        { "tc_id": "TC-1235", "rule": "DON'T: leave the patient ID empty — system rejects with validation error" }
      ]
    }
  ]
}
```

### 5. Initialize the Features Block in the Log

When the agent first writes the progress log, populate `features[]` from this plan:

- One entry per feature in chosen order
- `status: "pending"` for all
- `requirement_ids`, `test_case_ids` filled from the plan
- `assessment: null`, `user_notes: []`, `completed_at: null`

Do NOT serialize the full `steps` array into the log — it can be reconstructed from a fresh fetch on resume. The log stores only what's needed to resume position and grade outcomes.

## Output

Return:

```json
{
  "feature_order": ["FeatureA", "FeatureB", "..."],
  "features": [ { "<feature plan as shown above>" } ],
  "warnings": ["<any skipped or reordered features>"]
}
```

## Guidelines

- **Don't invent purpose statements** — synthesize only from text actually present in the requirement descriptions. If a feature has no usable description, set `purpose` to a literal title like `"<Feature Name> (no description in source)"` rather than guessing.
- **Stable ordering** — the same input should always produce the same plan. The persisted `feature_order` makes resumes deterministic.
- **Small steps** — prefer one requirement per step. If a single requirement is large (e.g., a paragraph with many acceptance criteria), the demo skill is responsible for splitting; this skill stays at the requirement granularity.
- **Surface warnings** explicitly. If `feature_order` lists a feature that doesn't exist in the data, the user should know.
