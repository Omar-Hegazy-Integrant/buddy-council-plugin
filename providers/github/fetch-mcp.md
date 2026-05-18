# GitHub Fetch (MCP Strategy) — Provider Skill

Fetch a single GitHub-hosted file via the official `github-mcp-server` MCP server. Used when `requirements.enrichment.strategy === "mcp"`.

## When to Use

Invoked by `skills/enrich-requirements/SKILL.md` for each unique URL when the user configured MCP at setup time. The user must have:

- Installed `github-mcp-server` externally (not vendored by this plugin — `.mcp.example.json` shows where to add it)
- Provided a GitHub PAT during `/bc:setup`, stored in `.mcp.json` env block under `GITHUB_TOKEN`
- Restarted Claude Code (or toggled the MCP server) so `mcp__github__*` tools are available

If the MCP tools are not available at runtime, return a structured error so the orchestrator can surface the failure to the user.

## Input

A single URL string matching `^https://github\.com/...`. The orchestrator has already stripped query params before calling this skill.

## URL Parsing

Same as `providers/github/fetch-cli.md` — see that file for the accepted shapes (`blob`, `tree`, `raw.githubusercontent.com`, with optional SHA and anchor).

## Fetch Procedure

### Step 1: Pre-probe repo access

Call the MCP tool for repo info:

```
mcp__github__get_file_contents
  owner: "<owner>"
  repo: "<repo>"
  path: ""           # empty path returns repo root metadata or directory listing
  ref: "<ref>"       # optional, defaults to default branch
```

Outcomes:
- success → repo accessible, proceed to Step 2
- 404 → `error: "repo_inaccessible"`
- 401/403 → `error: "auth_failed"`
- network/MCP unavailable → `error: "network"`

### Step 2: Fetch content

```
mcp__github__get_file_contents
  owner: "<owner>"
  repo: "<repo>"
  path: "<path>"
  ref: "<ref>"
```

The MCP tool returns either:
- An object with `{content, encoding: "base64", size, sha, ...}` for a single file
- An array of objects for a directory (each with `{name, path, type, ...}`)

Decode base64 content the same way as the CLI provider.

For directories (`tree` URLs), apply the same rule as the CLI provider:
1. Prefer `README.md` (case-insensitive).
2. Otherwise concatenate up to 5 `.md` files.
3. Otherwise return `error: "directory_no_markdown"`.

### Step 3: Rate-limit detection

If the MCP server surfaces a rate-limit error (HTTP 403 with reset time, or an explicit `rate_limit_remaining: 0` field), return `error: "rate_limit"` with `reset_at`.

## Output

Identical shape to `providers/github/fetch-cli.md`:

```json
{
  "source": "github",
  "url": "<original URL>",
  "locator": {
    "owner": "owner",
    "repo": "repo",
    "path": "docs/file.md",
    "ref": "main",
    "anchor": null
  },
  "content": "<decoded markdown verbatim>",
  "fetched_at": "<ISO 8601 UTC>",
  "raw_size": 4231,
  "http_status": 200
}
```

Error variant (same enum as CLI):

```json
{
  "source": "github",
  "url": "<original URL>",
  "error": "auth_failed | repo_inaccessible | file_not_found | rate_limit | directory_no_markdown | network | unknown",
  "message": "<human-readable detail>",
  "reset_at": "<ISO 8601, only on rate_limit>"
}
```

## Guidelines

- **Use MCP tools only.** Do NOT shell out to `gh` or `curl` from this provider — that's the CLI provider's job. Mixing strategies makes failure modes hard to reason about.
- **Detect MCP unavailability cleanly.** If `mcp__github__get_file_contents` isn't a callable tool at runtime, return `error: "network"` with message `"github-mcp-server not available — install it and restart Claude Code"`. The orchestrator will surface this to the user.
- **Preserve markdown verbatim** — same as CLI provider.
- **Preserve `ref` literally** — never substitute the default branch when a SHA is given.
- **Do not write to disk.** All content returned in memory.
