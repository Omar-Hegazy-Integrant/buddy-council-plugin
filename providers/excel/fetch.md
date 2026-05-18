# Excel Requirements Fetch — Provider Skill

Fetch requirements from a Jama-exported Excel file. This is the temporary fallback while Jama API authentication is being resolved.

## Input

- `excel_path`: Absolute path to the Excel file (from `config/sources.json`)
- `scope`: Optional — a specific requirement ID (e.g., "CWA-REQ-85"), feature name, or "all"

## Configuration

Read `config/sources.json` to get the following from the `requirements` block:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `excel_path` | string | required | Absolute path to the Excel file |
| `skip_rows` | integer | `3` | Rows to skip before the header row (Jama metadata header) |
| `column_mapping` | object | absent → fall back to positional mode | Map canonical field name → real Excel column header |
| `feature_inference` | object | `{ strategy: "hierarchical_folder", folder_item_type: "Folder" }` | How to group rows into features |
| `item_type_filter` | array of string | `[]` (no filter) | Only emit rows whose Item Type appears in this list (after the feature row is detected) |

### `column_mapping` shape

```json
{
  "id": "ID",
  "title": "Name",
  "description": "Description",
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

Use a Python one-liner via Bash to parse the Excel and output JSON. The Python script reads the same config the agent loaded, so behavior is consistent across phases.

```bash
python3 - <<'PYEOF'
import pandas as pd, json, sys, re, os

CONFIG_PATH = os.environ['BC_CONFIG_PATH']   # absolute path to config/sources.json
EXCEL_PATH  = os.environ['BC_EXCEL_PATH']    # absolute path to the Excel file

with open(CONFIG_PATH) as f:
    cfg = json.load(f)
req_cfg = cfg.get('requirements', {})

skip_rows         = req_cfg.get('skip_rows', 3)
column_mapping    = req_cfg.get('column_mapping')   # may be None
feature_inference = req_cfg.get('feature_inference', {'strategy': 'hierarchical_folder', 'folder_item_type': 'Folder'})
item_type_filter  = set(req_cfg.get('item_type_filter', []) or [])

df = pd.read_excel(EXCEL_PATH, skiprows=skip_rows)

# Legacy positional fallback (preserves today's behavior when column_mapping is absent)
if not column_mapping:
    col_map = {}
    unnamed_idx = 0
    known_cols = ['ID', 'Description', 'Rationale', 'Item Type', 'Status', 'Jira ID', 'Tags', 'Configuration']
    for col in df.columns:
        if 'Unnamed' in str(col) and unnamed_idx < len(known_cols):
            col_map[col] = known_cols[unnamed_idx]
            unnamed_idx += 1
        else:
            col_map[col] = col
    df.rename(columns=col_map, inplace=True)
    column_mapping = {
        'id': 'ID',
        'title': df.columns[0],
        'description': 'Description',
        'status': 'Status',
        'item_type': 'Item Type',
    }

def cell(row, canonical):
    excel_col = column_mapping.get(canonical)
    if not excel_col or excel_col not in row.index:
        return ''
    v = row.get(excel_col)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    return str(v).strip()

GITHUB_URL_RE = re.compile(r'^https://github\.com/', re.IGNORECASE)

def extract_enrichment_urls(raw):
    if not raw:
        return []
    # Split on whitespace, commas, semicolons, or newlines
    candidates = re.split(r'[\s,;]+', raw)
    return [{'source': 'github', 'url': c} for c in candidates if c and GITHUB_URL_RE.match(c)]

results = []
strategy = feature_inference.get('strategy', 'hierarchical_folder')
folder_item_type = feature_inference.get('folder_item_type', 'Folder')
current_feature = 'Unknown'

for _, row in df.iterrows():
    item_type = cell(row, 'item_type')

    if strategy == 'hierarchical_folder' and item_type == folder_item_type:
        title_col = column_mapping.get('title') or df.columns[0]
        current_feature = str(row.get(title_col, 'Unknown')).strip() or 'Unknown'
        continue

    req_id = cell(row, 'id')
    if not req_id or req_id.lower() in ('nan', 'none', 'null'):
        continue
    if item_type_filter and item_type not in item_type_filter:
        continue

    if strategy == 'column':
        feature = cell(row, 'feature')
    elif strategy == 'none':
        feature = ''
    else:
        feature = current_feature

    requirement = {
        'type': 'requirement',
        'id': req_id,
        'title': cell(row, 'title'),
        'description': cell(row, 'description'),
        'feature': feature,
        'status': cell(row, 'status'),
        'linked_ids': [],
        'raw_fields': {k: str(v) for k, v in row.to_dict().items() if str(v) != 'nan'},
    }

    enrichment_urls = extract_enrichment_urls(cell(row, 'github_url'))
    if enrichment_urls:
        requirement['_enrichment_urls'] = enrichment_urls

    results.append(requirement)

json.dump(results, sys.stdout, indent=2)
PYEOF
```

Pass the config path and Excel path via environment variables, set by the calling skill:

```bash
BC_CONFIG_PATH="${CLAUDE_PLUGIN_ROOT}/config/sources.json" \
BC_EXCEL_PATH="$(jq -r '.requirements.excel_path' "${CLAUDE_PLUGIN_ROOT}/config/sources.json")" \
python3 ...
```

### Scoping

After fetching, filter by scope:

- If `scope` is a specific requirement ID (e.g., `"CWA-REQ-85"`): keep that requirement plus its siblings (same feature).
- If `scope` is a feature name (e.g., `"Patient Monitoring"`): keep all requirements whose `feature` matches.
- If `scope` is `"all"` or empty: return everything.

## Output

Return a JSON array of requirement objects in the canonical schema, with an optional transient `_enrichment_urls` field that the `enrich-requirements` skill consumes and removes:

```json
[
  {
    "type": "requirement",
    "id": "CWA-REQ-85",
    "title": "System shall display patient vitals",
    "description": "The system shall display patient vital signs in real-time...",
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

## Notes

- The Excel file is a Jama export with a specific column structure. The default `skip_rows: 3` matches that layout; other tools may need a different value.
- `linked_ids` is empty here — bidirectional linking happens in `normalize-artifacts` by matching test case references.
- The transient `_enrichment_urls` field is consumed and removed by `skills/enrich-requirements/SKILL.md` when enrichment is enabled. If enrichment is disabled, the field is dropped by `normalize-artifacts` before final output.
- If pandas is not installed, advise the user to `pip install pandas openpyxl`.
