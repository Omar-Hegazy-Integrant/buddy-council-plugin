# Buddy-Council Plugin

A Claude Code plugin for multi-agent requirements/test-case analysis.

## What This Plugin Does

Buddy-Council helps teams detect contradictions, inconsistencies, and alignment gaps between **requirements** (from Jama or Excel) and **test cases** (from TestRail). It fetches data live — no RAG, no embeddings, no vector storage.

## Architecture

- **Commands** (`commands/`) — user-facing entry points (`/bc:contradiction`, `/bc:coverage`, `/bc:ask`, `/bc:setup`, `/bc:onboarding`, `/bc:validate`)
- **Agents** (`agents/`) — reasoning engines that orchestrate skills to complete tasks
- **Skills** (`skills/`) — reusable capabilities (fetch data, normalize, analyze)
- **Providers** (`providers/`) — platform-specific data fetching instructions (TestRail, Excel, Jama)
- **MCP Servers** (`mcp-servers/`) — standalone MCP servers wrapping external APIs (TestRail, Jama)
- **Config** (`config/`) — source selection and non-secret configuration

## Data Flow

```
Command → Agent → Skills (fetch → normalize → analyze) → Human-readable report
```

Requirements and test cases are fetched live from configured sources, normalized to a canonical schema, linked by ID references, then analyzed by Claude.

## Key Conventions

- All commands use the `bc:` prefix
- No hardcoded secrets — credentials live in `~/.buddy-council-secrets.json` and `.mcp.json` (both gitignored)
- Source configuration lives in `config/sources.json`
- Provider skills are swappable — adding a new platform means adding a `providers/<name>/` folder
- Agents never call providers directly — they go through router skills (`fetch-requirements`, `fetch-test-cases`)

## Available Commands

- `/bc:setup` — Configure data sources and credentials
- `/bc:contradiction` — Detect contradictions between requirements and test cases
- `/bc:coverage` — Find untested requirements, orphan test cases, and coverage gaps
- `/bc:ask` — Natural language query — routes to the right agent or answers directly
- `/bc:onboarding` — Walk a new team member through the product feature-by-feature with paced demos, do/don't pairs from test cases, and an assessment phase. Progress is logged to `.buddy-council/onboarding-progress.json` in the user's project root and resumes across sessions.
- `/bc:validate` — Validate Jira tickets against requirements and test cases (gap and contradiction detection)

## Canonical Artifact Schema

All providers normalize data to this shape before analysis:

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
