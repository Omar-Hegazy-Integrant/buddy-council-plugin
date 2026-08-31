# Buddy-Council Plugin Architecture

## Overview

Buddy-Council is a Claude Code plugin that detects contradictions, inconsistencies, and alignment gaps between requirements and test cases. It fetches data live from external systems — no RAG, no embeddings, no vector storage.

## Directory Layout

```
buddy_council_plugin/
├── .claude-plugin/
│   ├── plugin.json              # Plugin identity (name: "bc")
│   └── marketplace.json         # Marketplace catalog for distribution
├── commands/                    # User-facing slash commands
│   ├── contradiction.md         # /bc:contradiction — direct contradiction analysis
│   ├── coverage.md              # /bc:coverage — direct coverage analysis
│   ├── ask.md                   # /bc:ask — intent classification + routing
│   └── setup.md                 # /bc:setup — onboarding and configuration
├── agents/                      # Reasoning engines
│   ├── contradiction-agent.agent.md   # Orchestrates the contradiction workflow
│   └── coverage-agent.agent.md        # Orchestrates the coverage workflow
├── skills/                      # Reusable capabilities (SKILL.md with frontmatter)
│   ├── fetch-requirements/      # Router: delegates to configured provider
│   ├── fetch-test-cases/        # Router: delegates to configured provider
│   ├── normalize-artifacts/     # Clean, normalize IDs, cross-link
│   ├── detect-contradictions/   # Core analysis (7 contradiction types)
│   └── analyze-coverage/        # Coverage gap detection and metrics
├── providers/                   # Platform-specific data fetching instructions
│   ├── excel/fetch.md           # Jama Excel export parser
│   ├── testrail/fetch.md        # TestRail via MCP tools
│   └── jama/fetch.md            # Jama API (placeholder, auth blocked)
├── mcp-servers/                 # Standalone MCP servers wrapping external APIs
│   ├── testrail-server/         # Python MCP server for TestRail REST API
│   │   ├── server.py            # 5 read-only tools (projects, suites, sections, cases, case)
│   │   └── pyproject.toml       # Dependencies: mcp[cli], httpx
│   └── jama-server/             # Placeholder for future Jama MCP server
│       └── server.py            # Empty skeleton
├── .mcp.example.json            # Template for MCP server config (committed)
├── config/
│   ├── sources.json             # Active provider config (no secrets)
│   └── sources.example.json     # Template for new setups
└── docs/
    └── architecture.md          # This file
```

## Data Flow

```
User runs /bc:contradiction [scope]
  │
  ├─ Command (commands/contradiction.md)
  │    └─ Validates config exists
  │
  ├─ Agent (agents/contradiction-agent.agent.md)
  │    └─ Orchestrates the full pipeline:
  │
  │    Step 1: Load .buddy-council/sources.json
  │    Step 2: Determine scope (requirement ID, feature, or "all")
  │
  │    Step 3: Fetch requirements
  │    │  └─ skills/fetch-requirements/ → reads config → delegates to provider
  │    │     ├─ providers/excel/fetch.md (current)
  │    │     └─ providers/jama/fetch.md (future)
  │    │
  │    Step 4: Fetch test cases
  │    │  └─ skills/fetch-test-cases/ → reads config → delegates to provider
  │    │     └─ providers/testrail/fetch.md → MCP tools → TestRail API
  │    │
  │    Step 5: Normalize & cross-link
  │    │  └─ skills/normalize-artifacts/
  │    │     ├─ Clean HTML, whitespace, nan values
  │    │     ├─ Normalize IDs (CWA-REQ-XX, TC-XXXX)
  │    │     └─ Bidirectional linking via linked_ids
  │    │
  │    Step 6: Detect contradictions
  │    │  └─ skills/detect-contradictions/
  │    │     └─ 7 contradiction types, 4 severity levels
  │    │
  │    Step 7: Human-readable report
  │
  └─ Output: Markdown report grouped by severity
```

