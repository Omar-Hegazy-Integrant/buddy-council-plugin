# Buddy-Council Plugin

A Claude Code plugin for multi-agent requirements/test-case analysis.

## What This Plugin Does

Buddy-Council helps teams detect contradictions, inconsistencies, and alignment gaps between **requirements** (from Jama or Excel) and **test cases** (from TestRail). It fetches data live — no RAG, no embeddings, no vector storage.

## Architecture

- **Commands** (`commands/`) — user-facing entry points (`/bc:contradiction`, `/bc:coverage`, `/bc:ask`, `/bc:setup`, `/bc:onboarding`, `/bc:validate`, `/bc:codemap`)
- **Agents** (`agents/`) — reasoning engines that orchestrate skills to complete tasks
- **Skills** (`skills/`) — reusable capabilities (fetch data, normalize, analyze, enrich, map to code)
- **Providers** (`providers/`) — platform-specific data fetching instructions (TestRail, Excel, Jama, GitHub)
- **MCP Servers** (`mcp-servers/`) — standalone MCP servers wrapping external APIs (TestRail, Jama). GitHub access is handled either via the official external `github-mcp-server` (not vendored) or via the `gh` CLI.
- **Config** (`config/`) — source selection and non-secret configuration

## Data Flow

```
Command → Agent → Skills (fetch → normalize → analyze) → Human-readable report
```

Requirements and test cases are fetched live from configured sources, normalized to a canonical schema, linked by ID references, then analyzed by Claude.

## Key Conventions

