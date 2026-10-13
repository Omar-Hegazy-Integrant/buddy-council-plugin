---
description: Internal (used by /bc:ask) — Find where a behavior or business rule the user describes in their own words is already specified. Code ranks every requirement in scope against the ask (BM25 over concepts the model words — no embeddings), and the model judges which requirements cover it, partly cover it, conflict with it, or relate to it.
user-invocable: false
---

# Lookup Requirements — Skill

Answers "Do we already have this? Where is it specified? Which requirements cover it?" for an ask in the
user's own words, without reading every requirement. The work splits three ways: you word the ask as
concepts, code searches every requirement in scope (plus the test cases and linked docs) in about a second,
and you judge only the top matches, reading their text verbatim.

`<script>` below is `<plugin_root>/skills/collect-artifacts/bc_run.py`, where `<plugin_root>` is the absolute
plugin path — `${CLAUDE_PLUGIN_ROOT}`, else `plugin_root` in `.buddy-council/sources.json`. Always pass the
absolute path, and wrap every value in single quotes; no value may contain a single quote, so drop apostrophes
("patients", "dont").

## Step 1: Word the ask as concepts

- **Split the ask into its concepts** — the 1–4 distinct ideas a requirement must express to cover it.
  "Can alarms be muted during warm-up?" → *mute*, *alarm*, *warm-up*. A requirement about alarms that says
  nothing about muting is related, not coverage, and the search ranks requirements that match every concept
  first. Leave out the question's own words and the words every requirement has (app, user, system, shall,
  display).
- **Give each concept its other wordings**, separated by `|`: the user's own word first, then 3–8 that the
  requirements might use instead — synonyms, the domain's terms, abbreviations, other word forms, and the
  specific kinds of a general word (`alarm|alert|notification|audible|sound`). Phrases are fine
  (`sensor warm-up`); every spelling of a hyphenated word (warm-up, warmup) already matches.
- **Don't widen a concept into a different idea.** "Session start" is not "warm-up"; a loose wording floods
  the results with requirements that only share a word.
- A platform, product, or version (iOS, Pro edition, 2.1) is a concept only when the user asked about it.
- When the user named an area ("in the alerts requirements"), add `--feature '<its words>'` — the search then
  keeps requirements whose folder path contains those words.

## Step 2: Get a run of the whole scope

Lookups search every requirement in scope, so they need a run of the whole scope — and reuse a recent one:

1. Unless the user asked for fresh data ("refresh", "re-read Jama"), ask for a recent run:

   ```
   python3 <script> recent --project '<absolute path of the current working directory>' --max-age-minutes 30
   ```

   When it returns a `run_dir`, print
   `Collect: reusing run <run_dir's folder name>, collected <age_minutes> minutes ago — say refresh to re-read the sources`
   and then each entry of its `lines`, exactly. The run is complete and was checked against the current
   `sources.json` again.
2. Otherwise follow `${CLAUDE_PLUGIN_ROOT}/skills/collect-artifacts/SKILL.md`, Steps 1 and 2, with
   `command: lookup` and `scope: all` — its checks and the Data Contract included. Skip its Step 3: a lookup
   reads search results, not the feature bundles.
3. A run with 0 requirements → stop and say so.

## Step 3: Search

```
python3 <script> search --run '<run_dir>' --query '<the ask>' --concept '<own word|wording|wording>' --concept '<...>'
```

It answers with a JSON summary — `files`, `matched` (how many requirements match every concept and at least
one), `top`, `by_feature`, `unmatched_wordings`, `suggested_terms`, `warnings` — and writes the results to the
run folder. Read every file in `files`, in order. They hold:

- **Where the matches are** — the features and folders the top results sit in.
- **Ranked requirements** — the top 25, each with the concepts it matched and through which wording, its
  status, folder, text verbatim, custom fields (platform, product, version), Jama link, and the test cases
  that link to it.
