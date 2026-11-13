# Detect Contradictions

What to compare in a slice of the run, how to judge it, and how to save each finding so code can check it.

## What you read

Code cut the run into **slices**, each small enough to check at once: a few whole features, or some of the
requirements of a big one. A slice's bundle parts list each of its requirements as written — status, folder,
description, rationale, custom fields, linked docs — and, under each one, the test cases that link to it, steps and
expected results included. The test cases that link to no requirement come last. Code resolved the links, so a
requirement's `Test cases:` line is exact. A requirement with more test cases than one slice holds is written out
again, marked `(continued)`, above each share of them. Code also counted the requirements with no test case and the
test cases with no requirement, and the report states those counts.

Once every slice is checked, code pairs the items on the same subject that no slice holds together — and, in a run on
part of the product, its items with the rest of the product — by the subject lines their readers saved, and cuts the
pairs into **slices of pairs**, checked the same way. Last, code cuts the board's tickets into **slices of tickets**,
each ticket with the requirements it links to and the requirements and test cases on its subject. The session checks
those itself: only it holds the tickets' text.

## Checking a slice

Whoever checks a slice — a helper, or the session itself — does this once. Run each command exactly as shown, as
its own command, and read files with your view tool.

1. List the slice:

   ```
   python3 '<plugin root>/scripts/bc_run.py' slices --run '<run_dir>' --slice <n>
   ```

   It answers with the slice's bundle `parts` and the `docs` they cite, as paths, and `needs_lines`: the items it
   still needs a subject line for.
2. Read each part and each doc once, in order, one per view. Together they are the whole slice.
3. Make every comparison in *What to compare*, and judge each by the rules below.
4. Save a subject line for each ID in `needs_lines`, as *Subject lines* shows. When it is empty, there is nothing
   to save.
5. Save the slice's findings in one command, as *Saving* shows, with `--slice <n>`. When the slice holds no
   conflict, save `[]`.
6. **A helper** ends with one line of JSON, and nothing else: `{"slice": <n>, "saved": <how many>}`. Add
   `"docs_not_read": ["<link>: <why>"]` for each doc a part told you to read that you couldn't, and answer
   `{"slice": <n>, "error": "<what stopped you>"}` when you couldn't finish.

**Done when** the findings save answered `"checked": true`, and a helper has sent its line.

A helper works from its slice alone — its parts and the docs they name — and leaves every question to the session
that started it.

## What to compare

In a **slice of pairs**, compare the two items of each pair with each other, and nothing else: their slices already
compared each with its own neighbors. A test case comes with the requirements it tests, to tell which side is right.

In a **slice of tickets**, compare each ticket — its text is in your board read — with the requirements and test
cases under it, and nothing else, by the rules of `howtos/detect-ticket-contradictions.md`.

In any other slice, make every comparison across everything it holds:

1. **Each requirement with its test cases.**
2. **The requirements with each other.**
3. **The test cases with each other.** Test cases for the same requirement expect the same result in the same
   situation.
4. **Each item with itself:** a step with its own expected result, a test case's title or preconditions with its
   steps, a requirement's promise with its own details. A step that waits 5 minutes while its expected result says
   10 is one; a requirement that promises three options and lists two is another.

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
- **A board ticket** — name it by its key, and code adds the title and link saved with it. The run doesn't hold its
  text, so its quote is shown as not checked. Its `severity` and `fix` follow
  `howtos/detect-ticket-contradictions.md` → *Output Format*.
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
that replaces it, and write `"decide"` when you can't tell.

Between test cases, or inside one, the requirement they test decides. When its words show which side is right, the
requirement is one of the places that disagree: add it to `items`, and fix the test case that breaks it. Save that
once, even when several comparisons show it. When the requirement is too vague to say, write `"decide"`, and name
it in `why`.

The fix offer rewrites exactly the test cases `fix` names, to agree with the requirement's words, so name the side
with care. Code saves a test case as `fix` only when a requirement or a board ticket is among the items.

## Saving

Save a slice's findings together, as their own command:

```
python3 '<plugin root>/scripts/bc_run.py' findings --run '<run_dir>' --slice <n> --add '<json>'
```

`--slice` names the slice they come from. `--add` takes one finding or a list, as JSON on one line, with every `'`
written as `\u0027`. Code checks each one: every ID must be in the run, and every quote must be in its item,
whatever its case, spacing, or quote marks. It answers `saved`, `total`, and `rejected`. A rejected finding comes
back with its problems, and a quote that isn't in its item comes with the closest text code found there. Copy the
exact words from the bundle and save it again, or drop it. A finding saved again with the same quotes replaces the
first. With `--slice`, it also answers `checked`: the slice counts as checked once a save of it leaves nothing
rejected — so after dropping the last rejected finding, save `[]` — and each ID in its `needs_lines` has a subject
line.

**Done when** every finding is saved and `rejected` is empty, or each finding still in it was dropped on
purpose — and, with `--slice`, the answer says `"checked": true`.

## Subject lines

Code finds the items in other slices, or elsewhere in the product, that could conflict with this slice's by their
subject lines: one line per item, its subject, a colon, then what it says about it, at most 200 characters.

- **The subject** — the thing the item is about: the screen, setting, data, or state, in the product's own word for
  it. Name the thing rather than what happens to it — `Session`, not `Idle logout` — so an item on the same thing in
  another feature names it alike.
- **What it says** — the values, results, and states the item states, or for a test case expects, as behavior. For
  a board ticket, what it will change them to.

*(made-up)* Login's `Session: ends after 5 minutes idle.` and Monitoring's `Session: stays open while monitoring.`
share a subject, so code pairs them.

Save them together, as their own command:

```
python3 '<plugin root>/scripts/bc_run.py' subjects --run '<run_dir>' --slice <n> --add '<json>'
```

`--add` is one JSON object on one line, each ID with its line, with every `'` written as `\u0027` — *(made-up)*
`{"CWA-REQ-85": "Vitals refresh: every 3 seconds.", "TC-1234": "Vitals refresh: every 10 seconds."}`. It answers
`saved`, `rejected` with each problem, the IDs still `missing` a line, and `checked`.

**Done when** `missing` is empty.
