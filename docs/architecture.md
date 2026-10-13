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
│   ├── coverage-agent.agent.md        # Orchestrates the coverage workflow
│   ├── data-collector.agent.md        # Low-cost sub-agent: collects a run into .buddy-council/runs/
│   └── scenario-mapping-agent.agent.md # User scenarios → requirement traces → TestRail cases
├── skills/                      # Reusable capabilities (SKILL.md with frontmatter)
│   ├── fetch-requirements/      # Router: delegates to configured provider
│   ├── fetch-test-cases/        # Router: delegates to configured provider
│   ├── collect-artifacts/       # Run folders: launches data-collector; bc_run.py new/enrich/link (stdlib, Python 3.9+)
│   ├── normalize-artifacts/     # Clean, normalize IDs, cross-link
│   ├── detect-contradictions/   # Core analysis (7 contradiction types)
│   ├── analyze-coverage/        # Coverage gap detection and metrics
│   ├── check-platform-parity/   # V&V phase 2: iOS ↔ Android counterparts
│   ├── clone-to-vnv-board/      # V&V phase 3: sprint stories → V&V board
│   ├── draft-vnv-scenarios/     # V&V phase 5: high-level scenarios → ticket
│   ├── draft-test-cases/        # The one TestRail writer: V&V scenarios, coverage gaps, user scenarios
│   └── update-test-cases/       # The one TestRail updater: edits, orphan re-links, contradiction fixes, V&V refresh
├── providers/                   # Platform-specific data fetching instructions
│   ├── excel/fetch.md           # Jama Excel export parser
│   ├── testrail/fetch.md        # TestRail via MCP tools
│   └── jama/fetch.md            # Jama live via the read-only jama MCP server (features = folders)
├── mcp-servers/                 # Standalone MCP servers wrapping external APIs
│   ├── testrail-server/         # Python MCP server for TestRail REST API
│   │   ├── server.py            # 13 read tools (incl. a cached requirement-link index and canonical cases) + 5 write tools (add_case/add_cases/add_section/update_case/update_cases)
│   │   ├── pyproject.toml       # Dependencies: mcp[cli], httpx
│   │   └── tests/               # stdlib unittest suite against a fake TestRail API
│   └── jama-server/             # Python MCP server for the Jama REST API — read-only by construction
│       ├── server.py            # 8 jama_get_* tools, GET-only client, requirements roots + folder-based features
│       ├── pyproject.toml       # Dependencies: mcp[cli], httpx
│       └── tests/               # stdlib unittest suite against a fake Jama REST API
├── .mcp.example.json            # Template for MCP server config (committed)
├── config/
│   ├── sources.json             # Active provider config (no secrets)
│   └── sources.example.json     # Template for new setups
└── docs/
    └── architecture.md          # This file
