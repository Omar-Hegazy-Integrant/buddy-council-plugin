---
description: Find requirements related to a ticket description using feature extraction and keyword matching strategies.
---

# Find Related Requirements — Skill

Find requirements related to a ticket description by analyzing the description text and matching against normalized requirements.

## When to Use

In the ticket validation workflow, after fetching and normalizing requirements and before checking for contradictions or gaps. This skill determines which requirements to validate the ticket description against.

## Input

- `ticket_description`: The raw ticket description text provided by the user
- `requirements`: Array of normalized requirement objects (canonical schema)

## Matching Strategies

Use a **hybrid approach** combining feature extraction and keyword matching. Apply strategies in order, collecting matches:

### Strategy 1: Feature Name Extraction

Extract feature/component names from the ticket description using natural language cues:

**Patterns to look for:**
- Explicit feature mentions: "in the [feature name]", "for the [feature name]", "update [feature name]"
- Component mentions: "checkout page", "login button", "patient monitoring view"
- Module references: "authentication module", "reporting system"

**Extraction logic:**
- Look for noun phrases after prepositions ("in", "for", "on", "to")
- Identify capitalized phrases that might be feature names
- Extract technical terms or domain-specific vocabulary

**Matching:**
- Compare extracted features to the `feature` field in requirements (case-insensitive, fuzzy match)
- Also check if feature names appear in requirement titles or descriptions
- Assign high relevance score (0.9-1.0) for exact feature matches

### Strategy 2: Keyword Matching

Extract keywords from the ticket description and match against requirements:

**Keyword extraction:**
- Tokenize the description (split on whitespace, punctuation)
- Filter out stopwords (the, a, an, in, on, to, etc.)
- Identify significant terms: nouns, verbs, adjectives, technical terms
- Weight multi-word phrases higher than single words

**Matching logic:**
- For each requirement, calculate keyword overlap with the description
- Check keywords in requirement's `title` (higher weight), `description` (medium weight), and `feature` (lower weight)
- Use TF-IDF-like scoring: rare keywords in the corpus matter more than common ones

**Scoring:**
```
score = (title_matches * 3 + desc_matches * 2 + feature_matches * 1) / total_keywords
```

Assign relevance score 0.4-0.8 based on keyword overlap percentage.

### Strategy 3: Exact ID References

Check if the ticket description mentions specific requirement IDs:

**Patterns:**
- "CWA-REQ-85", "REQ-123", "requirement #85"
- "implements CWA-REQ-85", "relates to REQ-123"

If found, assign those requirements highest relevance (1.0) and include them automatically.

### Strategy 4: Semantic Context

If no matches from strategies 1-3 or too few matches (<3), broaden the search:

**Contextual clues:**
- Related requirements in the same feature
- Requirements with similar action verbs ("add", "update", "remove")
- Requirements recently modified or frequently referenced
- Requirements with similar complexity or scope

Assign lower relevance scores (0.2-0.4) for contextual matches.

## Scoring and Ranking

Combine scores from all strategies:
1. Exact ID references: 1.0
2. Feature name exact match: 0.9
3. Feature name fuzzy match: 0.7
4. High keyword overlap (>60%): 0.6-0.8
5. Medium keyword overlap (30-60%): 0.4-0.6
6. Low keyword overlap (<30%): 0.2-0.4
7. Contextual matches: 0.1-0.3

**Threshold:** Include requirements with score >= 0.3 in the results.

**Maximum:** Return top 10 most relevant requirements (to avoid overwhelming Claude with too many to validate against).

## Output

Return a JSON array of related requirement IDs with relevance scores, sorted by score descending:

```json
[
  {
    "id": "CWA-REQ-85",
    "relevance_score": 0.95,
    "match_reasons": ["Feature 'Patient Monitoring' exact match", "Keywords: monitor, vitals, display (8/10)"]
  },
  {
    "id": "CWA-REQ-86",
    "relevance_score": 0.78,
    "match_reasons": ["Feature 'Patient Monitoring' exact match", "Keywords: real-time, update (5/10)"]
  },
  {
    "id": "CWA-REQ-42",
    "relevance_score": 0.45,
    "match_reasons": ["Keywords: display, user interface (4/10)", "Related feature: UI Components"]
  }
]
```

## Edge Cases

### No Matches Found
- If NO requirements match (all scores < 0.3), return an empty array
- The calling agent should ask the user: "No related requirements found. Please provide requirement IDs to validate against, or skip validation."

### Too Many Matches
- If more than 20 requirements have scores >= 0.3, raise the threshold to 0.5
- Still cap at top 10 results to keep validation focused

### Ambiguous Feature References
- If multiple features have similar names (e.g., "Login" vs "Login Button" vs "User Login"), include requirements from all similar features
- Let the user filter later if needed

### Generic Descriptions
- If the ticket description is too generic ("update the app", "fix bug"), all strategies will yield low scores
- Return top 5 matches even if scores are low, with a note: "Description is generic — validation may miss relevant requirements"

## Example

**Ticket Description:**
```
Add a real-time heart rate monitor to the patient vitals dashboard. The monitor should update every 2 seconds when connected to a device.
```

**Requirements:**
- CWA-REQ-85: Patient monitoring view displays vital signs in real-time (Feature: Patient Monitoring)
- CWA-REQ-86: Vitals refresh rate must be <= 3 seconds (Feature: Patient Monitoring)
- CWA-REQ-100: ECG waveform displays on monitoring screen (Feature: Patient Monitoring)
- CWA-REQ-42: All dashboard widgets support real-time updates (Feature: UI Components)

**Expected Output:**
```json
[
  {
    "id": "CWA-REQ-85",
    "relevance_score": 0.95,
    "match_reasons": [
      "Feature 'Patient Monitoring' exact match",
      "Keywords: patient, monitoring, vitals, real-time, displays (10/12 keywords)"
    ]
  },
  {
    "id": "CWA-REQ-86",
    "relevance_score": 0.88,
    "match_reasons": [
      "Feature 'Patient Monitoring' exact match",
      "Keywords: vitals, refresh, rate, seconds (7/12 keywords)",
      "Mentions specific timing constraint (2 seconds vs requirement's 3 seconds)"
    ]
  },
  {
    "id": "CWA-REQ-100",
    "relevance_score": 0.72,
    "match_reasons": [
      "Feature 'Patient Monitoring' exact match",
      "Keywords: monitoring, displays (4/12 keywords)"
    ]
  },
  {
    "id": "CWA-REQ-42",
    "relevance_score": 0.45,
    "match_reasons": [
      "Keywords: dashboard, real-time, updates (3/12 keywords)",
      "Related feature: UI Components (not exact match)"
    ]
  }
]
```

## Performance Considerations

- For large requirements sets (>500), apply feature filtering FIRST to narrow the corpus before keyword matching
- Cache keyword extraction and scoring results if the same ticket description is validated multiple times
- Skip requirements marked as "Archived", "Deprecated", or "Obsolete" (check `status` field)
