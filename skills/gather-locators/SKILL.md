---
name: gather-locators
description: Read the Test Cases in TestRail folders and write the Walk Plan that a later walk of the app follows.
disable-model-invocation: true
---

# /bc:gather-locators — Write the Walk Plan

Read the Test Cases in one or more TestRail folders and write a **Walk Plan** into the Test Project: every Screen
State the cases mention, the components and labels on each screen, the Conditions each Screen State needs, and the
fewest Walks that visit every Screen State on each Platform (ADR 0011). Walking the phone with it is a later phase:
this command stops once the Walk Plan is written and shown.

**Plugin root:** two folders above this skill's base directory, which is `<plugin root>/skills/gather-locators/`. Every file these instructions name — `howtos/…`, `providers/…`, `scripts/…`, `com.github.copilot/agents/…` — is a path from the plugin root, and so is every file those files name.

## Usage

```
/bc:gather-locators https://acme.testrail.io/index.php?/suites/view/9&group_id=120
/bc:gather-locators App/Settings 'App/Log in'
```

## Arguments

The text the user typed after the command: one or more TestRail folder links or folder paths, mixed freely. Each
folder includes its subfolders. With nothing typed, ask once for them.

## Who does what

Code does every step it can, and checks every agent's output before anything builds on it. Three hidden agents on a
low-cost model do the rest, each one job: `bc:walk-collector` fetches, `bc:walk-extractor` reads one batch of cases,
`bc:walk-refiner` merges Walks. You decide only the near-duplicate names, and you never read a case.

To start an agent:

- **Claude Code** — your sub-agent tool takes `subagent_type`: pass `subagent_type: "bc:<agent>"` and
  **`model: "haiku"`**.
- **Copilot CLI** — your sub-agent tool takes `agent_type`: pass `agent_type: "bc:<agent>"` and **no `model`**; the
  agent's own definition picks a low-cost one.
- **Any other assistant** whose sub-agent tool lists the agent — pass no `model`.

**Inline.** An assistant with no sub-agents (Codex, Cursor), or one whose sub-agent tool doesn't list the agent, runs
the agent's steps itself: follow its file — `com.github.copilot/agents/walk-collector.agent.md`,
`com.github.copilot/agents/walk-extractor.agent.md`, `com.github.copilot/agents/walk-refiner.agent.md` — with the
same inputs, and print `<agent>: ran inline — <reason>` once. Same files, same checks; only the cost differs.

Every agent's prompt starts with `plugin_root` (the absolute plugin root) and `run_dir` (the run folder), one
`name: value` per line.

## Execution

Print every `lines` entry a script prints, as it is, each on its own line.

1. **Load settings.** Follow `howtos/load-settings.md` with `--need test_cases` and no `--reads`: it finds the
   project folder — the Test Project — and stops when the settings or the test suite aren't ready. Its rules hold
   for the whole command: each script runs as one plain command, in the shape shown here, with no `cd` and nothing
   chained; files are read with your view tool; answers stay small. Read `test_cases` from
   `<project>/.buddy-council/sources.json`: `base_url` (the `site` of every TestRail call), `project_id`,
   `suite_id`.

2. **Resolve the folders** to TestRail folder paths, such as `App/Settings`:
   - A path is used as typed.
   - A link (`…/index.php?/suites/view/<suite>&group_id=<section>`) names a folder by its section id. A link to
     another suite than `suite_id` is refused: say which suite it is and that this project reads suite
     `<suite_id>`. A link without `group_id` is the whole suite: ask for its folders' links instead.
   - For links, call `testrail_get_sections` once, with `site`, `project_id`, and `suite_id`, and take each linked
     section's `path`. A section id it doesn't list is refused by name.

   Then create the run folder:

   ```bash
   python3 '<plugin root>/scripts/bc_run.py' new --project '<project>' --command gather-locators --scope '<the folder paths, joined by "; ">'
   ```

   Its `run_dir` holds everything this run writes: the fetched cases, the batches, the fragments, the drafts, and
   `refine-failures.json`. None of it is committed.