- **Test cases that mention it**, with their links — `Links: none` means no requirement is behind the case —
  and **linked docs that mention it**, each with the best-matching line.
- **Next candidates** — ranks 26–50 by title, and **the words the top results use** that you didn't search.

## Step 4: Search again when one search can't settle it

Requirements often say it in words you didn't guess. Search again — at most twice more — with the concepts
reworded from what the results taught you, when:

- nothing in the results covers the ask, or the best matches miss one of its concepts;
- `unmatched_wordings` shows a wording no requirement uses, or `warnings` names a concept nothing matches;
- the top results, or `suggested_terms`, use the project's own wording for a concept — "loss of signal"
  where you searched "disconnected".

**Never answer "not specified" from a single search.**

## Step 5: Judge

Place each candidate that matters; leave the rest out — sharing a word is not relevance:

- **Covers** — an active requirement states the asked behavior, under the asked conditions.
- **Partly covers** — part of it, or the same behavior under other conditions (another state, platform,
  product, alert, or timing). Say exactly which part is missing.
- **Conflicts** — an active requirement requires the opposite, or something the ask would break.
- **Related** — the same area, a different behavior; context, not coverage.

Rules:

- **Read the conditions word by word.** "Once the warm-up has completed, the App shall sound …" does not
  cover "during warm-up" — it excludes it, and that may be the answer.
- A **Deprecated** or **Deferred** requirement never covers; mention it as history ("was specified by X, now
  Deprecated"). A **Draft** requirement covers, labelled Draft.
- To read a candidate's neighbours, or one from Next candidates, print them by ID:

  ```
  python3 <script> show --run '<run_dir>' --ids 'ID, ID'
  ```

- A test case under "Test cases that mention it" that exercises the asked behavior while no covering
  requirement backs it (`Links: none`, or links only to requirements that don't cover the ask) is **tested
  without a requirement** — report it; it is a gap in the requirements, not in the tests.

## Step 6: In-flight work — the Data Contract's board source

When `jira.board` is configured and `jira.pending` is not `true`, follow
`${CLAUDE_PLUGIN_ROOT}/skills/fetch-board-issues/SKILL.md` once for the board and print its `Fetch:` line;
otherwise print its skip line. An issue is **in flight** for this ask when its `linked_ids` hit a requirement
you placed, or its title or description describes the asked behavior. A configured board that errors is a
failed source: say so and ask whether to continue — the answer is then **PARTIAL**.

## Step 7: Answer

Lead with the verdict, then the evidence. Quote requirement text verbatim, cite IDs with their links, and
keep it short.

- **Already specified** — at least one active requirement covers the ask.
- **Partly specified** — requirements cover part of it; name the part that no requirement states.
- **Not specified** — only after Step 4; name the closest requirements and the folder where it would belong.
- Lead with **Conflicts with <ID>** whenever an active requirement conflicts — it matters most.

```
**Partly specified.** <one sentence: what the requirements say about the ask, and what they leave out>

Covers / partly covers
- CWA-REQ-41 (Draft) · Patient Onboarding / Sensor Warm-up — "<the sentence that covers it, verbatim>"
  Tested by TC-123, TC-456 · <link>
Conflicts
- CWA-REQ-87 · Alerts — "Once the warm-up has completed, …" — <why it conflicts>
Related
- CWA-REQ-90 — <one line>
Where it lives: Alerts / Alert Triggers (12 of the top 25), Patient Onboarding / Sensor Warm-up (2)
In flight: PROJ-1234 — <title> (In Progress) — touches CWA-REQ-87
Tested without a requirement: TC-789 — "<title>"
Gap: <what no requirement states> — it would belong in <folder>
```

Leave out any section with nothing in it. When the run was reused, say how old it is. A follow-up about the
same ask reads the same results; a new topic is a new search on the same run. If the user wants the gap
written up, suggest `/bc:validate` with the ask — it drafts the Jira ticket. This skill writes nothing.
