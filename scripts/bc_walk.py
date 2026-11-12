#!/usr/bin/env python3
"""Walk Plan steps for /bc:gather-locators that code can do. Standard library only; Python 3.9+.

They work in a run folder made by ``bc_run.py new --command gather-locators``:

  cases-<folder>-<hash>.json  one TestRail folder's cases, written by testrail_get_canonical_cases with
                              ``section`` and ``save_to`` (the name comes from ``folder_file``)
  check-cases.json            the folders asked for, and what check-cases found in each
  batches/batch-NNN.md        one batch of one folder's cases, at most PART_BYTES, for one extractor
  batches.json                each batch's id, file, folder, case IDs, and size

Subcommands, each printing one JSON object to stdout:

  check-cases RUN --folder PATH [--folder PATH]...   check each folder's file against sources.json: site,
                                                     project, suite, requirement field, the exact section, and
                                                     the count. One Fetch: line per folder; ``refetch`` names
                                                     the folders to fetch again, each to its ``file``. Run it
                                                     before fetching to learn every folder's save_to.
  batch RUN                                          check the folders again, then split their cases into
                                                     batches grouped by folder; print the batch list

Nothing here talks to TestRail, and nothing is written outside the run folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bc_run  # noqa: E402
from bc_run import PART_BYTES, RunError  # noqa: E402

CHECK_FILE = "check-cases.json"
BATCHES_FILE = "batches.json"
BATCH_DIR = "batches"
SEPARATOR = "/"


def normalize_folder(text: str) -> str:
    """A TestRail folder path, trimmed: "/App / Settings/" is "App/Settings"."""
    return SEPARATOR.join(part.strip() for part in str(text or "").split(SEPARATOR) if part.strip())


def folder_file(run: Path, folder: str) -> str:
    """The file a folder's cases are saved to. The hash keeps folders that slug alike apart; the name fits the
    TestRail connector's save_to rule ([a-z0-9][a-z0-9_-]{0,63}.json)."""
    folder = normalize_folder(folder)
    slug = re.sub(r"[^a-z0-9]+", "-", folder.lower()).strip("-")[:40].strip("-") or "folder"
    digest = hashlib.sha1(folder.encode("utf-8")).hexdigest()[:8]
    return str(Path(run) / f"cases-{slug}-{digest}.json")


def in_folder(case: dict[str, Any], folder: str) -> bool:
    """The case is filed in the folder or under it. A case that names no folder is not judged."""
    path = normalize_folder((case.get("raw_fields") or {}).get("section_path") or "").casefold()
    wanted = folder.casefold()
    return not path or path == wanted or path.startswith(wanted + SEPARATOR)


def testrail_config(run: Path) -> dict[str, Any]:
    config = bc_run.read_json(run.parent.parent / "sources.json") or {}
    tests = config.get("test_cases") or {}
    if tests.get("provider") != "testrail":
        raise RunError("this project reads no test cases from TestRail — run /bc:setup with a link to the TestRail suite.")
    if tests.get("pending"):
        raise RunError("the TestRail connection is not verified yet — run /bc:setup to finish it.")
    return tests


