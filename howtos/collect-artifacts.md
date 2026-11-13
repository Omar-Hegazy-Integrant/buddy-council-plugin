# Collect Artifacts

The mechanical part of an analysis run — fetching, waiting out a slow Jama read, paging, cross-linking IDs,
counting — needs no judgment, so it runs on a **low-cost model** in the `bc:data-collector` sub-agent, and
the counting runs as plain code. The servers and the link script write everything into a **run folder**;
your model only reads the parts it needs. Nothing is fetched twice, and no requirement or test case text is
ever retyped by a model.

```
<project>/.buddy-council/runs/<run-id>/
  requirements.json  test_cases.json   full canonical records (written by the Jama/TestRail servers)
  background.json                      lookups on Jama only: the rest of the project, context for answers
  product.json                         contradiction runs on part of the product: the whole product, to compare it with
  docs/  docs.json                     GitHub docs the requirements link to (cli strategy)
  links.json                           counts, untested, orphans, stranded links, per-feature rows — computed by code
  bundles/NN-<feature>-<p>.md          each feature's requirements with their test cases, verbatim, in small parts
  manifest.json
```

`background.json` holds every Jama item outside the requirements in scope — system requirements, risks,
standards, design — for `/bc:ask` lookups to search. It is never a requirement: no count, bundle, or report
includes it.

`product.json` holds every requirement in the product, read once more with the scope left out, when a contradiction
run covers only a requirement or a feature; the test cases are read whole in every run. Code matches the scope with
the rest of the product on the subjects they share. No count, bundle, or coverage number includes the rest.

The newest 5 runs are kept, none longer than a week. The folder sits inside `.buddy-council/`, which
`/bc:setup` keeps out of git.

## Step 1: Launch the collector

Start the `bc:data-collector` sub-agent and wait for it. Its prompt is four lines:

```
project: <absolute path of the project folder — the one howtos/load-settings.md found>
command: <coverage | contradiction | lookup>
scope: <the scope exactly as the user gave it, or all>
plugin_root: <absolute path of the plugin root>
```

How to pick its model depends on the assistant:

- **Claude Code** — your sub-agent tool takes `subagent_type`: pass `subagent_type: "bc:data-collector"` and
  **`model: "haiku"`**.
- **Copilot CLI** — your sub-agent tool takes `agent_type`: pass `agent_type: "bc:data-collector"` and **no
  `model`**. The agent's own definition picks a low-cost model (`claude-haiku-4.5`, else `gpt-5-mini`), and a
  user can change it in `/subagents`.
- **Any other assistant** whose sub-agent tool lists `bc:data-collector` — pass no `model`.

**Inline.** When your assistant has no sub-agent tool or doesn't list `bc:data-collector`, or the sub-agent
does not return a `run_dir`, print `Collect: ran inline — the low-cost collector was unavailable (<reason>)`
and carry out the steps of `com.github.copilot/agents/data-collector.agent.md` yourself, with the same four
inputs. Same files, same result — only the cost differs.

## Step 2: Check the run, then report it — the Data Contract

Code wrote `links.json` from the files the servers wrote, so it — not the collector's reply — is what you trust.
It is for code (a whole project's runs it to about 100 KB); read what you need of it through the script, as its own
command:

```
python3 '<plugin root>/scripts/bc_run.py' summary --run '<run_dir>'
```

1. **It answers `"ok": false`** (no `links.json`) → the collection did not finish; the collector's `failed` says
   why. Collect again inline (the fallback above), once. If that fails too, STOP and report the error.
2. **Wrong run** → `scope.input` is not the scope you asked for, or `mismatches` is not empty — each mismatch
   is a tool the collector called with arguments that differ from the project's config (a missing item-type
   filter, a different suite, a filtered test case read). Print
   `Collect: the run did not match the config — collecting again inline` and the mismatches, then collect
   inline once, into a new run folder. If the inline run still mismatches, STOP and show them.
3. **Print every entry of `lines`, exactly** — the `Fetch:`, `Enrichment:`, and `Readiness:` lines, built by
   code from the files themselves (a lookup on Jama adds a `Fetch: background` line, and a contradiction run on part
   of the product a `Fetch: the rest of the product` line). Print them as they are,
   each on its own line: never shorten or merge them.
4. **`missing` is not empty** → a configured source could not be fetched; the collector's `failed` has the
   error. STOP: name each missing source and its error, and ask whether to continue with partial data or
   abort. Continue only on an explicit yes, and mark the final output **PARTIAL** with the missing source
   named.
5. **`enrichment.failed` is not empty** → list the docs that could not be fetched (URL and error), and ask
   once whether to continue without them.
6. **`enrichment.pending` is above zero** (the `mcp` enrichment strategy) → those docs are read through the
   assistant's own GitHub connection, `providers/github/fetch-mcp.md`, by whoever reads the requirements that cite
   them: each bundle names them under those requirements. When that is you, print
   `Enrichment: fetched K of N GitHub-linked requirement docs` after the last.
7. Relay every entry of `warnings`.

## Step 3: Read the run

- **The summary first.** Its `counts`, `features` rows, and `unknown_linked_ids` are computed by code from every
  record in the run — **use them as given, never recount**. Lists stop at 200 entries (`lists_truncated`); the
  counts never do.
- **Then one feature at a time,** in the order of `features`:

  ```
  python3 '<plugin root>/scripts/bc_run.py' summary --run '<run_dir>' --feature '<feature>'
  ```

  It gives the feature's bundle parts and the docs they cite, as paths, and its `untested`, `orphans`, and
  `stranded` entries. Read every part with your view tool, one part per read — each is sized to fit — **and every
  doc**; read each doc once per run, and when a later feature lists one you have already read, use what you read.
  Each requirement appears with its status, folder, description, rationale, custom fields (platform, product,
  version, trace fields), linked docs, and the test cases that link to it — each case with its priority, type,
  custom fields (applicable OS, product version, CtQ), and steps. A case linked to several requirements is
  written out once and referred to after that. Each feature ends with its orphan cases. Release a feature's text
  before reading the next.
- **A single record** — its text, fields, Jama link, and the cases that test it:
  `python3 '<plugin root>/scripts/bc_run.py' show --run '<run_dir>' --ids '<ID>'`. For a raw field it doesn't
  print (a case's `updated_on`), search `test_cases.json` or `requirements.json` for the ID with your grep tool.
  Never read `requirements.json`, `test_cases.json`, or `links.json` whole.

## Follow-ups

The run folder outlives the command, so a follow-up about the same scope reads the same files — no
re-fetch. A different scope is a new run.
