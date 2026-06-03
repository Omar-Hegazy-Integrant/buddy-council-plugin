---
description: Map a feature's requirements to where they actually live in the user's codebase. Detects cwd as a code repo, runs ID grep + keyword search + AI synthesis to produce a files+flow narrative, caches the result per feature with surgical git-SHA invalidation, and honors inline deepening commands (show snippet, show diagram, show callers, read).
---

# Map Feature to Code — Skill

After the demo phase of an onboarding feature, walk the user through where the feature actually lives in the codebase: which files implement it, how those files talk to each other, and where each requirement appears in the code.

## When to Use

Invoked by `agents/onboarding-agent.agent.md` step 6b.5 (between demo and assessment) when `requirements.project.enabled` is true (or unset, defaulting to true) AND the current working directory contains a code repo marker. Also invoked by `commands/codemap.md` for ad-hoc mapping of a single feature outside the onboarding flow.

If the cwd is NOT a code repo (no markers detected), this skill returns immediately without output. The agent silently skips this phase.

## Input

- The feature object from the onboarding plan (`{ name, requirement_ids, test_case_ids, ... }`)
- The full normalized requirements + test cases (for context when synthesizing)
- The progress log path (if running inside onboarding — for cache read/write)
- Mode flag: `"in_onboarding"` or `"standalone"`

## Configuration

Read `.buddy-council/sources.json` `project`:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `enabled` | boolean | `true` | Master switch — `false` disables this skill entirely |
| `ignore_dirs` | array of string | `["node_modules", "dist", ".next", "build", "vendor", "__pycache__", ".venv", "target"]` | Directory globs to skip during all searches |
| `id_patterns` | array of regex | `["CWA-REQ-\\d+", "TC-\\d+"]` | Regex patterns for finding requirement/test IDs inside source files |

## Procedure

### Step 1: Detect cwd as a code repo

Probe for any of these markers in the user's current working directory (NOT the plugin root):

| Marker | Indicates |
|--------|-----------|
| `package.json` | Node/JS/TS |
| `pyproject.toml`, `setup.py`, `requirements.txt` | Python |
| `Cargo.toml` | Rust |
| `go.mod` | Go |
| `pom.xml`, `build.gradle` | JVM |
| `*.csproj` | .NET |
| `Gemfile` | Ruby |
| `composer.json` | PHP |
| `.git/` | any git repo |

If NONE of these are present, return `{detected: false}` and exit silently. The agent skips the phase.

### Step 2: Detect git state for caching

Run `git rev-parse HEAD` in the cwd. Outcomes:

- **success** → store the SHA as `current_sha`; caching is enabled.
- **failure** (`not a git repository` or no commits yet) → `current_sha = null`; caching is disabled. Surface a one-line note on first run: `"Not a git repo — code maps will recompute on each session."`

### Step 3: Load cache from progress log

If `mode === "in_onboarding"` and a progress log exists:

1. Read the log via `${CLAUDE_PLUGIN_ROOT}/skills/manage-progress-log/SKILL.md`.
2. Locate the current feature's entry under `features[i]`.
3. Inspect `features[i].code_mapping`:
   - If absent or null → **cold compute** (Step 5).
   - If present and `code_mapping.git_sha === null` (no git) → **always recompute** when no git.
   - If present and `code_mapping.git_sha === current_sha` → **use cached** (Step 6).
   - If present and SHA differs → **invalidate check** (Step 4).

If `mode === "standalone"`, skip cache read — always recompute.

### Step 4: Surgical SHA-diff invalidation

When stored `code_mapping.git_sha !== current_sha`:

1. Run `git diff --name-only <stored_sha> HEAD` to get the changed file list.
   - If the stored SHA no longer exists (rebase / force-push / shallow clone), the command fails — surface a warning and fall through to cold compute.
2. Build a set of changed paths.
3. Intersect the changed-paths set with `code_mapping.files[].path`.
4. If the intersection is **empty**, the cached map is still valid: use cached (Step 6) but update `code_mapping.git_sha = current_sha` to advance the freshness pointer.
5. If the intersection is **non-empty**, cold-compute this feature (Step 5).

### Step 5: Cold compute

Produce a fresh `code_mapping` for this feature.

