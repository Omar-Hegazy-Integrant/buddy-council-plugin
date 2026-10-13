---
description: Internal (used by /bc:coverage, /bc:contradiction, and /bc:ask lookups) — Collect a run's requirements, test cases, and linked GitHub docs into a run folder through the low-cost data-collector sub-agent, then read them back one feature at a time. The data reaches the analysis as files, never as a model's retelling.
user-invocable: false
---

# Collect Artifacts — Skill

The mechanical part of an analysis run — fetching, waiting out a slow Jama read, paging, cross-linking IDs,
counting — needs no judgment, so it runs on a **low-cost model** in the `bc:data-collector` sub-agent, and
the counting runs as plain code. The servers and the link script write everything into a **run folder**;
your model only reads the parts it needs. Nothing is fetched twice, and no requirement or test case text is
ever retyped by a model.

```
<project>/.buddy-council/runs/<run-id>/
  requirements.json  test_cases.json   full canonical records (written by the Jama/TestRail servers)
  docs/  docs.json                     GitHub docs the requirements link to (gh CLI strategy)
  links.json                           counts, untested, orphans, stranded links, per-feature rows — computed by code
  bundles/NN-<feature>-<p>.md          each feature's requirements with their test cases, verbatim, in small parts
  manifest.json
```

The newest 5 runs are kept, none longer than a week. The folder sits inside `.buddy-council/`, which
`/bc:setup` keeps out of git.

## Step 1: Launch the collector

Start the `bc:data-collector` sub-agent and wait for it. Its prompt is four lines:

```
project: <absolute path of the current working directory>
command: <coverage | contradiction | lookup>
scope: <the scope exactly as the user gave it, or all>
plugin_root: <absolute plugin path — ${CLAUDE_PLUGIN_ROOT}, else plugin_root from .buddy-council/sources.json>
```

How to pick its model depends on the runtime:

- **Claude Code** — your sub-agent tool takes `subagent_type`: pass `subagent_type: "bc:data-collector"` and
  **`model: "haiku"`**.
- **Copilot CLI** — your sub-agent tool takes `agent_type`: pass `agent_type: "bc:data-collector"` and **no
  `model`**. The agent's own definition picks a low-cost model (`claude-haiku-4.5`, else `gpt-5-mini`), and a
  user can change it in `/subagents`.

**Fallback.** If the sub-agent cannot be launched, or does not return a `run_dir`, print
`Collect: ran inline — the low-cost collector was unavailable (<reason>)` and carry out the steps of
`${CLAUDE_PLUGIN_ROOT}/agents/data-collector.agent.md` yourself (under Copilot CLI, the same file under the
`plugin_root` in `.buddy-council/sources.json`). Same files, same result — only the cost differs.

## Step 2: Check the run, then report it — the Data Contract

Read `<run_dir>/links.json`. Code wrote it from the files the servers wrote, so it — not the collector's
reply — is what you trust:

1. **No `links.json`** → the collection did not finish; the collector's `failed` says why. Collect again
   inline (the fallback above), once. If that fails too, STOP and report the error.
2. **Wrong run** → `scope.input` is not the scope you asked for, or `mismatches` is not empty — each mismatch
   is a tool the collector called with arguments that differ from the project's config (a missing item-type
   filter, a different suite, a filtered test case read). Print
   `Collect: the run did not match the config — collecting again inline` and the mismatches, then collect
   inline once. If the inline run still mismatches, STOP and show them.
3. **Print every entry of `lines`, exactly** — the `Fetch:`, `Enrichment:`, and `Readiness:` lines, built by
   code from the files themselves.
4. **`missing` is not empty** → a configured source could not be fetched; the collector's `failed` has the
   error. STOP: name each missing source and its error, and ask whether to continue with partial data or
   abort. Continue only on an explicit yes, and mark the final output **PARTIAL** with the missing source
   named.
5. **`enrichment.failed` is not empty** → list the docs that could not be fetched (URL and error), and ask
   once whether to continue without them.
6. **`enrichment.pending` is above zero** (the `mcp` enrichment strategy) → fetch those docs yourself: the
   URLs are under `pending` in `<run_dir>/docs.json`; follow
   `${CLAUDE_PLUGIN_ROOT}/providers/github/fetch-mcp.md` for each, as the analysis reaches the requirements
   that cite them, and print `Enrichment: fetched K of N GitHub-linked requirement docs`.
7. Relay every entry of `warnings`.

## Step 3: Read the run

- **`links.json` first.** Its `counts`, `untested`, `orphans`, `stranded`, `unknown_linked_ids`, and
  `features` rows are computed by code from every record in the run — **use them as given, never recount**.
  The lists stop at 200 entries (`lists_truncated`); the counts never do.
- **Then one feature at a time,** in the order of `features`: every part of its bundle
  (`features[].bundles`), **and every doc its header lists** (`features[].docs`) — read each doc once per run;
  when a later feature lists one you have already read, use what you read. Each requirement appears with its
  status, folder, description, rationale, custom fields (platform, product, version, trace fields), linked
  docs, and the test cases that link to it — each case with its priority, type, custom fields (applicable OS,
  product version, CtQ), and steps. A case linked to several requirements is written out once and referred
  to after that. Each feature ends with its orphan cases. Release a feature's text before reading the next.
- **A single record's raw fields** (a test case's `updated_on`, a requirement's Jama id): search
  `requirements.json` or `test_cases.json` for its ID. Never read those files whole.

## Follow-ups

The run folder outlives the command, so a follow-up about the same scope reads the same files — no
re-fetch. A different scope is a new run.