```

## Data Flow

`/bc:contradiction` and `/bc:coverage` split each run by what the work needs: a low-cost sub-agent fetches,
code links and counts, and the session's model only judges. The data travels as files in a run folder.

```
User runs /bc:contradiction [scope]
  │
  ├─ Command (commands/contradiction.md) → Agent (agents/contradiction-agent.agent.md), on the session's model
  │
  │    Step 1–2: Load .buddy-council/sources.json, determine the scope
  │
  │    Step 3: Collect — skills/collect-artifacts/
  │    │  └─ bc:data-collector sub-agent, on a low-cost model (Haiku; Copilot: claude-haiku-4.5, else gpt-5-mini)
  │    │     ├─ bc_run.py new        → .buddy-council/runs/<run-id>/
  │    │     ├─ jama_get_requirements(save_to)        → requirements.json   (or the Excel parser)
  │    │     ├─ testrail_get_canonical_cases(save_to) → test_cases.json     (every case in the suite)
  │    │     ├─ bc_run.py enrich     → docs/ + docs.json (gh CLI strategy)
  │    │     └─ bc_run.py link       → links.json + bundles/NN-<feature>-<p>.md   (code: no model)
  │    │        returns counts, Fetch/Enrichment/Readiness lines, failures — never the data
  │    │
  │    Step 3a: Board issues — skills/fetch-board-issues/ (session's model)
  │    Step 4: Readiness gate from links.json counts
  │    Step 5: One feature at a time — read its bundle parts, detect contradictions, board pass
  │    Step 6: Cross-feature pass from a compact index; re-read two bundles to confirm
  │    Step 7: Human-readable report
  │
  └─ Output: Markdown report grouped by severity
```

The other commands (`/bc:ask`, `/bc:validate`, `/bc:onboarding`, `/bc:codemap`, `/bc:vnv-sprint-prep`) still
fetch inline through the router skills:

```
skills/fetch-requirements/ → providers/excel/fetch.md (bundled parser) | providers/jama/fetch.md (jama MCP, GET only)
skills/fetch-test-cases/   → providers/testrail/fetch.md → testrail_get_canonical_cases (canonical, slim)
skills/normalize-artifacts/ → clean text, normalize IDs, cross-link
```

### Run folders

| File | Written by | Holds |
|---|---|---|
| `requirements.json` | Jama server (`save_to`) or Excel parser | canonical requirements in scope |
| `test_cases.json` | TestRail server (`save_to`) | every case in the suite, canonical — the link step scopes them |
| `docs/`, `docs.json` | `bc_run.py enrich` | the GitHub docs requirements link to, verbatim, truncated at `max_doc_chars`, split into parts of at most 18 KB |
| `links.json` | `bc_run.py link` | counts, untested (Deprecated/Deferred left out), orphans, stranded links, unknown linked IDs, per-feature rows with their bundles and docs; the `Fetch:`/`Enrichment:`/`Readiness:` lines; missing sources; and every mismatch between the run and `sources.json` |
| `bundles/*.md` | `bc_run.py link` | per feature: each requirement verbatim with its custom fields, then the cases that link to it with theirs; parts of at most 18 KB |
| `search/NN-<ask>-<p>.md` | `bc_run.py search` | a lookup's results: where the matches are, the top 25 requirements verbatim with what they matched, the test cases and docs that mention the ask, the next 25 candidates, and the project's own wording for a second search |
| `manifest.json` | `bc_run.py` | command, scope, timestamps, counts |

A run folder is created by `bc_run.py new`, never by a server: `save_to` only writes a `.json` file directly
inside an existing `.buddy-council/runs/<run>/`, refuses a symbolic link, and creates no directory. The newest
5 runs are kept, none longer than a week. Files are `chmod 600`.

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
| Excel | Requirements | Active — Jama export file |
| TestRail | Test Cases | Active — REST API with pagination |
| Jama | Requirements | Active — live REST API, read-only, features = folders |

### Future Providers

| Provider | Type | Notes |
|----------|------|-------|
| Jira | Requirements | When needed |
| Qase | Test Cases | When needed |

## Credential Management

Secrets live in exactly one place; configuration is separate:

- `.buddy-council/sources.json` — user-specific (gitignored), provider selection and non-secret settings (base URLs, project IDs)
- `~/.buddy-council/secrets.json` — user-local, `chmod 600`, the **single source of truth** for API keys/tokens. The MCP servers read it directly (path overridable via `BC_SECRETS_FILE`, default `~/.buddy-council/secrets.json`)
- `.mcp.json` — gitignored launch config holding **no credentials**: only non-secret env (`*_BASE_URL`) plus `BC_SECRETS_FILE`. Env vars still take precedence if set, so legacy files with literal credentials keep working. Exception: the external GitHub MCP server reads `GITHUB_TOKEN` from env, so under the `mcp` enrichment strategy its token stays here
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
| Data collector | `/bc:coverage`, `/bc:contradiction` | Low-cost sub-agent: fetch into a run folder, link in code, return counts |
| QA (via orchestration) | `/bc:ask` | Answer general questions about artifacts |
| Scenario mapping | `/bc:ask` | Trace the user's own scenarios to requirements, check them against existing cases, write them into TestRail |

## Orchestration (`/bc:ask`)

The `/bc:ask` command classifies intent and routes to the right agent:

```
/bc:ask "Why does TC-1234 contradict REQ-85?"
  └─ ask.md → CONTRADICTION intent → follows contradiction-agent.agent.md

/bc:ask "What requirements have no tests?"
  └─ ask.md → COVERAGE intent → follows coverage-agent.agent.md

/bc:ask "What does CWA-REQ-85 do?"
  └─ ask.md → QA intent → fetches data, answers directly

/bc:ask "Do we already have a requirement that silences alerts during warm-up?"
  └─ ask.md → LOOKUP intent → lookup-requirements → a recent or new run → bc_run.py search (code) → judged by the model

/bc:ask "Map the scenarios in sync.feature into TestRail"
  └─ ask.md → AUTHOR intent → follows scenario-mapping-agent.agent.md → draft-test-cases

/bc:ask "Change step 2 of C1234 to expect a 3-second refresh"
  └─ ask.md → EDIT intent → update-test-cases (edits path)
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
| `testrail-server` | Active | 13 `testrail_get_*` reads (projects, suites, sections, cases, cases by refs, case, case fields, case types, priorities, templates, requirement IDs, linked cases, canonical cases); writes `testrail_add_case`, `testrail_add_cases`, `testrail_add_section`, `testrail_update_case`, `testrail_update_cases` — always prompted |
| `jama-server` | Active, read-only | `jama_get_current_user`, `jama_get_projects`, `jama_get_item_types`, `jama_get_features`, `jama_get_requirements`, `jama_get_item`, `jama_get_items`, `jama_get_scope_proposal` |

The Jama server is the one MCP server that emits the canonical schema itself, like the Excel parser: feature
scoping needs the whole project tree (rebuilt from each item's `location.parent`), so the server builds the tree,
resolves pick lists, flattens rich text, and returns finished requirements. It has no write tools, and its HTTP
client can only issue GET. Because Jama answers each request in about a second however small it is, the server
runs up to 8 requests at once, reads a large project tree in the background (a call that would outlast the
client answers `loading: true`, and repeating it joins the same read), and reads lists of known keys with the
batched `jama_get_items` — which never touches the project tree — rather than one call per key.

Jira/Confluence are **not** vendored here. They use [`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian),
run as a local stdio server via `uvx mcp-atlassian@0.23.1` and authenticated by a token in
`~/.buddy-council/atlassian.env`. It serves **both Atlassian Cloud and Jira Server/Data Center**, and exposes
`jira_get_issue`, `jira_search`, `jira_get_agile_boards`, `jira_get_board_issues`,
`jira_get_sprints_from_board`, `jira_get_link_types`, and the write tools `jira_create_issue`,
`jira_update_issue`, `jira_add_comment`, `jira_create_issue_link`, and `jira_add_issues_to_sprint`.
`/bc:setup` writes it into `.mcp.json` for Claude Code and `~/.copilot/mcp-config.json` for Copilot CLI —
no plugin manifest declares it, because a `uvx` server needs absolute paths a committed manifest cannot hold.

It replaced Atlassian's hosted `mcp.atlassian.com` server in 0.21.1. That server is Cloud-only, so a Jira
Server/Data Center team could never reach it, and it exposed no board or sprint tools — which is why board
reads used to be approximated by a project-wide JQL. Boards are now addressed by id, so two boards in one
project are fully distinguishable.

The vendored MCP servers are configured in `.mcp.json` (gitignored) and read credentials from
`~/.buddy-council/secrets.json` (via `BC_SECRETS_FILE`), not the `env` block — see Credential Management
above. See `.mcp.example.json` for the template.
