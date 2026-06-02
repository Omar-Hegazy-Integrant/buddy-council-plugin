"""Parse a Jama-exported Excel requirements file into canonical requirement JSON.

Reads two environment variables and writes a JSON array to stdout:

  BC_CONFIG_PATH  absolute path to config/sources.json
  BC_EXCEL_PATH   absolute path to the Excel file

Parsing is driven by the ``requirements`` block of the config: ``skip_rows``,
``column_mapping``, ``feature_inference`` and ``item_type_filter``. When
``column_mapping`` is absent it falls back to legacy positional mode, preserving
behavior for configs that predate the column-mapping wizard.

Invoked by ``providers/excel/fetch.md``. The output schema is documented there.
"""

import json
import os
import re
import sys

import pandas as pd

GITHUB_URL_RE = re.compile(r"^https://github\.com/", re.IGNORECASE)


def extract_enrichment_urls(raw: str) -> list[dict]:
    """Pull GitHub URLs from a free-text cell (whitespace/comma/semicolon separated)."""
    if not raw:
        return []
    candidates = re.split(r"[\s,;]+", raw)
    return [{"source": "github", "url": c} for c in candidates if c and GITHUB_URL_RE.match(c)]


def main() -> None:
    try:
        config_path = os.environ["BC_CONFIG_PATH"]
        excel_path = os.environ["BC_EXCEL_PATH"]
    except KeyError as missing:
        print(
            f"ERROR: missing required environment variable {missing}. "
            "Set BC_CONFIG_PATH and BC_EXCEL_PATH before running.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    with open(config_path) as f:
        cfg = json.load(f)
    req_cfg = cfg.get("requirements", {})

    skip_rows = req_cfg.get("skip_rows", 3)
    column_mapping = req_cfg.get("column_mapping")  # may be None
    feature_inference = req_cfg.get(
        "feature_inference",
        {"strategy": "hierarchical_folder", "folder_item_type": "Folder"},
    )
    item_type_filter = set(req_cfg.get("item_type_filter", []) or [])

    df = pd.read_excel(excel_path, skiprows=skip_rows)

    # Legacy positional fallback (preserves behavior when column_mapping is absent)
    if not column_mapping:
        col_map = {}
        unnamed_idx = 0
        known_cols = ["ID", "Description", "Rationale", "Item Type", "Status", "Jira ID", "Tags", "Configuration"]
        for col in df.columns:
            if "Unnamed" in str(col) and unnamed_idx < len(known_cols):
                col_map[col] = known_cols[unnamed_idx]
                unnamed_idx += 1
            else:
                col_map[col] = col
        df.rename(columns=col_map, inplace=True)
        column_mapping = {
            "id": "ID",
            "title": df.columns[0],
            "description": "Description",
            "status": "Status",
            "item_type": "Item Type",
        }

    def cell(row, canonical: str) -> str:
        excel_col = column_mapping.get(canonical)
        if not excel_col or excel_col not in row.index:
            return ""
        v = row.get(excel_col)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return str(v).strip()

    results = []
    strategy = feature_inference.get("strategy", "hierarchical_folder")
    folder_item_type = feature_inference.get("folder_item_type", "Folder")
    current_feature = "Unknown"

    for _, row in df.iterrows():
        item_type = cell(row, "item_type")

        if strategy == "hierarchical_folder" and item_type == folder_item_type:
            title_col = column_mapping.get("title") or df.columns[0]
            current_feature = str(row.get(title_col, "Unknown")).strip() or "Unknown"
            continue

        req_id = cell(row, "id")
        if not req_id or req_id.lower() in ("nan", "none", "null"):
            continue
        if item_type_filter and item_type not in item_type_filter:
            continue

        if strategy == "column":
            feature = cell(row, "feature")
        elif strategy == "none":
            feature = ""
        else:
            feature = current_feature

        requirement = {
            "type": "requirement",
            "id": req_id,
            "title": cell(row, "title"),
            "description": cell(row, "description"),
            "feature": feature,
            "status": cell(row, "status"),
            "linked_ids": [],
            "raw_fields": {k: str(v) for k, v in row.to_dict().items() if str(v) != "nan"},
        }

        enrichment_urls = extract_enrichment_urls(cell(row, "github_url"))
        if enrichment_urls:
            requirement["_enrichment_urls"] = enrichment_urls

        results.append(requirement)

    json.dump(results, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
