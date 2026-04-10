# Excel Requirements Fetch — Provider Skill

Fetch requirements from a Jama-exported Excel file. This is the temporary fallback while Jama API authentication is being resolved.

## Input

- `excel_path`: Absolute path to the Excel file (from `config/sources.json`)
- `scope`: Optional — a specific requirement ID (e.g., "CWA-REQ-85"), feature name, or "all"

## How to Fetch

1. Read `config/sources.json` to get the `excel_path`
2. Use a Python one-liner via Bash to parse the Excel and output JSON:

```bash
python3 -c "
import pandas as pd, json, sys

df = pd.read_excel('EXCEL_PATH', skiprows=3)

# Rename unnamed columns to match Jama export structure
col_map = {}
unnamed_idx = 0
known_cols = ['ID', 'Description', 'Rationale', 'Item Type', 'Status', 'Jira ID', 'Tags', 'Configuration']
for i, col in enumerate(df.columns):
    if 'Unnamed' in str(col) and unnamed_idx < len(known_cols):
        col_map[col] = known_cols[unnamed_idx]
        unnamed_idx += 1
    else:
        col_map[col] = col
df.rename(columns=col_map, inplace=True)

# Group by folders (Item Type == 'Folder') to extract features
results = []
current_feature = 'Unknown'
for _, row in df.iterrows():
    item_type = str(row.get('Item Type', ''))
    if item_type == 'Folder':
        current_feature = str(row.get('Name', row.get(df.columns[0], 'Unknown')))
        continue
    req_id = str(row.get('ID', ''))
    if not req_id or req_id == 'nan':
        continue
    results.append({
        'type': 'requirement',
        'id': req_id.strip(),
        'title': str(row.get(df.columns[0], '')).strip(),
        'description': str(row.get('Description', '')).strip(),
        'feature': current_feature,
        'status': str(row.get('Status', '')).strip(),
        'linked_ids': [],
        'raw_fields': {k: str(v) for k, v in row.to_dict().items() if str(v) != 'nan'}
    })
json.dump(results, sys.stdout, indent=2)
"
```

3. If `scope` is a specific requirement ID, filter the output to that requirement plus its siblings (same feature)
4. If `scope` is a feature name, filter to all requirements in that feature
5. If `scope` is "all", return everything

## Output

Return a JSON array of requirement objects in the canonical schema:

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
    "raw_fields": { ... }
  }
]
```

## Notes

- The Excel file is a Jama export with a specific column structure
- First 3 rows are metadata and should be skipped
- Folders define feature grouping — requirements under a folder belong to that feature
- `linked_ids` will be empty from Excel — linking happens in the normalization step by matching against test case data
- If pandas is not installed, fall back to `openpyxl` or advise the user to install pandas