## Canonical Schema

Every artifact is normalized to this shape before analysis:

```json
{
  "type": "requirement | test_case",
  "id": "CWA-REQ-85",
  "title": "...",
  "description": "...",
  "feature": "Feature Name",
  "status": "Active",
  "linked_ids": ["TC-1234"],
  "raw_fields": {}
}
```

## Provider Abstraction

Agents never call providers directly. The flow is:

```
Agent → Router Skill → Provider Skill
```

- **Router skills** (`fetch-requirements`, `fetch-test-cases`) read `.buddy-council/sources.json` and delegate to the correct provider
- **Provider skills** (`providers/<name>/fetch.md`) handle platform-specific API calls and field mapping
- Adding a new platform requires only: new `providers/<name>/` folder + update router skill + update `/bc:setup`

### Current Providers

| Provider | Type | Status |
|----------|------|--------|
| Excel | Requirements | Active — Jama export fallback |
| TestRail | Test Cases | Active — REST API with pagination |
| Jama | Requirements | Placeholder — auth blocked |

### Future Providers

| Provider | Type | Notes |
|----------|------|-------|
| Jira | Requirements | When needed |
| Qase | Test Cases | When needed |

## Credential Management

Secrets live in exactly one place; configuration is separate:

- `.buddy-council/sources.json` — user-specific (gitignored), provider selection and non-secret settings (base URLs, project IDs)
- `~/.buddy-council/secrets.json` — user-local, `chmod 600`, holding API keys/tokens for the servers that read JSON (TestRail, GitHub). The MCP servers read it directly (path overridable via `BC_SECRETS_FILE`, default `~/.buddy-council/secrets.json`)
- `~/.buddy-council/atlassian.env` — user-local, `chmod 600`, holding the Jira/Confluence credentials plus `TOOLSETS`/`ENABLED_TOOLS`. It exists as a separate file because the Dockerized Atlassian server is configured through environment variables (`--env-file`) and cannot read JSON. Values must be unquoted: Docker's parser is literal, so quotes become part of the token
- `.mcp.json` — gitignored launch config holding **no credentials**: only non-secret env (`*_BASE_URL`), `BC_SECRETS_FILE`, and the Atlassian `--env-file` *path*. Env vars still take precedence if set, so legacy files with literal credentials keep working. Exception: the external GitHub MCP server reads `GITHUB_TOKEN` from env, so under the `mcp` enrichment strategy its token stays here
- `.mcp.example.json` — committed template
- `/bc:setup` writes the config and secrets files and generates `.mcp.json`

## Contradiction Detection

The agent detects 7 types of issues:

| Type | Severity | Description |
|------|----------|-------------|
| Direct conflicts | CRITICAL | Requirement says X, test validates opposite |
| Behavioral conflicts | HIGH | Different expected behaviors for same trigger |
| Test vs requirement | HIGH | Test steps contradict linked requirement |
| Scope overlaps | MEDIUM | Overlapping requirements with incompatible constraints |
| Cross-feature tensions | MEDIUM | Requirements in different features that conflict |
| Temporal/state conflicts | MEDIUM | Conflicting state or timing expectations |
| Missing alignment | LOW | Untested requirements or orphan test cases |

## Available Agents

| Agent | Command | Purpose |
|-------|---------|---------|
| Contradiction | `/bc:contradiction` | Detect conflicts between artifacts |
| Coverage | `/bc:coverage` | Find untested requirements and coverage gaps |
| QA (via orchestration) | `/bc:ask` | Answer general questions about artifacts |

## Orchestration (`/bc:ask`)

The `/bc:ask` command classifies intent and routes to the right agent:

```
/bc:ask "Why does TC-1234 contradict REQ-85?"
  └─ ask.md → CONTRADICTION intent → follows contradiction-agent.agent.md

/bc:ask "What requirements have no tests?"
  └─ ask.md → COVERAGE intent → follows coverage-agent.agent.md

/bc:ask "What does CWA-REQ-85 do?"
  └─ ask.md → QA intent → fetches data, answers directly
```

