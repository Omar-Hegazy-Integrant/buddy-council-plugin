# Excel Requirements Fetch — Provider Skill

Fetch requirements from a Jama-exported Excel file. This is the temporary fallback while Jama API authentication is being resolved.

## Input

- `excel_path`: Absolute path to the Excel file (from `.buddy-council/sources.json`)
- `scope`: Optional — a specific requirement ID (e.g., "CWA-REQ-85"), feature name, or "all". Passed to the parser via the `BC_SCOPE` environment variable (see Scoping below).

## Configuration

Read `.buddy-council/sources.json` to get the following from the `requirements` block:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `excel_path` | string | required | Absolute path to the Excel file |
| `skip_rows` | integer | `3` | Rows to skip before the header row (Jama metadata header) |
| `column_mapping` | object | absent → fall back to positional mode | Map canonical field name → real Excel column header |
| `feature_inference` | object | `{ strategy: "hierarchical_folder", folder_item_type: "Folder" }` | How to group rows into features |
| `item_type_filter` | array of string | `[]` (no filter) | Only emit rows whose Item Type appears in this list (after the feature row is detected) |
| `item_type_exclude` | array of string | `[]` (nothing excluded) | Skip rows whose Item Type appears in this list — e.g. `["Text"]` for narrative rows that carry IDs but aren't requirements |

### `column_mapping` shape

```json
{
  "id": "ID",
  "title": "Name",
  "description": "Description",
  "rationale": "Rationale",
  "status": "Status",
  "item_type": "Item Type",
  "github_url": "Linked to Github"
}
```

Required keys: `id`. All other keys are optional — when absent, the corresponding canonical field is left empty for that requirement.

When `column_mapping` is **absent entirely**, fall back to the legacy positional mode (see "Backward-compat fallback" below). This preserves today's behavior for users who haven't re-run `/bc:setup`.

### `feature_inference` strategies

- **`hierarchical_folder`** (default) — a row whose `item_type` column equals `feature_inference.folder_item_type` (default `"Folder"`) marks a feature boundary. All subsequent rows belong to that feature until another folder row appears. The folder row itself is NOT emitted as a requirement.
- **`column`** — the configured `feature` column literally names each row's feature. Requires `column_mapping.feature` to be set.
- **`none`** — no grouping; every row gets `feature: ""`.

## How to Fetch

Run the committed parser script, passing the config and Excel paths via environment variables. The script reads the same `.buddy-council/sources.json` the agent already loaded (`skip_rows`, `column_mapping`, `feature_inference`, `item_type_filter`, `item_type_exclude`), so behavior is identical across phases — and the parsing logic stays out of the prompt context.

```bash
CFG="$PWD/.buddy-council/sources.json"
# plugin_root is recorded by /bc:setup; fall back to ${CLAUDE_PLUGIN_ROOT} (Claude Code)
ROOT="$(jq -r '.plugin_root // empty' "$CFG")"; [ -z "$ROOT" ] && ROOT="${CLAUDE_PLUGIN_ROOT}"
BC_CONFIG_PATH="$CFG" \
BC_EXCEL_PATH="$(jq -r '.requirements.excel_path' "$CFG")" \
BC_SCOPE="<the scope input, or empty for everything>" \
uv run "$ROOT/providers/excel/parse.py"
```

The script writes a JSON array of requirement objects to stdout. It applies `column_mapping` (or the legacy positional fallback when `column_mapping` is absent), groups rows into features per `feature_inference`, honors `item_type_exclude` and `item_type_filter`, and emits the transient `_enrichment_urls` field for any GitHub links found in the mapped `github_url` column. The script carries a PEP 723 metadata header, so `uv run` provisions a compatible Python plus pandas/openpyxl/xlrd automatically in a cached, isolated environment — no pip installs, and the host's `python3` version doesn't matter. The full source is `providers/excel/parse.py` inside the plugin install (the `plugin_root` recorded in config).

### Scoping

Scoping happens **inside the script** — pass the scope via `BC_SCOPE` so only the relevant rows ever enter context:

- A specific requirement ID (e.g., `"CWA-REQ-85"`, case-insensitive): returns that requirement plus its siblings (same feature).
- A feature name (e.g., `"Patient Monitoring"`, case-insensitive): returns all requirements whose `feature` matches.
- `"all"` or empty/unset: returns everything.
- No match: returns an empty array and prints a stderr warning listing the available features — relay that warning to the user instead of treating it as "no requirements exist".

## Output

Return a JSON array of requirement objects in the canonical schema, with an optional transient `_enrichment_urls` field that the `enrich-requirements` skill consumes and removes:

```json
[
  {
    "type": "requirement",
    "id": "CWA-REQ-85",
    "title": "System shall display patient vitals",
    "description": "The system shall display patient vital signs in real-time...",
    "rationale": "Clinicians need continuous visibility of vitals to react to deterioration...",
    "feature": "Patient Monitoring",
    "status": "Active",
    "linked_ids": [],
    "raw_fields": { "...": "..." },
    "_enrichment_urls": [
      { "source": "github", "url": "https://github.com/org/repo/blob/main/docs/sds/patient-monitoring.md" }
    ]
  }
]
```

The `_enrichment_urls` field is **only** present when:
- `column_mapping.github_url` is configured, AND
- the cell value matches `^https://github\.com/` after splitting on whitespace/comma/semicolon/newline.

If `column_mapping.github_url` is configured but no URL is found in the row, the field is omitted (not set to `[]`).

`raw_fields` contains only the sheet columns **not** covered by `column_mapping` — mapped columns already surface as top-level canonical fields, so they are not duplicated.

## Notes

- The Excel file is a Jama export with a specific column structure. The default `skip_rows: 3` matches that layout; other tools may need a different value.
- `linked_ids` is empty here — bidirectional linking happens in `normalize-artifacts` by matching test case references.
- The transient `_enrichment_urls` field is consumed and removed by `skills/enrich-requirements/SKILL.md` when enrichment is enabled. If enrichment is disabled, the field is dropped by `normalize-artifacts` before final output.
- The parser is a self-contained PEP 723 script run via `uv run` — dependencies are never installed on the host. If `uv` itself is missing, advise `brew install uv` (macOS) or https://docs.astral.sh/uv/ — uv is already required for the plugin's MCP servers, so it is normally present.
- Every run prints a one-line `Summary:` to stderr (requirement/feature counts, rows skipped by `item_type_exclude`/`item_type_filter`, scope result). The configured `column_mapping` is validated against the sheet: a stale `id` column is a hard error telling the user to re-run `/bc:setup`; other stale columns produce per-column warnings and empty canonical fields.