3. **Collect.** Start `bc:walk-collector` with `plugin_root`, `run_dir`, and `folders` (the paths, one per
   `folders:` line), and wait. It fetches each folder, has the script check the cases against the settings, and
   returns the batch list: `batches` (each `{id, file, folder, cases, fragment}`) and `failed`. Relay each entry
   of `failed`. With no batches, stop and say why.

4. **Extract.** Start one `bc:walk-extractor` per batch, all at once, each with `plugin_root`, `run_dir`, and its
   batch's `batch` (the id), `file`, and `fragment`. When they're done, validate each batch:

   ```bash
   python3 '<plugin root>/scripts/bc_walk.py' validate '<run_dir>' <batch>
   ```

   On `"retry": true`, start that batch's extractor once more with the same inputs plus `errors`: the `errors`
   list, as it printed. Then validate again. A batch that fails again prints a `line` naming its cases: print it.
   Nothing is dropped silently.

5. **Merge** every valid fragment into `graph.json`:

   ```bash
   python3 '<plugin root>/scripts/bc_walk.py' merge '<run_dir>'
   ```

   `near_duplicates` lists the pairs of names the script couldn't settle, each with its `kind`, `ids`, and
   `names`. Decide only these: the same thing named two ways, or two things. When the list isn't empty, merge
   again with your answer — every pair you judge the same, as `[keep id, drop id]`; a pair left out is two things;
   `'[]'` when none are the same:

   ```bash
   python3 '<plugin root>/scripts/bc_walk.py' merge '<run_dir>' --same '[["<keep id>", "<drop id>"]]'
   ```

   A `more_near_duplicates` count means the list was cut: answer the next list it prints the same way.

6. **Plan** separate minimal Walks per Platform:

   ```bash
   python3 '<plugin root>/scripts/bc_walk.py' plan '<run_dir>'
   ```

7. **Refine, then check.** Start `bc:walk-refiner` with `plugin_root`, `run_dir`, and `attempt` 1. It writes
   `<run_dir>/refined-1.json`. Check it:

   ```bash
   python3 '<plugin root>/scripts/bc_walk.py' check '<run_dir>' refined-1.json
   ```

   - `"status": "ok"` — `walks.json` holds the refined Walks.
   - `"status": "retry"` — start the refiner once more with `attempt` 2 and `failures`: the `failures` list, as
     it printed. Check `refined-2.json` the same way.
   - `"status": "failed"` — `walks.json` holds plan's separate Walks, the fallback. Print the failures, then end
     the report with **one** suggested change to `com.github.copilot/agents/walk-refiner.agent.md` that would
     have avoided them, for the plugin's maintainers. Never edit the agent file.

   When the refiner can't run at all, check plan's own Walks instead (`check '<run_dir>' planned-walks.json`), so
   `walks.json` holds them.

8. **Render** `walks.md` and the summary:

   ```bash
   python3 '<plugin root>/scripts/bc_walk.py' render '<run_dir>'
   ```

9. **Publish** the Walk Plan into the Test Project:

   ```bash
   python3 '<plugin root>/scripts/bc_walk.py' publish '<run_dir>'
   ```

   It writes `graph.json`, `walks.json`, and `walks.md` into `walk-plan/` in the Test Project, beside
   `.buddy-council/`, replacing the last plan's three files. They are meant to be committed and reviewed with the
   Test Project.

## Report

Show render's `lines` and the `walk-plan/` folder publish wrote, then the Walks in `<project>/walk-plan/walks.md`
— read it with your view tool. Use the counts render printed; never count again. Name everything left out on the
way: folders the collector couldn't fetch, batches that failed twice, Screen States no Walk reaches, and the
refiner's fallback with its suggested change. When render says two plans are recommended, say so. Then stop:
Walking the phone is a later phase, and this command never starts it.
