# TestRail MCP Server

MCP server wrapping the TestRail REST API: read access to projects, suites, sections and cases, plus
guarded creation of test cases and the folders they live in.

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

### Read

| Tool | Description |
|------|-------------|
| `testrail_get_projects` | List all projects |
| `testrail_get_suites` | List suites for a project |
| `testrail_get_sections` | List sections (folders) for a project/suite, each with a derived `path` |
| `testrail_get_cases` | Fetch test cases with pagination |
| `testrail_get_cases_by_refs` | Fetch cases linked to given requirement IDs |
| `testrail_get_case` | Fetch a single test case by ID |
| `testrail_get_case_fields` | Every case field on the instance, including custom ones |
| `testrail_get_case_types` | Case types, for `type_id` |
| `testrail_get_priorities` | Priorities, for `priority_id` |
| `testrail_get_templates` | Case templates for a project, for `template_id` |

### Write

| Tool | Description |
|------|-------------|
| `testrail_add_cases` | Create one or more cases in suite folders |
| `testrail_add_case` | Convenience wrapper for a single case |
| `testrail_add_section` | Create one folder |

**The write tools always prompt.** They are excluded from the plugin's auto-approve hook and from
`settings.json` by design — the hook allowlist only globs `testrail_get_*`. If you ever add a write tool
whose name begins `testrail_get_`, that glob will auto-approve it silently; don't.

## Choosing the target folder

`testrail_add_cases` needs to know which section (folder) of the suite a case belongs in. Give it either:

- **`section_id`** — a numeric id. It is *validated* against the suite before anything is written, because a
  wrong id quietly files cases into another team's folder.
- **`section_path`** — a path like `"Login/Negative cases"`, matched case-insensitively against the section
  tree. With `create_missing_sections` (default true) any missing folders in the chain are created and
  listed back in `sections_created`.

Individual entries in `cases` may carry their own `section_id`/`section_path`, so one call can write into
several folders. Folder lookups and duplicate checks are cached per target, so a 40-case batch into one
folder costs one section read and one duplicate read, not forty.

## Duplicates, dry runs, and partial failures

- `skip_if_title_exists` (default true) reads the target folder's existing titles and skips any incoming
  case that matches, trimmed and case-insensitive. Re-running a batch is therefore safe.
- `dry_run: true` resolves folders and duplicate-checks but creates nothing — the response shows exactly
  what would be written.
- TestRail has **no bulk-create endpoint**, so the server loops `add_case`. A case that fails is recorded in
  `failed` and the rest still run; a `429` from TestRail Cloud is retried using its `Retry-After` header.
  Always surface `failed` — a silently dropped case is a test that never gets written.

Case content uses friendly aliases that map onto TestRail's field names: `preconditions` →
`custom_preconds`, `steps` → `custom_steps`, `expected` → `custom_expected`, and `steps_separated` →
`custom_steps_separated` (`[{"content": ..., "expected": ...}]`, which needs a steps-style template).
Anything else goes in `custom_fields`, where a missing `custom_` prefix is added for you. Run
`testrail_get_case_fields` first against an unfamiliar instance — a required custom field you didn't set is
the usual cause of a 400.

## Pagination

`testrail_get_cases` returns max 250 cases per call. Check if `size == limit` and increment `offset` to get the next page.
