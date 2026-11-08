---
name: codemap
description: "Map a feature to where it lives in the current codebase: files, communication flow, and per-requirement locations."
disable-model-invocation: true
---

# /bc:codemap — Map a Feature to the Codebase

Show where a specific feature lives in the current codebase: which files implement it, how they communicate, and where each linked requirement appears in code. This is the same code-mapping output produced by `/bc:onboarding` between demo and assessment, but invokable on its own.

**Plugin root:** two folders above this skill's base directory, which is `<plugin root>/skills/codemap/`. Every file these instructions name — `howtos/…`, `providers/…`, `scripts/…`, `com.github.copilot/agents/…` — is a path from the plugin root, and so is every file those files name.

## Usage

```
/bc:codemap "Patient Monitoring"      # Map a feature by name
/bc:codemap CWA-REQ-85                # Map the feature containing this requirement
/bc:codemap                           # Prompt for a feature name (interactive)
```

## Arguments

The text the user typed after the command: a feature name, a requirement ID, or nothing.

## Execution

1. Follow `howtos/load-settings.md` with `--reads test_cases`: it finds the project folder and stops when the settings aren't ready.

2. Verify that the current working directory contains code repo markers (per `howtos/map-feature-to-code.md` Step 1). If not, tell the user:
   > This command needs to run from inside a code repository. The current directory doesn't look like one — no `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `*.csproj`, `Gemfile`, `composer.json`, `requirements.txt`, `setup.py`, or `.git/` was found here.

3. Parse the argument:
   - **Feature name in quotes** (e.g., `"Patient Monitoring"`) → run code mapping for that feature
   - **Requirement ID** (e.g., `CWA-REQ-85`) → fetch requirements, find which feature this ID belongs to, run code mapping for that feature
   - **Empty** → interactively prompt the user for a feature name (offering the list from the onboarding progress log if it exists)

4. Locate the feature definition. Try in order:
   a. **Onboarding progress log** at `<project>/.buddy-council/onboarding-progress.json` — read `features[]` and find the matching `name` (case-insensitive) or the entry whose `requirement_ids` contains the given ID. Use that feature's stored requirement IDs and test case IDs. This is the cheapest path.
   b. **Live fetch** — if no progress log exists or the feature isn't in it, follow `howtos/fetch-requirements.md` and `howtos/fetch-test-cases.md` with the feature name as scope, then normalize via `howtos/normalize-artifacts.md`.
   c. **No match** — if neither finds the feature, list the available features (from the log, if present) and ask the user to pick one.

5. Follow `howtos/map-feature-to-code.md` in **standalone mode**:
   - Pass `mode: "standalone"`
   - Pass the feature object
   - Pass the requirements and test cases
   - The how-to skips the in-onboarding cache read and always cold-computes (per its documented behavior in standalone mode)

6. The how-to renders the standard code-mapping output (Implementation files / How they communicate / Where each requirement lives / Notes) and enters its inline deepening loop (`show snippet`, `show diagram`, `show callers`, `read`, `recompute code map`).

7. When the user types `next`, `done`, or exits, return to the prompt.

## What This Command Does NOT Do

- Does NOT mutate `<user-project>/.buddy-council/onboarding-progress.json`. Even if the feature is found in the log, the standalone run is read-only on the cache; results are NOT persisted.
- Does NOT run the demo or assessment phases — pure code mapping only.
- Does NOT require an active onboarding journey. You can run this any time after `/bc:setup` has configured the data sources.

## Important

- This command needs Read access to your code files. It does not modify, build, or run the codebase.
- All git operations (`git rev-parse HEAD`, `git diff --name-only`, `git grep`) are read-only on the project's git state.
- The code-mapping how-to is bounded at 200 candidate files / 50 deep reads per feature. Very large monorepos may need a `hint <directory>` to narrow the search — the how-to prompts for this if no results are found.
