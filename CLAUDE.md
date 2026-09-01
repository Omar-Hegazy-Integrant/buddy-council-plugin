# Buddy-Council Plugin

A Claude Code plugin for multi-agent requirements/test-case analysis.

## What This Plugin Does

Buddy-Council helps teams detect contradictions, inconsistencies, and alignment gaps between **requirements** (from Jama or Excel) and **test cases** (from TestRail). It fetches data live — no RAG, no embeddings, no vector storage.

## Architecture

- **Commands** (`commands/`) — user-facing entry points (`/bc:contradiction`, `/bc:coverage`, `/bc:ask`, `/bc:setup`, `/bc:onboarding`, `/bc:validate`, `/bc:codemap`, `/bc:vnv-sprint-prep`)
- **Agents** (`agents/`) — reasoning engines that orchestrate skills to complete tasks
- **Skills** (`skills/`) — reusable capabilities (fetch data, normalize, analyze, enrich, map to code)
- **Providers** (`providers/`) — platform-specific data fetching instructions (TestRail, Excel, Jama, GitHub)
- **MCP Servers** (`mcp-servers/`) — standalone MCP servers wrapping external APIs (TestRail, Jama). GitHub access is handled either via the official external `github-mcp-server` (not vendored) or via the `gh` CLI. Jira/Confluence use **[`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian)** (`uvx mcp-atlassian@0.23.1`, stdio) — also not vendored: `/bc:setup` writes it into `.mcp.json` for Claude Code and `~/.copilot/mcp-config.json` for Copilot CLI, both with absolute paths. Never vendor a Jira server again. It replaced Atlassian's hosted `mcp.atlassian.com` server in 0.21.1 for two reasons: the hosted one serves **Atlassian Cloud only**, so a Jira Server/Data Center team could never connect, and it exposes **no board or sprint tools**, which forced every board read to be faked with a project-wide JQL.
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
- No hardcoded secrets — credentials live only in `~/.buddy-council/secrets.json` (gitignored, `chmod 600`), the single source of truth. `.mcp.json` is gitignored and holds no secrets — only non-secret env (`*_BASE_URL`) plus `BC_SECRETS_FILE` (the path the MCP servers read). The external GitHub MCP server is the lone exception (its token stays in `.mcp.json` env). **Jira is the one credential that does not live in `secrets.json`** — `mcp-atlassian` reads a dotenv file, so its token goes in `~/.buddy-council/atlassian.env` (`chmod 600`, same directory, same rules) and never into `secrets.json`, `.mcp.json`, or `~/.copilot/mcp-config.json`. The MCP configs carry only the absolute `--env-file` path.
- **The `atlassian` server is registered by `/bc:setup`, in both runtime configs**: `.mcp.json` for Claude Code and `~/.copilot/mcp-config.json` for Copilot CLI — exactly like `testrail`. **No plugin manifest declares it**, because a `uvx` server needs an absolute interpreter path and an absolute env-file path, and a committed manifest can hold neither. (Copilot could never use a manifest entry anyway: it doesn't merge plugin-declared MCP servers into its runtime config, [#2709](https://github.com/github/copilot-cli/issues/2709).)
- **MCP config is dual-file, like hooks**: Claude Code reads the project/plugin `.mcp.json`; Copilot CLI reads `~/.copilot/mcp-config.json` and **ignores `.mcp.json` entirely**. `/bc:setup` writes both on every run, merging into the Copilot file rather than overwriting it. Copilot's schema additionally requires `type: "local"` and a `tools` allowlist per server, and neither file may rely on `PATH` or `~` expansion — `command` must be the absolute `uv` path and `BC_SECRETS_FILE` a fully expanded path, because the spawned server inherits neither.
- Source configuration lives in `.buddy-council/sources.json`
- Provider skills are swappable — adding a new platform means adding a `providers/<name>/` folder
- Agents never call providers directly — they go through router skills (`fetch-requirements`, `fetch-test-cases`, `fetch-board-issues`)
- **Data Contract**: every analysis command fetches **every configured source** — requirements and test cases always, plus GitHub doc enrichment whenever a `github_url` column is mapped and `requirements.enrichment.enabled` is true, plus **Jira board issues** whenever `jira.board` is configured and `jira.pending` is not true (scope narrows a fetch, never skips one). An unconfigured or still-`pending` board is *skipped with a visible line, not failed* — it does not make the run PARTIAL; a configured board that errors does. Each attempt is surfaced as a visible `Fetch:`/`Readiness:`/`Enrichment:` line, and if any configured source fails the agent stops and asks the user whether to continue with partial data — continuing marks the output **PARTIAL**. Tool calls are logged to `.buddy-council/logs/` by a bundled PostToolUse hook on **both runtimes**.
- **`/bc:vnv-sprint-prep` writes to other teams' tickets, so it is dry-run by default.** A plain run performs zero `jira_create_issue`/`jira_add_comment`/`jira_update_issue`/`jira_create_issue_link`/`jira_add_issues_to_sprint` calls and prints a plan; `--apply` executes after one batch confirmation *per phase*. Never widen this: the auto-approve hook deliberately excludes every Jira write tool, and no `--allow-tool` recipe may list them.
- **`mcp-atlassian` cannot attach files** — `jira_download_attachments` has no upload counterpart, so V&V scenarios live in the ticket description instead of an attached `.md`. It **can** create issue links (`jira_create_issue_link`), so clones get a real Jira link *and* keep the `src-<DEV-KEY>` label: the label is what the duplicate guard searches on, since one board query beats a link traversal per issue. A site that refuses link creation degrades to label-only rather than failing the clone.
- **Hooks are dual-manifest**: `hooks/hooks.json` (Claude Code format) and the plugin-root `hooks.json` (Copilot CLI format, `version: 1`) register the same four scripts. The scripts are runtime-agnostic — they parse both payload shapes (`tool_name`/`tool_input` object vs `toolName`/`toolArgs` JSON-string) and emit both decision shapes (top-level `permissionDecision` for Copilot, `hookSpecificOutput` wrapper for Claude Code). Keep all of that intact when editing a hook, and never let a preToolUse script exit non-zero incidentally — Copilot treats that as deny (fail-closed). Copilot runs hook commands with cwd = the plugin dir, so project paths must come from `CLAUDE_PROJECT_DIR`/`COPILOT_PROJECT_DIR` or the payload's `cwd`, never the process cwd.

## Available Commands

- `/bc:setup` — Configure data sources and credentials in four steps with a single review-and-save confirmation. Auto-guesses the Excel column mapping (one confirmation), auto-selects the GitHub enrichment strategy, and auto-detects the code project. Re-running shows the current config and only changes what you ask.
- `/bc:contradiction` — Detect contradictions between requirements and test cases
- `/bc:coverage` — Find untested requirements, orphan test cases, and coverage gaps
- `/bc:ask` — Natural language query — routes to the right agent or answers directly
- `/bc:onboarding` — Walk a new team member through the product feature-by-feature with paced demos, do/don't pairs from test cases, optional code mapping when run from inside a codebase, and an assessment phase. Progress is logged to `.buddy-council/onboarding-progress.json` in the user's project root and resumes across sessions.
- `/bc:validate` — Validate Jira tickets against requirements and test cases (gap and contradiction detection)
- `/bc:codemap "<feature>"` — Map a single feature to where it lives in the current codebase (files, communication flow, per-requirement locations). Same output as the onboarding code-mapping phase, invokable standalone.
- `/bc:vnv-sprint-prep` — Run the V&V (Validation & Verification) sprint workflow: check cross-platform parity on the dev board, clone sprint stories onto the V&V board, validate them against requirements and test cases, draft high-level scenarios, and drive label state through review. Dry-run by default; `--apply` writes. Resumable — re-run it to pick up dev answers and reviewer approvals.

## Canonical Artifact Schema

All providers normalize data to this shape before analysis:

```json
{
  "type": "requirement | test_case | board_issue",
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
- **`jira`** — `{base_url, deployment, project_key, default_issue_type, board: {url, id}, pending}`. **Written on every `/bc:setup` run — Step 3 is required, not optional.** `deployment` is `"cloud"` or `"server"`, detected from the host (`*.atlassian.net` → cloud), and decides the auth style. `board.url` is the dev board URL the user pasted and `board.id` the integer parsed out of it; `project_key` comes from that URL, or is derived from the board's first issue when a classic RapidBoard URL omits it. `pending: true` means the connection test failed — `/bc:setup` re-tests and clears it on the next run, analysis commands skip the board source while it is set, and `/bc:validate` refuses to create real tickets. **There is no `cloud_id`**: `mcp-atlassian` is bound to one site by `JIRA_URL`, so no tool takes a site id. The credential lives in `~/.buddy-council/atlassian.env`, never here.
- **`jira.vnv_board`** — `{url, id, project_key, discriminator}`. The V&V team's board, used by `/bc:vnv-sprint-prep`. Optional in `/bc:setup` (unlike the dev board) because `/bc:vnv-sprint-prep` can collect it itself. **It may share a project with the dev board** — one project with two boards is a normal Jira layout, and everything here is addressed by board id. Only a duplicate board **id** is refused. When the projects match, `discriminator` (`{kind: "label"|"component"|"issue_type"|"sprint", value, confidence, sampled_at}`) records what the V&V board's filter keys off, so clones land on it; it is `null` when the V&V board has its own project. `/bc:setup` derives it by sampling both boards and confirms once.
- **`jira.platform`** — `{field_name, field_id, values, require_parity, title_prefix_fallback}`. Drives the cross-platform parity check. `field_name` defaults to `OS`; `field_id` is the resolved custom-field id, cached after first discovery and re-discovered when stale. The title prefix is a fallback only, and any result derived from it is reported as lower-confidence.
- **`jira.vnv_workflow`** — `{reviewer: {account_id, display_name}, approval_phrases, labels}`. Who may approve scenarios and what the **four** pipeline labels are called: `pending_validation` (set at clone) → `pending_questions` → `pending_scenario_validation` → `ready_for_test_cases`. **They are mutually exclusive** — every transition removes the previous pipeline label rather than stacking, while preserving `src-<DEV-KEY>`, the platform label, and anything a human added. A ticket with no pipeline label is treated as `pending_validation`; one with several is repaired to the furthest-along and the repair is reported. `/bc:setup` writes the defaults without asking; hand-edit to rename labels.
- **`project`** — `{enabled: bool, ignore_dirs: [string], id_patterns: [regex]}`. Controls code mapping for `/bc:onboarding` and `/bc:codemap`. When `enabled` is true and cwd contains code markers, the onboarding agent runs a code-mapping phase between demo and assessment for each feature.
- **`plugin_root`** — absolute path to the plugin's install directory, recorded by `/bc:setup`. Bundled files (MCP servers, the Excel parser) are referenced through it because `${CLAUDE_PLUGIN_ROOT}` only resolves under Claude Code, not Copilot CLI. Per-machine; lives only in the git-excluded config, never committed. **It is a cache, not the source of truth**: Claude Code's install path embeds the plugin version, so a stored value goes stale on every update. Runtime shell snippets resolve `${CLAUDE_PLUGIN_ROOT}`/`$COPILOT_PLUGIN_ROOT` first and fall back to `plugin_root`; `/bc:setup` Step 0a re-resolves on every run and silently repairs both `sources.json` and `.mcp.json` when the path drifted. Never add a code path that trusts the stored value without validating it exists.

## V&V Progress Log

`<user-project>/.buddy-council/vnv-progress.json`, written by `/bc:vnv-sprint-prep`. One entry per sprint story:
`{version: 1, sprint, dev_board_id, vnv_board_id, started_at, updated_at, stories: [{dev_key, vnv_key,
platform, platform_source, counterpart, parity, state, concerns, concern_comment_id, concern_posted_at,
scenarios_written_at, approved_by, approved_at, last_checked_at}]}`. Timestamps are ISO 8601 UTC.

**Jira labels are the source of truth, not this file.** `state` mirrors the ticket's pipeline label; when
they disagree the next run follows Jira and repairs the file. The log exists to remember *why* a ticket is
where it is (which concerns were raised, when, and who approved) — never to decide where it is.

Lives inside `.buddy-council/`, kept out of git via the repo-local `.git/info/exclude`, like the onboarding log.

## Progress Log Schema Additions

`<user-project>/.buddy-council/onboarding-progress.json` `features[i]` now supports one additional optional field:

- **`code_mapping`** — `{computed_at: ISO 8601 UTC, git_sha: string|null, files: [{path, role}], flow: string, requirement_locations: [{req_id, files: [{path, lines}]}], notes: [string]}`. Written by the `map-feature-to-code` skill. Cached with surgical SHA-diff invalidation when the codebase is a git repo. Stays inside `.buddy-council/`, which is kept out of git via the repo-local `.git/info/exclude`, so it never reaches the team's repo.