#### 5a. ID grep across the codebase

For each ID in `id_patterns`, run `git grep -nE "<pattern>" -- '*'` (or `grep -rnE` if not a git repo). Exclude `ignore_dirs`. Collect hits as `{path, line, matched_id}`.

Filter to hits whose `matched_id` is one of the feature's `requirement_ids` or `test_case_ids`. These are **high-signal** candidates — likely traceability comments.

#### 5b. Keyword search

Extract distinctive terms from the feature's requirements:

- Each requirement's `title` and `description`, after stripping common words (the, and, of, with, a, etc.).
- Tokens longer than 4 characters and not all-numeric.
- Cap at 10 distinct terms across the whole feature.

For each term, run `git grep -lE "\\b<term>\\b" -- '*'` excluding `ignore_dirs`. Collect candidate files (no need for line numbers at this stage).

Merge ID-grep hits and keyword hits into a single candidate list. Bound at **200 candidate files** — if more are produced, sort by hit count and take the top 200. Log a one-line note when this happens.

#### 5c. AI-driven file synthesis

Of the candidate files:

1. Sort by signal strength: files with ID-grep hits first, then files appearing under multiple keyword terms, then single-term keyword hits.
2. Cap at **50 deeply read files**. Read each via the Read tool, focusing on the first ~100 lines (which usually contain imports, exports, and the module's purpose).
3. For each file, identify its **role**: is it a UI component, a hook/composable, an API client, a route handler, a service/repository, a model/entity, a test, etc. One-line role string.
4. Drop files whose role is clearly noise (build configs, generated code, vendored deps that snuck through the ignore filter).
5. Cluster the surviving files into a small set (typically 4–8 entries) that constitute the feature's implementation. Discard candidates that don't fit.

#### 5d. Flow narrative

Synthesize a short prose description of how the identified files communicate. Use the imports and exports observed in 5c. Stay concrete: name the call chain, mention the protocol (HTTP, SSE, function call, event bus), and surface anything noteworthy ("uses Server-Sent Events", "frontend + backend split", "writes to Postgres").

Do NOT speculate. If you can't trace a flow with high confidence, say so in the `notes` array instead of inventing one.

#### 5e. Per-requirement locations

For each `requirement_id` in the feature:

1. Look for that exact ID via `git grep` again (cheap second pass).
2. For each hit, record `{path, lines: "<line> or <first>-<last>"}`.
3. If no ID hit, look for keyword overlap with the requirement's title — record the best-match file + estimated line range.
4. If still no signal, mark the requirement as `unlocated` in the `notes`.

#### 5f. Assemble code_mapping

```json
{
  "computed_at": "<ISO 8601 UTC>",
  "git_sha": "<current_sha or null>",
  "files": [
    { "path": "src/features/x.tsx", "role": "<one-line role>" }
  ],
  "flow": "<prose narrative>",
  "requirement_locations": [
    { "req_id": "REQ-001", "files": [{ "path": "src/x.tsx", "lines": "42-78" }] }
  ],
  "notes": ["<observations>", "<unlocated requirements>", "<warnings>"]
}
```

If `mode === "in_onboarding"`, write this back to the progress log under `features[i].code_mapping` via `manage-progress-log`.

### Step 6: Render the user-facing view

Print a one-screen output:

```
─────────────────────────────────────────────
  Code Mapping: <Feature Name>
─────────────────────────────────────────────

How this feature shows up in the codebase:

Implementation files
  • <path>
       <role>
  • <path>
       <role>

How they communicate
  <flow narrative, formatted as a short paragraph or arrow chain>

Where each requirement lives
  <REQ-ID> → <file>:<lines> + <file>:<lines>
  <REQ-ID> → <file>:<lines>

Notes
  - <note 1>
  - <note 2>

[next / show snippet <file> / show diagram / show callers <symbol> / read <file> / recompute code map / skip code map]
```

If a section is empty (no notes, no unlocated requirements), omit it entirely.

### Step 7: Inline deepening loop

Wait for the user's input and honor these commands:

| Input | Behavior |
|-------|----------|
| `next` / Enter | Return control to the agent → assessment phase begins. |
| `show snippet <path>` | Read the file. Identify the most relevant 10-15 line block (around the line ranges in `requirement_locations`, or the top exported function). Print it in a fenced code block. Stay in the inline loop. |
| `show diagram` | Emit a mermaid sequence diagram synthesized from `flow`. Diagram source goes in a ```mermaid block. Stay in the inline loop. |
| `show callers <symbol>` | Run `git grep -nE "\\b<symbol>\\b"` excluding `ignore_dirs`. List up to 20 hits as `<path>:<line>`. Stay in the inline loop. |
| `read <path>` | Read the full file (or up to 500 lines if longer) and print inline. Stay in the inline loop. |
| `recompute code map` | Force a fresh cold compute (skip cache). Update the log. Re-render. Stay in the inline loop. |
| `skip code map` | Return control to the agent → assessment phase. |
| `skip feature` | Propagate to the agent (mark feature skipped, advance to next). |
| `pause` | Save log, exit cleanly. |
| Unknown input | Re-prompt with the controls list. Do not advance. |

### Step 8: Handle special cases

#### No detected matches

If Step 5 produces zero implementation files, render:

```
No clear implementation found in this codebase for <Feature Name>.

This may be because:
  (a) the feature is not yet implemented
  (b) it's implemented under unfamiliar names that didn't match the keyword search
  (c) it lives in a different repo

Options:
  hint <directory>   — narrow the search to a directory you suspect
  broaden            — drop keyword filtering, search by IDs only
  skip code map      — jump to assessment
```

On `hint`, restrict the candidate file set to the named directory and re-run Step 5. On `broaden`, re-run Step 5 with `keyword_terms = []` (ID grep only). On `skip code map`, return control to the agent.

#### Mostly test matches

If >70% of identified files are tests (filenames matching `*test*`, `*.spec.*`, `__tests__/`), surface a note: `"Most matches are in tests — implementation may live elsewhere or under a different name."` Still present the result.

#### Long-running search

If Step 5b's candidate gathering takes more than ~30s of wall-clock work, log progress lines: `"scanning 14/200 files…"`. Allow the user to type `cancel` to stop and render whatever's been found so far with a `partial: true` note.

#### Cache schema mismatch

If `code_mapping` exists in the log but has fields we don't recognize, OR is missing required fields, treat it as stale and cold-compute. Log a one-line warning.

## Output to the Agent

When this skill yields control (via `next` or `skip code map`), it returns one of:

- `{phase: "advance"}` — proceed to assessment
- `{phase: "skip_feature"}` — agent marks feature skipped
- `{phase: "pause"}` — agent writes log, exits cleanly

Plus the side effect of having written `features[i].code_mapping` to the progress log (if `mode === "in_onboarding"`).

## Error Handling

| Situation | Behavior |
|-----------|----------|
| `project.enabled === false` in config | Should not be invoked. If invoked anyway, return `{detected: false}`. |
| Codebase has no detection markers | Return `{detected: false}` — agent skips silently. |
| `git rev-parse HEAD` fails (no git) | Disable caching, recompute each run, one-line note. |
| Stored SHA no longer exists | Warn, fall through to cold compute. |
| Read of a candidate file fails (permission, deleted between grep and read) | Skip that file, log warning, continue. |
| Progress log write fails | Surface the error; continue rendering the result. Don't fail the whole phase over a write error. |

## Guidelines

- **Read-only on the project's git.** Never write to the user's repo state. We `git rev-parse`, `git diff --name-only`, and `git grep` — that's it.
- **Bound everything.** 200 candidates, 50 deep reads, 30s soft time budget. Hard limits prevent runaway scans on huge monorepos.
- **Don't speculate flow.** If imports don't show a connection between two files, don't invent one. Use `notes` to flag uncertainty.
- **Cite back to requirements always.** Every implementation file should be tied to at least one `requirement_id`, ideally via line numbers.
- **Quiet for skips, terse for output.** Render in one screen by default. Inline commands deepen on demand.
- **Cache lives in the user's project, ignored locally.** The `code_mapping` field sits inside `.buddy-council/onboarding-progress.json`; `.buddy-council/` is kept out of git via the repo-local `.git/info/exclude`, so it's never committed to the team's repo.
