# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pandas>=2.0,<3",
#     "openpyxl>=3.1,<4",
#     "xlrd>=2.0,<3",
# ]
# ///
"""Parse a Jama-exported Excel requirements file into canonical requirement JSON.

Run with ``uv run parse.py`` — the PEP 723 header above lets uv provision a
compatible Python plus pandas/openpyxl/xlrd automatically in an isolated,
cached environment. Nothing needs to be pip-installed on the host, and the
host's ``python3`` version is irrelevant.

Default mode (no arguments) reads environment variables and writes a JSON
array to stdout:

  BC_CONFIG_PATH  absolute path to .buddy-council/sources.json
  BC_EXCEL_PATH   absolute path to the Excel file
  BC_SCOPE        optional — a requirement ID (returns that requirement's
                  whole feature), a feature name, or "all"/empty for
                  everything

Parsing is driven by the ``requirements`` block of the config: ``skip_rows``,
``column_mapping``, ``feature_inference``, ``item_type_filter`` and
``item_type_exclude``. When ``column_mapping`` is absent it falls back to
legacy positional mode, preserving behavior for configs that predate the
column-mapping wizard.

The configured mapping is validated against the sheet (hard error if the id
column is missing, warnings for other stale columns), and a one-line summary
is printed to stderr so every fetch is observable. ``raw_fields`` carries
only the columns NOT already covered by ``column_mapping``.

Subcommands used by /bc:setup (these need only BC_EXCEL_PATH, plus optional
BC_SKIP_ROWS defaulting to 3, because the config file does not exist yet
while setup is running):

  headers                     JSON list of the sheet's column headers
  distinct <column>           JSON sorted list of distinct non-empty values
  sample <column>             JSON list of the first 20 non-empty values
  first-github-url <column>   first https://github.com/ URL in the column

Invoked by ``providers/excel/fetch.md`` and ``commands/setup.md``. The output
schema is documented in fetch.md.
"""

import json
import os
import re
import sys

import pandas as pd

GITHUB_URL_RE = re.compile(r"^https://github\.com/", re.IGNORECASE)

USAGE = """\
usage: uv run parse.py                          # full parse (BC_CONFIG_PATH + BC_EXCEL_PATH)
       uv run parse.py headers                  # list column headers
       uv run parse.py distinct <column>        # distinct non-empty values
       uv run parse.py sample <column>          # first 20 non-empty values
       uv run parse.py first-github-url <column>
Env: BC_EXCEL_PATH (required), BC_SKIP_ROWS (subcommands only, default 3)\
"""