def read_file(path: Path) -> tuple[Any, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as exc:
        return None, f"{path.name} is not valid JSON ({exc})."


def check_folder(run: Path, tests: dict[str, Any], folder: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """What one folder's file holds, and its cases inside the folder (only when it passes)."""
    path = Path(folder_file(run, folder))
    entry: dict[str, Any] = {"folder": folder, "file": str(path)}
    data, error = read_file(path)
    if error == "missing":
        return {**entry, "status": "missing"}, []
    problems = [error] if error else []
    if not error and not (isinstance(data, dict) and data.get("kind") == "test_cases" and data.get("source") == "testrail"):
        problems.append(f"{path.name} was not written by testrail_get_canonical_cases.")
    if problems:
        return {**entry, "status": "mismatch", "problems": problems}, []
    problems += bc_run.testrail_problems(tests, data, "the cases")
    selection = data.get("filter") or {}
    if str(selection.get("section") or "") != folder:
        problems.append(f"the cases were read with section {selection.get('section')!r}, not {folder!r}.")
    if selection.get("requirement_ids") or selection.get("unlinked"):
        problems.append("the cases were also filtered by requirement_ids or unlinked; the folder needs every case in it.")
    cases = data.get("test_cases")
    if not isinstance(cases, list):
        problems.append("the file holds no test_cases list.")
        cases = []
    if data.get("count") != len(cases):
        problems.append(f"the file records count {data.get('count')} but holds {len(cases)} cases.")
    warnings = [str(w) for w in data.get("warnings") or []]
    if warnings:
        entry["warnings"] = warnings
    if problems:
        return {**entry, "status": "mismatch", "problems": problems}, []
    inside = [c for c in cases if isinstance(c, dict) and in_folder(c, folder)]
    entry.update(status="ok" if inside else "empty", cases=len(inside), suite_id=data.get("suite_id"))
    if len(cases) != len(inside):
        entry["outside"] = len(cases) - len(inside)
    return entry, inside


def fetch_line(entry: dict[str, Any]) -> str:
    folder, status = entry["folder"], entry["status"]
    if status == "missing":
        return f"Fetch: {folder} → FAILED — no file in the run ({Path(entry['file']).name})"
    if status == "mismatch":
        return f"Fetch: {folder} → MISMATCH — {' '.join(entry['problems'])}"
    outside = f" ({entry['outside']} outside the folder left out)" if entry.get("outside") else ""
    if status == "empty":
        return f"Fetch: {folder} → no cases in {folder}{outside}"
    return f"Fetch: {folder} → {entry['cases']} cases from TestRail suite {entry.get('suite_id')}{outside}"


def check_all(run: Path, folders: list[str]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    tests = testrail_config(run)
    entries, cases = [], {}
    for folder in folders:
        entry, inside = check_folder(run, tests, folder)
        entries.append(entry)
        cases[folder] = inside
    return entries, cases


def unique_folders(texts: list[str]) -> list[str]:
    folders: list[str] = []
    for text in texts:
        folder = normalize_folder(text)
        if folder and folder.casefold() not in {f.casefold() for f in folders}:
            folders.append(folder)
    if not folders:
        raise RunError("no TestRail folder given — pass each one as --folder 'Path/To/Folder'.")
    return folders


def cmd_check_cases(run_text: str, folder_texts: list[str]) -> dict[str, Any]:
    run = bc_run.run_folder(run_text)
    folders = unique_folders(folder_texts)
    entries, _ = check_all(run, folders)
    bc_run.write_json(run / CHECK_FILE, {"checked_at": bc_run.utc_now(), "folders": entries})
    return {
        "lines": [fetch_line(e) for e in entries],
        "folders": entries,
        "refetch": [e["folder"] for e in entries if e["status"] in ("missing", "mismatch")],
        "warnings": [f"{e['folder']}: {w}" for e in entries for w in e.get("warnings", [])],
    }


def owner_of(case: dict[str, Any], folders: list[str]) -> str:
    """The deepest requested folder that holds the case, so a case two folders share is batched once."""
    holding = [f for f in folders if in_folder(case, f)]
    return max(holding, key=lambda f: len(f.split(SEPARATOR)))


def batch_header(batch_id: str, folder: str, note: str = "") -> str:
    return f"# Batch {batch_id} — folder {folder}{note}\n\n"


def cmd_batch(run_text: str) -> dict[str, Any]:
    run = bc_run.run_folder(run_text)
    checked = bc_run.read_json(run / CHECK_FILE)
    if not isinstance(checked, dict) or not checked.get("folders"):
        raise RunError(f"{CHECK_FILE} is missing — run check-cases first.")
    folders = [str(e.get("folder")) for e in checked["folders"] if e.get("folder")]
    entries, cases_by_folder = check_all(run, folders)  # a file changed since the check is never trusted
    left_out = []
    for entry in entries:
        if entry["status"] == "empty":
            left_out.append({"folder": entry["folder"], "reason": f"no cases in {entry['folder']}"})
        elif entry["status"] != "ok":
            left_out.append({"folder": entry["folder"], "reason": fetch_line(entry).split(" → ", 1)[1]})
    good = [e["folder"] for e in entries if e["status"] == "ok"]
    grouped: dict[str, list[dict[str, Any]]] = {f: [] for f in good}
    seen: set[str] = set()
    for folder in good:
        for case in cases_by_folder[folder]:
            cid = str(case.get("id") or "")
            if cid and cid not in seen and owner_of(case, good) == folder:
                seen.add(cid)
                grouped[folder].append(case)
    if not seen:
        raise RunError("no cases to batch — " + "; ".join(f"{e['folder']}: {e['reason']}" for e in left_out))

    batch_dir = run / BATCH_DIR
    batch_dir.mkdir(mode=0o700, exist_ok=True)
    for old in batch_dir.glob("batch-*.md"):
        old.unlink()
    batches: list[dict[str, Any]] = []
    split_cases: list[str] = []

    def add(folder: str, ids: list[str], body: str, note: str = "") -> None:
        batch_id = f"batch-{len(batches) + 1:03d}"
        text = batch_header(batch_id, folder, note) + body
        path = batch_dir / f"{batch_id}.md"
        bc_run.write_private(path, text)
        batches.append({"id": batch_id, "file": str(path), "folder": folder, "case_ids": ids,
                        "bytes": len(text.encode("utf-8"))})

    for folder in good:
        # What a batch's header leaves of PART_BYTES for its cases, with the longest header note this writes.
        room = PART_BYTES - len(batch_header("batch-000", folder, " — case , part 000 of 000").encode("utf-8"))
        ids: list[str] = []
        body, size = "", 0
        for case in grouped[folder]:
            block = bc_run.case_block(case)
            weight = len(block.encode("utf-8"))
            if body and size + weight > room:
                add(folder, ids, body)
                ids, body, size = [], "", 0
            if weight > room:
                cid = str(case["id"])
                parts = bc_run.split_parts(block, room - len(cid.encode("utf-8")))
                for n, part in enumerate(parts, start=1):
                    add(folder, [cid], part, f" — case {cid}, part {n} of {len(parts)}")
                split_cases.append(cid)
                continue
            ids.append(str(case["id"]))
            body += block
            size += weight
        if body:
            add(folder, ids, body)

    bc_run.write_json(run / BATCHES_FILE, {"created_at": bc_run.utc_now(), "limit_bytes": PART_BYTES,
                                           "batches": batches, "left_out": left_out, "split_cases": split_cases})
    result: dict[str, Any] = {
        "batches": [{"id": b["id"], "file": b["file"], "folder": b["folder"], "cases": len(b["case_ids"])} for b in batches],
        "cases": len(seen),
        "batches_file": str(run / BATCHES_FILE),
    }
    if left_out:
        result["left_out"] = left_out
    if split_cases:
        result["split_cases"] = split_cases
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buddy-Council Walk Plan steps.")
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check-cases", help="check each folder's fetched cases against sources.json")
    check.add_argument("run", help="the run folder")
    check.add_argument("--folder", action="append", required=True, help="a TestRail folder path, e.g. 'App/Settings'")
    batch = sub.add_parser("batch", help="split the checked cases into batches of at most 18 KB, by folder")
    batch.add_argument("run", help="the run folder")
    args = parser.parse_args(argv)
    try:
        if args.action == "check-cases":
            result = cmd_check_cases(args.run, args.folder)
        else:
            result = cmd_batch(args.run)
    except (RunError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
