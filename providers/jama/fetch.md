# Jama Requirements Fetch — Provider Skill

Fetch requirements live from **Jama Connect** through the plugin's own `jama` MCP server
(`mcp-servers/jama-server/`), which wraps Jama's REST API.

**The server is read-only by construction.** Every tool is named `jama_get_*`, and its HTTP client can only
issue GET requests — there is no tool that creates, edits, moves, or deletes anything in Jama. Never call the
Jama REST API with curl or Bash for data; the MCP server is the required access method. (`/bc:setup` runs the
server's `--check` mode once, deliberately, before the server is registered.)

## Prerequisites

`/bc:setup` registers the `jama` server in both runtime configs when Jama is the requirements source:
`.mcp.json` for Claude Code and `~/.copilot/mcp-config.json` for Copilot CLI. Check availability by looking
for `mcp__jama__jama_get_requirements` (Claude Code) or `jama_get_requirements` (Copilot CLI).

If the tools are NOT available:

- Tell the user to restart the runtime — the server is registered in config but only loads at startup. The
  first launch also provisions its Python dependencies through `uv`, which takes a few seconds.
- If the server loads but every call fails with "Jama is not configured" or a credentials error, the fix is
  `/bc:setup` — credentials live in `~/.buddy-council/secrets.json` under `jama`, the base URL in the MCP
  config's `JAMA_BASE_URL`.

## Configuration

Read the `requirements` block of `.buddy-council/sources.json` and pass these to the tool:

| Config field | Tool argument | Default | Purpose |
|---|---|---|---|
| `project_id` | `project_id` | required | Jama project id |
| `feature_inference.folder_item_type` | `feature_item_type` | `"Folder"` | Item type that marks a feature |
| `item_type_exclude` | `item_type_exclude` | `[]` | Item types to drop (e.g. `["Text", "Test Case"]`) |
| `item_type_filter` | `item_type_filter` | `[]` (no filter) | If set, keep only these item types |
| `field_mapping` | `field_mapping` | `{status: "status", rationale: "rationale"}` | Jama field labels for `status`, `rationale`, and `github_url` |

Folders, Sets, and Components are structure, never requirements — the server drops them on its own.

## Input

- `scope`: Optional — a requirement ID (Jama document key, e.g. `CWA-REQ-85`), a feature name or path, or
  `"all"`. Passed straight through as the tool's `scope` argument.

## How to Fetch

Call `jama_get_requirements` once:

```json
{
  "project_id": 42,
  "scope": "<the scope input, or empty for everything>",
  "feature_item_type": "Folder",
  "item_type_exclude": ["Text"],
  "field_mapping": { "status": "Status", "rationale": "Rationale", "github_url": "Linked to Github" }
}
```

The server reads the project tree with paginated GETs and caches it for 10 minutes, so several scoped fetches
in one run cost a single read. Pass `"refresh": true` only when the user says Jama changed in the last few
minutes.

**Follow `next_offset` until it is null.** A large result arrives in pages sized to fit one MCP response. When
the response carries `next_offset`, call `jama_get_requirements` again with the **same arguments** plus
`"offset": <next_offset>`, and concatenate every page's `requirements`. Later pages come from the cached tree,
so they cost no Jama requests. Stopping early silently drops requirements — `total` says how many to expect.

### Scoping — features are folders

Scoping happens **inside the server**, so only the relevant requirements enter context:

- **Feature name** (`"Patient Monitoring"`, case-insensitive) → every requirement under that folder,
  **sub-folders included**. A requirement's `feature` field is still its *nearest* folder, so one in
  `Patient Monitoring / Vitals` reports `feature: "Vitals"` but is returned for both scopes.
- **Feature path** (`"Software Requirements/Patient Monitoring"`) → disambiguates folders that share a name.
  When a bare name matches several folders the server returns all of them with a warning naming each path —
  relay it and offer the paths.
- **Requirement ID** (`"CWA-REQ-85"`, case-insensitive) → that requirement plus every requirement in the same
  feature (the Excel provider's semantics).
- **`"all"` or empty** → every requirement.
- **No match** → an empty array plus a warning listing the available features. Relay that warning instead of
  treating it as "no requirements exist".

To show the user which features exist (or to plan onboarding), call `jama_get_features` with the same
`project_id`, `feature_item_type`, and item-type settings. It returns each folder's `path`, `requirements`
(subtree count — what a feature-scoped fetch returns), and `direct_requirements` (those whose `feature` is
this folder), plus `outside_features` for requirements in no folder.

## Output

The response is `{project_id, scope, total, offset, next_offset, requirements, summary, warnings, fetched_at,
truncated}`.

- **`requirements` is already in the canonical schema** — return it unchanged. Each entry's `id` is the Jama
  document key, `feature` its nearest folder (falling back to the nearest Set/Component, then `"Unknown"`),
  `status`/`rationale` come from `field_mapping`, and `description` is Jama's rich text flattened to plain
  text. `raw_fields` carries `jama_id`, `item_type`, `feature_path`, `sequence`, `modified_date`, `url` (the
  item's Jama link), and the remaining custom fields by label, with pick lists resolved to names. People and
  release fields are left out.
- **`_enrichment_urls`** appears only when `field_mapping.github_url` names a field holding
  `https://github.com/` links — the same transient field the Excel parser emits, consumed by
  `enrich-requirements`.
- **`summary`** — print it as the provider's one-line summary (the Excel parser's `Summary:` equivalent).
- **`warnings`** — relay every entry to the user.
- **`truncated: true`** — the project exceeded the server's item cap and the result is incomplete. Treat the
  source as partially fetched under the Data Contract: say so, and ask whether to continue (output marked
  **PARTIAL**) or narrow the scope to a feature.

`linked_ids` is empty on these bulk fetches, exactly like the Excel provider — `normalize-artifacts`
cross-links requirements from the test-case side.

## One requirement with its traceability

For a deep dive on a single requirement — `/bc:ask` questions such as "what is CWA-REQ-85 derived from?" —
call `jama_get_item` with `item` set to the document key (or numeric Jama id) and `project_id`. It returns the
canonical requirement with `linked_ids` filled from Jama's upstream and downstream relationships, plus a
`relationships` object giving each related item's key, title, relationship type, and suspect flag.

## Error Handling

The server's errors are written for the user; relay them, then map the remedy:

- **"Jama is not configured" / "No Jama credentials"** → run `/bc:setup`.
- **Rejected API client ID/secret, or HTTP 401** → the credentials were rotated or revoked; regenerate them in
  Jama (profile → Set API Credentials) and re-run `/bc:setup`.
- **HTTP 403** → the account lacks read permission on the project, or is not enabled under Admin → REST API →
  Managed Access Control. Name both; the response cannot distinguish them.
- **HTTP 404 / "returned no items"** → wrong `project_id`; list projects with `jama_get_projects` and re-run
  `/bc:setup`.
- **"Could not reach …"** → network. A self-hosted Jama usually needs the corporate VPN — say so.
- **Rate limiting (429)** is retried automatically with the server's `Retry-After`; an error means it
  persisted — wait a minute or narrow the scope.

Never fabricate requirements when a fetch fails. Report the failure and let the caller's Data Contract handling
decide whether to continue with partial data.
