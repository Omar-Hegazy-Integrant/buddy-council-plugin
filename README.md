# Buddy-Council

A multi-agent plugin for detecting contradictions, coverage gaps, and alignment issues between **requirements** and **test cases**. Works with both **Claude Code** and **Copilot CLI**.

Data is fetched live from external systems via MCP — no RAG, no embeddings, no vector storage.

## Supported Sources

| Source | Type | Status |
|--------|------|--------|
| **TestRail** | Test cases | Supported (via MCP server) |
| **Excel** (Jama export) | Requirements | Supported (temporary Jama fallback) |
| **Jama** | Requirements | Planned (auth in progress) |
| **Jira** | Requirements | Planned |
| **Qase** | Test cases | Planned |

## Available Commands

| Command | Description |
|---------|-------------|
| `/bc:setup` | Configure data sources and credentials |
| `/bc:contradiction` | Detect contradictions between requirements and test cases |
| `/bc:coverage` | Find untested requirements, orphan tests, and coverage gaps |
| `/bc:ask` | Natural language query — routes to the right agent |

## Prerequisites

- [Claude Code](https://claude.ai/code) **or** [Copilot CLI](https://docs.github.com/en/copilot)
- [uv](https://docs.astral.sh/uv/) (Python package manager, for the TestRail MCP server)
- Python 3.10+
- A TestRail account with API access (for test cases)
- An Excel export from Jama (for requirements), or direct Jama API access (when available)

## Installation

### Claude Code

**Step 1: Add the marketplace**

```
/plugin marketplace add https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin
```

**Step 2: Install the plugin**

```
/plugin install bc
```

**Step 3: Reload**

```
/reload-plugins
```

### Copilot CLI

**Step 1: Add the marketplace**

```
copilot plugin marketplace add https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin
```

**Step 2: Install the plugin**

```
copilot plugin install bc
```

### Local Development (either platform)

```bash
# Clone the repository
git clone https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin.git

# Install the TestRail MCP server dependencies
cd buddy-council-plugin/mcp-servers/testrail-server
uv venv && uv pip install -e .

# Run with Claude Code
claude --plugin-dir /path/to/buddy-council-plugin

# Or with Copilot CLI
copilot plugin install /path/to/buddy-council-plugin
```

## Setup

After installation, run the setup command to configure your data sources:

```
/bc:setup
```

This walks you through:

1. **Requirements source** — choose Excel (Jama export) or Jama (when available)
2. **Test cases source** — configure TestRail connection
3. **Credentials** — stored securely in `~/.buddy-council-secrets.json` (never committed)
4. **MCP server** — writes `.mcp.json` with TestRail credentials for the MCP server

After setup, restart your CLI tool or toggle the MCP server for it to take effect.

### Manual Configuration

If you prefer to configure manually instead of using `/bc:setup`:

**1. Create `config/sources.json`** (no secrets in this file):

```json
{
  "requirements": {
    "provider": "excel",
    "excel_path": "/absolute/path/to/requirements.xls"
  },
  "test_cases": {
    "provider": "testrail",
    "base_url": "https://your-instance.testrail.io",
    "project_id": 1,
    "suite_id": null
  }
}
```

**2. Create `~/.buddy-council-secrets.json`**:

```json
{
  "testrail": {
    "username": "user@company.com",
    "api_key": "your-testrail-api-key"
  }
}
```

```bash
chmod 600 ~/.buddy-council-secrets.json
```

**3. Create `.mcp.json`** in the plugin root (copy from `.mcp.example.json` and fill in credentials):

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uv",
      "args": ["run", "--directory", "mcp-servers/testrail-server", "mcp", "run", "server.py"],
      "env": {
        "TESTRAIL_BASE_URL": "https://your-instance.testrail.io",
        "TESTRAIL_USERNAME": "user@company.com",
        "TESTRAIL_API_KEY": "your-testrail-api-key"
      }
    }
  }
}
```

## Usage

### Detect Contradictions

```
/bc:contradiction                        # Analyze all requirements and test cases
/bc:contradiction CWA-REQ-85             # Analyze a specific requirement
/bc:contradiction "Patient Monitoring"   # Analyze a specific feature
```

The agent fetches requirements and test cases, normalizes and cross-links them, then analyzes for 7 types of contradictions:

| Type | Severity |
|------|----------|
| Direct conflicts | CRITICAL |
| Behavioral conflicts | HIGH |
| Test vs requirement conflicts | HIGH |
| Scope overlaps | MEDIUM |
| Cross-feature tensions | MEDIUM |
| Temporal/state conflicts | MEDIUM |
| Missing alignment | LOW |

### Check Coverage

```
/bc:coverage                             # Full coverage analysis
/bc:coverage "Login"                     # Coverage for a specific feature
```

### Ask Questions

```
/bc:ask "Why does TC-1234 contradict REQ-85?"
/bc:ask "What requirements have no tests?"
/bc:ask "What does CWA-REQ-85 do?"
```

Routes automatically to the right agent based on intent.

## Architecture

```
Command → Agent → Skills (fetch → normalize → analyze) → Report
```

- **Commands** — user-facing entry points
- **Agents** — orchestrate the analysis workflow end-to-end
- **Skills** — reusable capabilities (fetching, normalization, analysis)
- **Providers** — platform-specific data fetching (TestRail, Excel, Jama)
- **MCP Servers** — wrap external APIs with structured tool interfaces

Agents never call providers directly — they go through router skills, which read the config and delegate to the correct provider. This means adding a new platform (e.g., Jira, Qase) only requires adding a `providers/<name>/` folder and updating the router.

See [docs/architecture.md](docs/architecture.md) for the full architecture documentation.

## Security

- Credentials are **never** committed to git
- `config/sources.json` contains only provider names and non-secret settings
- Secrets live in `~/.buddy-council-secrets.json` (user home directory)
- `.mcp.json` is gitignored (contains credentials in env block)
- All MCP tools are **read-only** — no write operations to external systems
- A PreToolUse hook blocks destructive Bash commands (`rm -rf`, `kill`, `git push --force`, etc.)

## Adding a New Provider

1. Create `providers/<name>/fetch.md` with fetch instructions
2. Update the router skill (`skills/fetch-requirements/SKILL.md` or `skills/fetch-test-cases/SKILL.md`)
3. Update `/bc:setup` to offer the new provider as an option
4. Optionally add an MCP server in `mcp-servers/<name>/`

No changes to agents or analysis skills required.

## License

MIT
