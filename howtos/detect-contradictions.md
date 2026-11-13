# Detect Contradictions

The rules for judging what `skills/contradiction/SKILL.md` compares, and for saving each finding so code can check
it.

## What you read

A feature's bundle lists every requirement as written — status, folder, description, rationale, custom fields,
linked docs — and, under each one, the test cases that link to it, steps and expected results included. Code
resolved the links, so a requirement's `Test cases:` line is exact. Code also counted the requirements with no
test case and the test cases with no requirement, and the report states those counts.

## A finding

A finding is two or more places that can't all be right. Look for a conflict in meaning:

- a different value for the same thing — a time, count, limit, or default: 3 seconds against 10
- a different result for the same trigger — a warning dialog against a silent log entry
- a different state or order — data shown at once against data that loads later
- a step that does what its requirement rules out — a read-only requirement whose test changes data
- a linked test case whose steps never exercise its requirement — rated Low

Two wordings of the same behavior agree. Save each finding as JSON:

```json
{
  "items": [
    {"id": "CWA-REQ-85", "quote": "refresh rate of 3 seconds"},
    {"id": "TC-1234", "quote": "vitals update every 10 seconds"}
  ],
  "severity": "critical",
  "why": "The test passes a system that refreshes every 10 seconds, which breaks the 3-second requirement.",
  "fix": "TC-1234"
}
```

- **`items`** — the places that disagree, 2 to 8, each an `id` and a `quote`. An item that contradicts itself
  appears twice, once per place.
- **`quote`** — the item's own words, copied from the bundle: a phrase or a sentence, up to 500 characters, enough
  to find the place. Mark words you leave out with `…`. Code finds the quote in its item and names the place —
  `preconditions`, `step 3, expected result` — so give the words alone. In a Low finding, the test case needs no
  quote: say in `why` what it leaves out.
- **A board ticket** isn't in the run: give it `"board": {"title": "<its title>", "url": "<its link>"}`, from
  `raw_fields.url`. Its quote is shown as not checked.
- **`severity`** — from the table below.
- **`why`** — one plain sentence on why they can't all be right.
- **`fix`** — the ID of the item to change, or `"decide"` when a person must choose. When that item appears twice,
  list the place to change first.

## Severity

| `severity` | When |
|---|---|
| `critical` | a broken product would still pass the test |
| `high` | the items ask for different results in the same situation, so someone builds or tests the wrong thing |
| `medium` | they may conflict, and a person has to decide |
| `low` | a test case is linked to a requirement it doesn't really test |

When unsure, take the lower level and say what is uncertain in `why`.

## The side to fix

The requirement is the specification, so a test case that contradicts a clear requirement is the side to fix.
Name the requirement only on evidence that it is out of date, such as a newer requirement or in-flight board work
that replaces it, and write `"decide"` when you can't tell. The fix offer rewrites exactly the test cases `fix`
names, so name the side with care.

## Saving

Save a feature's findings together, as their own command:

```
python3 '<plugin root>/scripts/bc_run.py' findings --run '<run_dir>' --add '<json>'
```

`--add` takes one finding or a list, as JSON on one line, with every `'` written as `\u0027`. Code checks each
one: every ID must be in the run, and every quote must be in its item, whatever its case, spacing, or quote
marks. It answers `saved`, `total`, and `rejected`. A rejected finding comes back with its problems, and a quote
that isn't in its item comes with the closest text code found there. Copy the exact words from the bundle and
save it again, or drop it. A finding saved again with the same quotes replaces the first.

**Done when** every finding is saved and `rejected` is empty, or each finding still in it was dropped on
purpose.
