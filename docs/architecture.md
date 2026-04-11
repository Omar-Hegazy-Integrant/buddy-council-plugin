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
│   ├── contradiction.md         # /bc:contradiction
│   ├── setup.md                 # /bc:setup
│   └── ask.md                   # /bc:ask (future orchestration)
├── agents/                      # Reasoning engines
│   └── contradiction-agent.md   # Orchestrates the contradiction workflow
├── skills/                      # Reusable capabilities (SKILL.md with frontmatter)
│   ├── fetch-requirements/      # Router: delegates to configured provider
│   ├── fetch-test-cases/        # Router: delegates to configured provider
│   ├── normalize-artifacts/     # Clean, normalize IDs, cross-link
│   └── detect-contradictions/   # Core analysis (7 contradiction types)
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
  ├─ Agent (agents/contradiction-agent.md)
  │    └─ Orchestrates the full pipeline:
  │
  │    Step 1: Load config/sources.json
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

- **Router skills** (`fetch-requirements`, `fetch-test-cases`) read `config/sources.json` and delegate to the correct provider
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

Secrets are separated from configuration:

- `config/sources.json` — user-specific (gitignored), contains provider selection and non-secret settings (base URLs, project IDs)
- `~/.buddy-council-secrets.json` — user-local, `chmod 600`, contains API keys and usernames
- `.mcp.json` — gitignored, contains MCP server config with credentials in env block
- `.mcp.example.json` — committed template with empty credential placeholders
- `/bc:setup` writes all three files and validates the connection

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

## Future Architecture

### Multi-Agent Orchestration (Phase 2)

```
/bc:ask "Why does TC-1234 contradict REQ-85?"
  └─ ask.md → intent classification → routes to contradiction-agent

/bc:ask "What's the test coverage for login?"
  └─ ask.md → intent classification → routes to coverage-agent (future)
```

### Planned Agents

| Agent | Command | Purpose |
|-------|---------|---------|
| Contradiction | `/bc:contradiction` | Detect conflicts between artifacts |
| Coverage | `/bc:coverage` | Find untested requirements |
| QA | `/bc:ask` | Answer general questions about artifacts |

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

MCP servers are configured in `.mcp.json` (gitignored) with credentials in the `env` block. See `.mcp.example.json` for the template.