def require_env(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        print(
            f"ERROR: missing required environment variable '{name}'.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def load_df(skip_rows: int) -> pd.DataFrame:
    excel_path = require_env("BC_EXCEL_PATH")
    return pd.read_excel(excel_path, skiprows=skip_rows)


def column_values(df: pd.DataFrame, column: str) -> list[str]:
    """Non-empty stripped values of a column, in row order. Exits if missing."""
    if column not in df.columns:
        print(
            f"ERROR: column '{column}' not found. "
            f"Sheet has: {[str(c) for c in df.columns]}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    stripped = (str(v).strip() for v in df[column].dropna())
    return [v for v in stripped if v and v.lower() not in ("nan", "none", "null")]


def extract_enrichment_urls(raw: str) -> list[dict]:
    """Pull GitHub URLs from a free-text cell (whitespace/comma/semicolon separated)."""
    if not raw:
        return []
    candidates = re.split(r"[\s,;]+", raw)
    return [{"source": "github", "url": c} for c in candidates if c and GITHUB_URL_RE.match(c)]


def env_skip_rows() -> int:
    return int(os.environ.get("BC_SKIP_ROWS", "3"))


def cmd_headers() -> None:
    df = load_df(env_skip_rows())
    json.dump([str(c) for c in df.columns], sys.stdout)


def cmd_distinct(column: str) -> None:
    df = load_df(env_skip_rows())
    json.dump(sorted(set(column_values(df, column))), sys.stdout)


def cmd_sample(column: str) -> None:
    df = load_df(env_skip_rows())
    json.dump(column_values(df, column)[:20], sys.stdout)


def cmd_first_github_url(column: str) -> None:
    df = load_df(env_skip_rows())
    for value in column_values(df, column):
        for candidate in re.split(r"[\s,;]+", value):
            if GITHUB_URL_RE.match(candidate):
                print(candidate)
                return


def legacy_positional_mapping(df: pd.DataFrame) -> dict:
    """Rename unnamed columns positionally and return the implied mapping."""
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
    return {
        "id": "ID",
        "title": df.columns[0],
        "description": "Description",
        "rationale": "Rationale",
        "status": "Status",
        "item_type": "Item Type",
    }


def validate_mapping(column_mapping: dict, df: pd.DataFrame) -> None:
    """Fail loudly on a stale id mapping; warn for other stale columns."""
    sheet_cols = {str(c) for c in df.columns}
    missing = {canon: col for canon, col in column_mapping.items() if col and col not in sheet_cols}
    if "id" in missing:
        print(
            f"ERROR: mapped id column '{missing['id']}' not found in the sheet. "
            f"Sheet has: {sorted(sheet_cols)}. Re-run /bc:setup to fix the column mapping.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    for canon, col in missing.items():
        print(
            f"WARNING: mapped {canon} column '{col}' not found in the sheet; "
            f"'{canon}' will be empty. Re-run /bc:setup if the sheet layout changed.",
            file=sys.stderr,
        )


def apply_scope(results: list[dict], scope: str) -> list[dict]:
    """Filter to a requirement's feature (ID scope) or a feature by name."""
    if not scope or scope.lower() == "all":
        return results
    lowered = scope.lower()
    by_id = next((r for r in results if r["id"].lower() == lowered), None)
    if by_id:
        return [r for r in results if r["feature"] == by_id["feature"]]
    by_feature = [r for r in results if r["feature"].lower() == lowered]
    if by_feature:
        return by_feature
    features = sorted({r["feature"] for r in results if r["feature"]})
    print(
        f"WARNING: scope '{scope}' matched no requirement ID or feature; "
        f"returning 0 requirements. Available features: {features}",
        file=sys.stderr,
    )
    return []


def run_parse() -> None:
    config_path = require_env("BC_CONFIG_PATH")
    scope = os.environ.get("BC_SCOPE", "").strip()

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
    item_type_exclude = set(req_cfg.get("item_type_exclude", []) or [])

    df = load_df(skip_rows)

    if not column_mapping:
        column_mapping = legacy_positional_mapping(df)
    else:
        validate_mapping(column_mapping, df)
    mapped_cols = {c for c in column_mapping.values() if c}

    def cell(row, canonical: str) -> str:
        excel_col = column_mapping.get(canonical)
        if not excel_col or excel_col not in row.index:
            return ""
        v = row.get(excel_col)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return str(v).strip()

    results = []
    skipped_excluded = 0
    skipped_filtered = 0
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
        if item_type in item_type_exclude:
            skipped_excluded += 1
            continue
        if item_type_filter and item_type not in item_type_filter:
            skipped_filtered += 1
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
            "rationale": cell(row, "rationale"),
            "feature": feature,
            "status": cell(row, "status"),
            "linked_ids": [],
            "raw_fields": {
                k: str(v)
                for k, v in row.to_dict().items()
                if str(v) != "nan" and k not in mapped_cols
            },
        }

        enrichment_urls = extract_enrichment_urls(cell(row, "github_url"))
        if enrichment_urls:
            requirement["_enrichment_urls"] = enrichment_urls

        results.append(requirement)

    scoped = apply_scope(results, scope)

    feature_count = len({r["feature"] for r in results if r["feature"]})
    summary = [f"{len(results)} requirements across {feature_count} features"]
    if skipped_excluded:
        summary.append(f"{skipped_excluded} rows skipped by item_type_exclude")
    if skipped_filtered:
        summary.append(f"{skipped_filtered} rows dropped by item_type_filter")
    if scope and scope.lower() != "all":
        summary.append(f"scope '{scope}' -> {len(scoped)} returned")
    print("Summary: " + "; ".join(summary), file=sys.stderr)

    json.dump(scoped, sys.stdout, indent=2)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        run_parse()
        return

    cmd, rest = args[0], args[1:]
    if cmd == "headers" and not rest:
        cmd_headers()
    elif cmd == "distinct" and len(rest) == 1:
        cmd_distinct(rest[0])
    elif cmd == "sample" and len(rest) == 1:
        cmd_sample(rest[0])
    elif cmd == "first-github-url" and len(rest) == 1:
        cmd_first_github_url(rest[0])
    else:
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
