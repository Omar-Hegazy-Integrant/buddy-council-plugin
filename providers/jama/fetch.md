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
| `roots[*].id` | `root_ids` | none — whole project | The requirements root(s): only what sits inside them is in scope |
| `exclude[*].id` | `exclude_ids` | none | Containers inside a root to leave out entirely |
| `feature_inference.depth` | `feature_depth` | `1` | With roots: the levels below a root that define a feature |
| `feature_inference.folder_item_type` | `feature_item_type` | `"Folder"` | Without roots: the item type that marks a feature |
| `item_type_exclude` | `item_type_exclude` | `[]` | Item types to drop (e.g. `["Text", "Test Case"]`) |
| `item_type_filter` | `item_type_filter` | `[]` (no filter) | If set, keep only these item types |
| `field_mapping` | `field_mapping` | `{status: "status", rationale: "rationale"}` | Jama field labels for `status`, `rationale`, and `github_url` |

Folders, Sets, and Components are structure, never requirements — the server drops them on its own.

**With `roots` configured** — `/bc:setup` anchors them on the requirement IDs the TestRail suite links to —
everything outside the roots is ignored, and a requirement's feature is the folder `depth` levels below its
root: the root's top-level folders by default, with deeper folders rolled up into them. **Without `roots`**,
the whole project is in scope and every folder is a feature. Always pass `root_ids`, `exclude_ids`, and
`feature_depth` when the config has them, on every Jama call — a fetch that drops them reads a different
scope with different feature names.

## Input

- `scope`: Optional — a requirement ID (Jama document key, e.g. `CWA-REQ-85`), a feature name or path, or
  `"all"`. Passed straight through as the tool's `scope` argument.

## How to Fetch

Call `jama_get_requirements` once:

```json
{
  "project_id": 42,
  "scope": "<the scope input, or empty for everything>",
  "root_ids": [1001],
  "feature_depth": 1,
  "item_type_filter": ["Mobile SRS"],
  "item_type_exclude": ["Text"],
  "field_mapping": { "status": "Status", "rationale": "Rationale", "github_url": "Linked to Github" }
}
```

The server reads the project tree with paginated GETs and caches it for 10 minutes, so several scoped fetches
in one run cost a single read. Pass `"refresh": true` only when the user says Jama changed in the last few
minutes.

**`loading: true` means call again.** A large project takes Jama a minute or more to send, longer than one tool
call may last, so the read carries on in the server and the call answers early with
`{loading: true, pages_read, pages_total, summary}`. Print one progress line (`Fetch: Jama project 42 —
reading, 85 of 99 pages`) and call `jama_get_requirements` again with the **same arguments**: it joins the
read already running and returns the requirements once it finishes. This is not a failure. Never fall back
to fetching requirements one by one — that is far slower than waiting for the tree. The same applies to
`jama_get_features`, `jama_get_item_types`, and `jama_get_scope_proposal`.

**Follow `next_offset` until it is null.** A large result arrives in pages sized to fit one MCP response. When
the response carries `next_offset`, call `jama_get_requirements` again with the **same arguments** plus
`"offset": <next_offset>`, and concatenate every page's `requirements`. Later pages come from the cached tree,
so they cost no Jama requests. Stopping early silently drops requirements — `total` says how many to expect.

**`save_to`** writes every requirement in scope into a run folder instead of returning them, and the response
carries only the counts and the file path. It is how `skills/collect-artifacts` collects for `/bc:coverage`
and `/bc:contradiction`; it accepts only a `.json` file directly inside an existing
`<project>/.buddy-council/runs/<run>/` folder. Don't use it elsewhere.

### Scoping — features are folders

Scoping happens **inside the server**, so only the relevant requirements enter context:

- **Feature name** (`"Patient Monitoring"`, case-insensitive) → every requirement under that folder,
  **sub-folders included**. With roots, only folders inside them can match, a sub-folder name still works
  (`"Vitals"` returns just that part of its feature), and a requirement's `feature` is its top-level folder —
  `raw_fields.folder_path` keeps its exact location. Without roots, a requirement's `feature` is its
  *nearest* folder, so one in `Patient Monitoring / Vitals` reports `feature: "Vitals"` but is returned for
  both scopes.
- **Feature path** (`"Software Requirements/Patient Monitoring"`) → disambiguates folders that share a name.
  When a bare name matches several folders the server returns all of them with a warning naming each path —
  relay it and offer the paths.
