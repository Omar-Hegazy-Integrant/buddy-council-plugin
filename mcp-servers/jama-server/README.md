# Jama MCP Server

Read-only MCP server wrapping the Jama Connect REST API (`/rest/v1`). It is how the `jama` requirements
provider reads requirements live, scoped by feature — and Jama features are **folders**.

## Read-only by construction

- Every tool is named `jama_get_*`, and every tool is annotated `readOnlyHint`.
- The HTTP client exposes only GET for `/rest/v1/`. The single other request it can make is the OAuth token
  exchange (`POST /rest/oauth/token`), which reads and writes nothing in Jama.
- `tests/test_server.py` fails if a write-capable HTTP call appears in the source, if any request other than
  GET reaches the API, or if a tool is added outside the `jama_get_*` prefix.

The plugin's auto-approve hook trusts the `jama_get_*` glob on that basis. **Never add a write tool.** If one
is ever needed, it must not be named `jama_get_*`, and it must be excluded from the hook, from `settings.json`,
and from every `--allow-tool` recipe, like the TestRail and Jira writes.

For defense in depth, create the API credentials on a Jama account with read-only permissions on the project.

## Configuration

| Setting | Source | Description |
|---------|--------|-------------|
| `JAMA_BASE_URL` | MCP config env (non-secret) | Jama root URL, e.g. `https://company.jamacloud.com`. Must be `https://` (plain HTTP only on localhost). A pasted `/rest/v1` or `/perspective.req…` suffix is stripped. |
| `client_id`, `client_secret` | `~/.buddy-council/secrets.json` → `jama` | OAuth client credentials — Jama → profile menu → **Set API Credentials**. Preferred. |
| `username`, `password` | `~/.buddy-council/secrets.json` → `jama` | Basic auth, only for instances without API credentials. |

Credentials are read from the secrets file at `BC_SECRETS_FILE` (default `~/.buddy-council/secrets.json`) and
**only** from there — unlike the TestRail server, no environment variable can carry them, so they never end up
in `.mcp.json`. The server starts without configuration and explains what is missing on the first tool call.

Behind a TLS-inspecting corporate proxy, set `SSL_CERT_FILE` in the server's env to the company CA bundle.

## Tools

| Tool | Description |
|------|-------------|
| `jama_get_current_user` | Connection check: the account in use and the auth method |
| `jama_get_projects` | Readable projects: id, key, name |
| `jama_get_item_types` | Item types and their fields; with `project_id`, only the types in that project, with counts |
| `jama_get_features` | The project's folders (features) with paths and requirement counts |
| `jama_get_requirements` | Requirements in the plugin's canonical schema, optionally scoped to a feature |
| `jama_get_item` | One item by document key or id, with upstream/downstream relationships |

### Features and scoping

A requirement's `feature` is its nearest **Folder** ancestor (`feature_item_type` picks another type), falling
back to the nearest Set or Component, then `"Unknown"`. `jama_get_requirements` takes a `scope`:

- a feature name (`Patient Monitoring`) → everything under that folder, sub-folders included;
- a feature path (`Software Requirements/Patient Monitoring`) → the same, for folders that share a name;
- a document key (`CWA-REQ-85`) → that requirement plus the rest of its feature;
- empty or `all` → the whole project.

The project tree is read once with paginated GETs (50 items per request, Jama's maximum) and cached for 10
minutes, so several scoped fetches cost one read; `refresh=true` bypasses the cache. A project larger than
20,000 items is returned as `truncated` with a warning. Rate limiting (HTTP 429) is retried with `Retry-After`;
an expired token is renewed once.

`jama_get_requirements` responses are capped at roughly 60,000 characters so they fit MCP clients' output
limits (Claude Code's default is 25k tokens). A larger result carries `next_offset`; calling again with
`offset=next_offset` returns the next page from the cache, and `total` gives the full count.

## Running

```bash
cd mcp-servers/jama-server
uv run mcp run server.py          # serve MCP over stdio
uv run mcp dev server.py          # MCP Inspector
```

`/bc:setup` checks a connection before the server is registered, with a one-shot report that prints JSON and
exits:

```bash
JAMA_BASE_URL=https://company.jamacloud.com uv run python server.py --check               # account + projects
JAMA_BASE_URL=https://company.jamacloud.com uv run python server.py --check --project 42  # + item types, folders, field candidates
```

## Tests

Standard library only — they run against an in-process fake of the Jama REST API, so no Jama instance is
needed:

```bash
uv run --directory mcp-servers/jama-server python -m unittest discover -s tests -v
```

After changing `pyproject.toml`, re-run `uv lock --directory mcp-servers/jama-server` and commit the lock.
