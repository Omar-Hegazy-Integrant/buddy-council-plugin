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
| `jama_get_features` | The project's features with paths and requirement counts |
| `jama_get_requirements` | Requirements in the plugin's canonical schema, optionally scoped to a feature |
| `jama_get_item` | One item by document key or id, with upstream/downstream relationships |
| `jama_get_items` | Many items in one call, by document key or id — each shaped like `jama_get_item`, in request order, paged |
| `jama_get_scope_proposal` | From the requirement IDs a test suite links to: the proposed root(s), item types, and feature preview |

### Roots and features

A large project holds far more than the requirements a team tests. **Roots** narrow it: pass `root_ids` (the
requirements roots from config) and only what sits inside them is in scope; `exclude_ids` leaves out
subtrees inside a root, such as an archive folder. With roots, a requirement's `feature` is the container
`feature_depth` levels below its root — the root's top-level folders by default — and deeper folders roll
up into it, with `raw_fields.folder_path` keeping the exact location. Two features that share a name are
labelled with their paths so they never merge. A configured root that no longer exists is reported loudly
rather than silently widened to the project.

Without roots, the whole project is in scope and a requirement's `feature` is its nearest **Folder** ancestor
(`feature_item_type` picks another type), falling back to the nearest Set or Component, then `"Unknown"`.

`jama_get_scope_proposal` chooses roots from evidence. Given the requirement IDs the test suite links to, it
walks down from the project while one container holds at least 95% of them; a Set or Folder reached that way
is the root. Above set level — the project, or a Component grouping several documents — it splits into the
few children that together hold 95%. Links outside the result are reported as outliers rather than widening
the root, and features with no linked IDs are listed, because only a person can tell an untested feature from
an archive.

`jama_get_requirements` takes a `scope`:

- a feature name (`Patient Monitoring`) → everything under that folder, sub-folders included;
- a feature path (`Software Requirements/Patient Monitoring`) → the same, for folders that share a name;
- a document key (`CWA-REQ-85`) → that requirement plus the rest of its feature;
- empty or `all` → everything in scope.

The project tree is read with paginated GETs (50 items per request, Jama's maximum) and cached for 10
minutes, so several scoped fetches cost one read; `refresh=true` bypasses the cache. A project larger than
20,000 items is returned as `truncated` with a warning. Rate limiting (HTTP 429) is retried with `Retry-After`,
a dropped connection is retried twice, and an expired token is renewed once.

`jama_get_requirements` responses are capped at roughly 60,000 characters so they fit MCP clients' output
limits (Claude Code's default is 25k tokens). A larger result carries `next_offset`; calling again with
`offset=next_offset` returns the next page from the cache, and `total` gives the full count.

### Performance

Jama Cloud answers each request in about 1–1.5 s however small it is, and a page of 50 requirements is
hundreds of kilobytes, so the server is built around request count and latency:

- **Requests run in parallel**, at most 8 in flight across every tool call. After a listing's first page
  reports the total, the remaining pages are requested together.
- **A project read runs in the background.** A 5,000-item project takes Jama about a minute to send — longer
  than a client waits for one tool call. `jama_get_requirements`, `jama_get_features`, `jama_get_item_types`
  (with a project), and `jama_get_scope_proposal` wait up to 40 s, then answer
  `{loading: true, pages_read, pages_total, …}` while the read carries on. Calling again with the same
  arguments joins the same read rather than starting another, even with `refresh=true`. A read that fails
  while no call is waiting is reported by the next call, and the call after that retries.
- **`jama_get_items` reads a list of keys without the project tree.** Keys are searched ten to a request, the
  project's folders, sets, and components come from one search (cached, and shared by every item in it), and
  each item's relationships are read once. Items, key lookups, relationships, and containers are cached for
  10 minutes, so a repeat call makes no requests. Each call stops starting new batches of 16 items before it
  would pass ~25 s, and returns `next_offset` for the rest. `include_relationships=false` skips the two
  traceability requests per item, which are most of the cost.
- Item types (the field definitions, several large pages) are cached for an hour.
- **`save_to` keeps large results out of the conversation.** `jama_get_requirements` with `save_to` writes
  every requirement in scope to a JSON file in a run folder and answers with the counts and the path. The
  path must be a `.json` file directly inside an existing `<project>/.buddy-council/runs/<run>/` folder, never
  a symbolic link; the server creates no directory. This is its only local side effect — nothing is ever
  written to Jama.
- **Each result is sent once, as text.** Every tool sets `structured_output=False`; otherwise FastMCP adds a
  structured copy of the same JSON, Copilot CLI hands the model both, and every response doubles. The test
  suite fails if a tool is added without it.

Measured on a Jama Cloud project of about 5,000 items: a full read in about 50 s (one `loading` answer, then the
result), and 49 requirements with traceability in about 45 s over three calls — instead of the full read
timing out and roughly 15–20 s per item one at a time. Repeat calls are served from the cache.

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
JAMA_BASE_URL=https://company.jamacloud.com uv run python server.py --check --project 42 \
  --anchors-file linked-ids.txt                                                           # + scope_proposal from test-linked IDs
```

`--root`, `--exclude`, and `--feature-depth` preview an adjusted root with the same proposal output.

## Tests

Standard library only — they run against an in-process fake of the Jama REST API, so no Jama instance is
needed:

```bash
uv run --directory mcp-servers/jama-server python -m unittest discover -s tests -v
```

After changing `pyproject.toml`, re-run `uv lock --directory mcp-servers/jama-server` and commit the lock.