- **Requirement ID** (`"CWA-REQ-85"`, case-insensitive) → that requirement plus every requirement in the same
  feature (the Excel provider's semantics).
- **`"all"` or empty** → every requirement.
- **No match** → an empty array plus a warning listing the available features. Relay that warning instead of
  treating it as "no requirements exist".

To show the user which features exist (or to plan onboarding), call `jama_get_features` with the same
arguments. With roots it lists only the features that hold requirements, each with its `requirements` count
and the number of `folders` rolled into it. Without roots it lists every folder with `requirements` (subtree
count — what a feature-scoped fetch returns) and `direct_requirements` (those whose `feature` is this
folder), plus `outside_features` for requirements in no folder.

`jama_get_scope_proposal` is how `/bc:setup` chooses the roots: given the requirement IDs the test suite
links to, it proposes the smallest container(s) holding nearly all of them and previews the resulting
features. Analysis commands do not need it.

## Output

The response is `{project_id, scope, total, offset, next_offset, requirements, summary, warnings, fetched_at,
truncated}`.

- **`requirements` is already in the canonical schema** — return it unchanged. Each entry's `id` is the Jama
  document key and `feature` its top-level folder under the root — or, without roots, its nearest folder
  (falling back to the nearest Set/Component, then `"Unknown"`). With roots, two features that share a name
  are labelled with their paths, so they never merge. `status`/`rationale` come from `field_mapping`, and
  `description` is Jama's rich text flattened to plain text. `raw_fields` carries `jama_id`, `item_type`,
  `feature_path`, `sequence`, `modified_date`, `url` (the item's Jama link) — plus `folder_path`, the exact
  location, when roots are configured — and the remaining custom fields by label, with pick lists resolved to
  names. People and release fields are left out.
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

## Specific requirements by key, with their traceability

When you already know which requirements you need — the IDs a ticket or bug names, a test case's linked
requirements, `/bc:ask` questions such as "what is CWA-REQ-85 derived from?" — read them by key instead of
pulling a feature.

**A list of keys is one `jama_get_items` call.** Pass every key at once (up to 500), with `project_id` and the
same `field_mapping`, `root_ids`, and `feature_depth` as above:

```json
{
  "items": ["CWA-REQ-85", "CWA-REQ-86", "CWA-REQ-91"],
  "project_id": 42,
  "root_ids": [1001],
  "feature_depth": 1,
  "field_mapping": { "status": "Status", "rationale": "Rationale" }
}
```

The server searches the keys in batches, reads shared parent folders once, runs its requests in parallel, and
caches everything for 10 minutes. Never loop `jama_get_item` over a list, and never split a key list across
parallel calls or sub-agents: the server already parallelises against Jama's limits, so fanning out only
competes for the same requests and adds overhead.

- **Follow `next_offset` until it is null**, calling again with the same arguments plus `"offset"`, and
  concatenate `items`, `not_found`, and `failed`. Each call answers within about 25 s and later pages reuse
  what earlier ones read.
- Each entry in `items` is `{requirement, relationships, warnings?}`, in the order requested.
  `requirement` is canonical, with `linked_ids` filled from Jama's upstream and downstream relationships.
  `relationships` gives each related item's key, title, relationship type, and suspect flag.
- **`not_found`** keys do not exist (or not in `project_id`) — say so; never invent them. **`failed`** keys
  hit an error after retries — pass just those again once, then report what still fails.
- `include_relationships: false` skips the two traceability requests per item — most of the cost — when only
  the text is needed; `linked_ids` is then empty. `include_custom_fields: false` keeps entries small.

For exactly one item, `jama_get_item` returns the same entry as a single object
(`{requirement, relationships, warnings}`).

## Error Handling

The server's errors are written for the user; relay them, then map the remedy:

- **"Jama is not configured" / "No Jama credentials"** → run `/bc:setup`.
- **Rejected API client ID/secret, or HTTP 401** → the credentials were rotated or revoked; regenerate them in
  Jama (profile → Set API Credentials) and re-run `/bc:setup`.
- **HTTP 403** → the account lacks read permission on the project, or is not enabled under Admin → REST API →
  Managed Access Control. Name both; the response cannot distinguish them.
- **HTTP 404 / "returned no items"** → wrong `project_id`; list projects with `jama_get_projects` and re-run
  `/bc:setup`.
- **"Could not reach …"** → network. A self-hosted Jama usually needs the corporate VPN — say so. Dropped
  connections are already retried twice before this error appears.
- **"Reading project … from Jama failed"** → a background project read failed while no call was waiting.
  Relay it; calling again retries the read.
- **Rate limiting (429)** is retried automatically with the server's `Retry-After`; an error means it
  persisted — wait a minute or narrow the scope.

Never fabricate requirements when a fetch fails. Report the failure and let the caller's Data Contract handling
decide whether to continue with partial data.