Intent classification uses Claude's native reasoning with trigger phrases and examples — no regex or external classifier. When intent is ambiguous, the command asks a clarifying question.

## Coverage Detection

The coverage agent detects 3 types of gaps:

| Type | Severity | Description |
|------|----------|-------------|
| Untested requirement (critical) | HIGH | Safety-critical requirement with no test cases |
| Untested requirement (other) | MEDIUM | Non-critical requirement with no test cases |
| Orphan test case | MEDIUM | Test case with no linked requirement |
| Weak coverage | LOW | Test exists but doesn't fully exercise the requirement |

### MCP Servers

The `mcp-servers/` directory contains standalone MCP servers that wrap external APIs:

```
Agent → Router Skill → Provider Skill → MCP Tool → External API
```

- **MCP servers** are the transport layer — thin API wrappers returning raw JSON
- **Provider skills** are the mapping layer — field extraction, canonical schema conversion
- **Normalization skill** handles cross-cutting concerns — cleaning, linking, deduplication

| Server | Status | Tools |
|--------|--------|-------|
| `testrail-server` | Active | `testrail_get_projects`, `testrail_get_suites`, `testrail_get_sections`, `testrail_get_cases`, `testrail_get_case` |
| `jama-server` | Placeholder | None yet (auth blocked) |

Jira/Confluence are **not** vendored here. They use the Dockerized community server
[`sooperset/mcp-atlassian`](https://github.com/sooperset/mcp-atlassian)
(`ghcr.io/sooperset/mcp-atlassian:latest`), which authenticates with an Atlassian API token and runs as a
stdio container. `/bc:setup` writes the entry into both `.mcp.json` (Claude Code) and
`~/.copilot/mcp-config.json` (Copilot CLI) — it is deliberately **not** declared in a plugin manifest,
because the `--env-file` path is absolute and per-machine.

The tools the plugin uses, by toolset:

| Toolset | In `default`? | Tools used |
|---|---|---|
| `jira_issues` | yes | `jira_get_issue`, `jira_search`, `jira_create_issue`, `jira_update_issue` |
| `jira_comments` | yes | `jira_add_comment` |
| `jira_transitions` | yes | `jira_get_transitions`, `jira_transition_issue` |
| `jira_fields` | yes | `jira_search_fields` |
| `jira_agile` | **no** | `jira_get_agile_boards`, `jira_get_board_issues`, `jira_get_sprints_from_board`, `jira_get_sprint_issues`, `jira_add_issues_to_sprint` |
| `jira_links` | **no** | `jira_get_link_types`, `jira_create_issue_link` |
| `jira_projects` | **no** | `jira_get_all_projects`, `jira_get_project_issue_types`, `jira_get_create_fields`, `jira_get_project_fields` |
| `jira_users` | **no** | `jira_get_user_profile`, `jira_search_assignable_users` |
| `confluence_pages` / `confluence_comments` | yes | reads only — `confluence_search`, `confluence_get_page`, `confluence_get_page_children`, `confluence_get_comments` |

The four non-default toolsets must be named explicitly in `TOOLSETS`, and an omitted one **fails silently**
— the tools are simply absent. `ENABLED_TOOLS` intersects with `TOOLSETS` and pins the surface to exactly
the tools above, which is what keeps Confluence's destructive page tools out of reach even though
`confluence_pages` is a core toolset.

Credentials for this server live in `~/.buddy-council/atlassian.env` (`chmod 600`), passed with
`--env-file`, so no secret appears in either MCP config.

The vendored MCP servers are configured in `.mcp.json` (gitignored) and read credentials from
`~/.buddy-council/secrets.json` (via `BC_SECRETS_FILE`), not the `env` block — see Credential Management
above. See `.mcp.example.json` for the template.
