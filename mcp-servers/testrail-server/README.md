# TestRail MCP Server

Read-only MCP server wrapping the TestRail REST API.

## Configuration

The server resolves each value with this precedence: **environment variable → secrets file → empty**.

| Setting | Source | Description |
|---------|--------|-------------|
| `TESTRAIL_BASE_URL` | `.mcp.json` env (non-secret) | TestRail instance URL (e.g., `https://company.testrail.io`) |
| `username` | `~/.buddy-council/secrets.json` → `testrail.username` | TestRail username (email) |
| `api_key` | `~/.buddy-council/secrets.json` → `testrail.api_key` | TestRail API key (from My Settings > API Keys) |

Credentials are read from the secrets file at `BC_SECRETS_FILE` (default `~/.buddy-council/secrets.json`), so no secret needs to live in `.mcp.json`. The equivalent env vars (`TESTRAIL_USERNAME`, `TESTRAIL_API_KEY`) still take precedence if set, for the legacy inline style.

## Setup

```bash
cd mcp-servers/testrail-server
uv pip install -e .
```

Or with pip:
```bash
pip install -e .
```

## Running Standalone

```bash
# With uv
uv run mcp run server.py

# With python directly
python server.py
```

## Testing with MCP Inspector

```bash
mcp dev server.py
```

## Available Tools

| Tool | Description |
|------|-------------|
| `testrail_get_projects` | List all projects |
| `testrail_get_suites` | List suites for a project |
| `testrail_get_sections` | List sections (folders) for a project/suite |
| `testrail_get_cases` | Fetch test cases with pagination |
| `testrail_get_case` | Fetch a single test case by ID |

All tools are read-only. No write operations are exposed.

## Pagination

`testrail_get_cases` returns max 250 cases per call. Check if `size == limit` and increment `offset` to get the next page.