- All commands use the `bc:` prefix
- **README stays current**: any change that alters user-visible behavior — commands, the setup flow, the config schema, prerequisites, permissions, install or release steps — must update `README.md` in the same change set. A stale README is part of the bug, not a follow-up.
- **Commands are the only user-facing surface.** Skills are internal implementation details: every SKILL.md carries `user-invocable: false` (hides it from Claude Code's `/` menu) and a description starting with `Internal (used by /bc:…) —` so runtimes that still list skills (Copilot CLI) make the distinction obvious. Keep both markers when adding a skill.
- No hardcoded secrets — credentials live only in `~/.buddy-council/secrets.json` (gitignored, `chmod 600`), the single source of truth. `.mcp.json` is gitignored and holds no secrets — only non-secret env (`*_BASE_URL`) plus `BC_SECRETS_FILE` (the path the MCP servers read). The external GitHub MCP server is the lone exception (its token stays in `.mcp.json` env).
- Source configuration lives in `.buddy-council/sources.json`
- Provider skills are swappable — adding a new platform means adding a `providers/<name>/` folder
- Agents never call providers directly — they go through router skills (`fetch-requirements`, `fetch-test-cases`)
- **Data Contract**: every analysis command fetches **every configured source** — requirements and test cases always, plus GitHub doc enrichment whenever a `github_url` column is mapped and `requirements.enrichment.enabled` is true (scope narrows a fetch, never skips one). Each attempt is surfaced as a visible `Fetch:`/`Readiness:`/`Enrichment:` line, and if any configured source fails the agent stops and asks the user whether to continue with partial data — continuing marks the output **PARTIAL**. Tool calls are logged to `.buddy-council/logs/` by a bundled PostToolUse hook on **both runtimes**.
- **Hooks are dual-manifest**: `hooks/hooks.json` (Claude Code format) and the plugin-root `hooks.json` (Copilot CLI format, `version: 1`) register the same four scripts. The scripts are runtime-agnostic — they parse both payload shapes (`tool_name`/`tool_input` object vs `toolName`/`toolArgs` JSON-string) and emit both decision shapes (top-level `permissionDecision` for Copilot, `hookSpecificOutput` wrapper for Claude Code). Keep all of that intact when editing a hook, and never let a preToolUse script exit non-zero incidentally — Copilot treats that as deny (fail-closed). Copilot runs hook commands with cwd = the plugin dir, so project paths must come from `CLAUDE_PROJECT_DIR`/`COPILOT_PROJECT_DIR` or the payload's `cwd`, never the process cwd.

## Available Commands

- `/bc:setup` — Configure data sources and credentials in four steps with a single review-and-save confirmation. Auto-guesses the Excel column mapping (one confirmation), auto-selects the GitHub enrichment strategy, and auto-detects the code project. Re-running shows the current config and only changes what you ask.
- `/bc:contradiction` — Detect contradictions between requirements and test cases
- `/bc:coverage` — Find untested requirements, orphan test cases, and coverage gaps
- `/bc:ask` — Natural language query — routes to the right agent or answers directly
- `/bc:onboarding` — Walk a new team member through the product feature-by-feature with paced demos, do/don't pairs from test cases, optional code mapping when run from inside a codebase, and an assessment phase. Progress is logged to `.buddy-council/onboarding-progress.json` in the user's project root and resumes across sessions.
- `/bc:validate` — Validate Jira tickets against requirements and test cases (gap and contradiction detection)
- `/bc:codemap "<feature>"` — Map a single feature to where it lives in the current codebase (files, communication flow, per-requirement locations). Same output as the onboarding code-mapping phase, invokable standalone.

## Canonical Artifact Schema

All providers normalize data to this shape before analysis:

```json
{
  "type": "requirement | test_case",
  "id": "CWA-REQ-85",
  "title": "...",
  "description": "...",
  "rationale": "...",
  "feature": "Feature Name",
  "status": "Active",
  "linked_ids": ["TC-1234"],
  "raw_fields": {},
  "extended_context": [
    {
      "source": "github",
      "url": "https://github.com/org/repo/blob/main/docs/sds.md",
      "locator": { "owner": "org", "repo": "repo", "path": "docs/sds.md", "ref": "main", "anchor": null },
      "content": "<markdown verbatim>",
      "fetched_at": "ISO 8601 UTC",
      "referenced_images": [{ "alt": "...", "url": "https://raw.githubusercontent.com/..." }],
      "truncated": false
    }
  ]
}
```

The `extended_context` field is **optional** — populated only when:
- The provider extracted a `_enrichment_urls` transient field from the source (e.g., the Excel sheet has a `github_url` column mapped), AND
- `requirements.enrichment.enabled === true` in `.buddy-council/sources.json`, AND
- The fetch succeeded.

Every downstream skill treats it as optional and reads `description` non-exclusively, so legacy data and disabled-enrichment paths work unchanged.

## Configuration Schema Additions

`.buddy-council/sources.json` supports these additional blocks for richer onboarding and analysis:

- **`requirements.column_mapping`** — maps canonical fields (`id`, `title`, `description`, `rationale`, `status`, `item_type`, `github_url`, `feature`) to actual Excel column names. Set by the `/bc:setup` wizard. When absent, the Excel parser falls back to its legacy positional mode.
- **`requirements.feature_inference`** — `{strategy: "hierarchical_folder" | "column" | "none", folder_item_type: "Folder"}`. Controls how requirements are grouped into features. `/bc:setup` always writes `hierarchical_folder` without asking; the other strategies stay parser-supported for hand-edited configs.
- **`requirements.item_type_filter`** — array of `Item Type` values to include (everything else is ignored). Optional; never written by `/bc:setup` — hand-edit only.
- **`requirements.item_type_exclude`** — array of `Item Type` values to skip even when they carry IDs (e.g. `["Text"]` narrative rows). Written automatically by `/bc:setup` when the sheet's Item Type sample contains `Text`; hand-editable for other types.
- **`requirements.enrichment`** — `{enabled: bool, strategy: "cli" | "mcp", max_doc_chars: int}`. Drives GitHub-doc enrichment when a `github_url` column is mapped.
- **`project`** — `{enabled: bool, ignore_dirs: [string], id_patterns: [regex]}`. Controls code mapping for `/bc:onboarding` and `/bc:codemap`. When `enabled` is true and cwd contains code markers, the onboarding agent runs a code-mapping phase between demo and assessment for each feature.
- **`plugin_root`** — absolute path to the plugin's install directory, recorded by `/bc:setup`. Bundled files (MCP servers, the Excel parser) are referenced through it because `${CLAUDE_PLUGIN_ROOT}` only resolves under Claude Code, not Copilot CLI. Per-machine; lives only in the git-excluded config, never committed.

## Progress Log Schema Additions

`<user-project>/.buddy-council/onboarding-progress.json` `features[i]` now supports one additional optional field:

- **`code_mapping`** — `{computed_at: ISO 8601 UTC, git_sha: string|null, files: [{path, role}], flow: string, requirement_locations: [{req_id, files: [{path, lines}]}], notes: [string]}`. Written by the `map-feature-to-code` skill. Cached with surgical SHA-diff invalidation when the codebase is a git repo. Stays inside `.buddy-council/`, which is kept out of git via the repo-local `.git/info/exclude`, so it never reaches the team's repo.
