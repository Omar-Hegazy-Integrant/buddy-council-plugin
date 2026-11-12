#!/usr/bin/env python3
"""Walk Plan steps for /bc:gather-locators that code can do. Standard library only; Python 3.9+.

They work in a run folder made by ``bc_run.py new --command gather-locators``:

  cases-<folder>-<hash>.json  one TestRail folder's cases, written by testrail_get_canonical_cases with
                              ``section`` and ``save_to`` (the name comes from ``folder_file``)
  check-cases.json            the folders asked for, and what check-cases found in each
  batches/batch-NNN.md        one batch of one folder's cases, at most PART_BYTES, for one extractor
  batches.json                each batch's id, file, folder, case IDs, size, and the fragment file it gets
  fragments/batch-NNN.json    what one extractor found in one batch, in FRAGMENT_SCHEMA (below)
  validation.json             each batch's validation attempts
  graph.json                  every valid fragment merged: screens, Screen States, components, labels,
                              Conditions, entries, and transitions, each with a slug ID, platforms, and sources
  near-duplicates.json        the pairs merge couldn't settle, for the main model
  decisions.json              the main model's answers to them, applied by every later merge

Subcommands, each printing one JSON object to stdout:

  check-cases RUN --folder PATH [--folder PATH]...   check each folder's file against sources.json: site,
                                                     project, suite, requirement field, the exact section, and
                                                     the count. One Fetch: line per folder; ``refetch`` names
                                                     the folders to fetch again, each to its ``file``. Run it
                                                     before fetching to learn every folder's save_to.
  batch RUN                                          check the folders again, then split their cases into
                                                     batches grouped by folder; print the batch list
  validate RUN BATCH-ID                              check one batch's fragment against FRAGMENT_SCHEMA and list
                                                     every error. ``retry`` says to ask that batch once more; a
                                                     second failure names the batch's case IDs instead
  merge RUN [--same '[["keep", "drop"], ...]']       merge every valid fragment into graph.json by normalized
                                                     name, name the batches left out and their cases, and list
                                                     near-duplicates; --same answers the last list (pairs left out
                                                     of it are different)

Nothing here talks to TestRail, and nothing is written outside the run folder.
"""

from __future__ import annotations

import argparse
import difflib
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
FRAGMENT_DIR = "fragments"
VALIDATION_FILE = "validation.json"
GRAPH_FILE = "graph.json"
NEAR_FILE = "near-duplicates.json"
DECISIONS_FILE = "decisions.json"
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
    for old in [*(run / FRAGMENT_DIR).glob("batch-*.json"), run / VALIDATION_FILE]:
        if old.exists():
            old.unlink()  # fragments of the old batches would cite the wrong cases
    batches: list[dict[str, Any]] = []
    split_cases: list[str] = []

    def add(folder: str, ids: list[str], body: str, note: str = "") -> None:
        batch_id = f"batch-{len(batches) + 1:03d}"
        text = batch_header(batch_id, folder, note) + body
        path = batch_dir / f"{batch_id}.md"
        bc_run.write_private(path, text)
        batches.append({"id": batch_id, "file": str(path), "folder": folder, "case_ids": ids,
                        "bytes": len(text.encode("utf-8")), "fragment": fragment_file(run, batch_id)})

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
        "batches": [{"id": b["id"], "file": b["file"], "folder": b["folder"], "cases": len(b["case_ids"]),
                     "fragment": b["fragment"]} for b in batches],
        "cases": len(seen),
        "batches_file": str(run / BATCHES_FILE),
    }
    if left_out:
        result["left_out"] = left_out
    if split_cases:
        result["split_cases"] = split_cases
    return result


# ---- Fragments: what one extractor writes for one batch -------------------------------------------------------

PLATFORMS = ("ios", "android")
ACTIONS = ("tap", "type", "select", "scroll", "back", "external")
NEEDS_COMPONENT = ("tap", "type", "select")
TEXT, NAMES, PLATFORM_LIST, SOURCES = "text", "names", "platforms", "sources"
STATE_REF = {"screen": TEXT, "state?": TEXT}

# The fragment schema, written once. A key ending in "?" may be left out. Things are named, not given IDs: merge makes
# the slug IDs from the names, so the same name in two batches becomes one node.
#   text       a non-empty string
#   names      a list of names; in "requires", "sets", "clears", and "conditions", each names a condition of this
#              fragment; in "states", each names a Screen State of the same screen
#   platforms  some of "ios", "android"; left out means both. Tag a node with one Platform when only one shows it
#   sources    the Test Case IDs the item comes from; every one must be a case in this batch
# A screen's "states" are versions whose set of elements differs (text that only changes value is not a new state).
# "default" is every screen's plain state: a reference may name it without listing it. A component or label with
# no "states" is in every state of its screen. A component that serves one purpose with a different control on each
# Platform is two components, each with its Platforms, sharing a "role". A label's "text_hint" is the text as the
# case wrote it. A transition goes from a Screen State to a Screen State; "state" left out means "default". Its
# "component" names a component of the "from" screen, and tap, type, and select need one. "back" only where a case
# shows the back tap. "external" is a step for a person, with an "instruction" such as "Turn off the network", and
# no component. "requires" are the Conditions a step needs; "sets" and "clears" are the Conditions it changes.
FRAGMENT_SCHEMA: dict[str, Any] = {
    "batch": TEXT,
    "conditions?": [{"name": TEXT, "description": TEXT, "sources": SOURCES}],
    "screens": [{
        "name": TEXT,
        "platforms?": PLATFORM_LIST,
        "sources": SOURCES,
        "states?": [{"name": TEXT, "requires?": NAMES, "platforms?": PLATFORM_LIST, "sources": SOURCES}],
        "components?": [{"name": TEXT, "kind": TEXT, "role?": TEXT, "states?": NAMES, "platforms?": PLATFORM_LIST,
                         "sources": SOURCES}],
        "labels?": [{"text_hint": TEXT, "purpose": TEXT, "states?": NAMES, "platforms?": PLATFORM_LIST,
                     "sources": SOURCES}],
    }],
    "entries?": [{"screen": TEXT, "state?": TEXT, "conditions?": NAMES, "platforms?": PLATFORM_LIST,
                  "sources": SOURCES}],
    "transitions?": [{
        "from": STATE_REF,
        "to": STATE_REF,
        "action": ACTIONS,
        "component?": TEXT,
        "instruction?": TEXT,
        "requires?": NAMES,
        "sets?": NAMES,
        "clears?": NAMES,
        "platforms?": PLATFORM_LIST,
        "sources": SOURCES,
    }],
}

GENERIC_SCREEN_WORDS = {"screen", "page", "view"}
DEFAULT_STATE_NAMES = {"", "default", "initial", "normal", "base"}
SIMILAR = 0.8  # name similarity that puts two nodes on the near-duplicate list
SAME_LABEL = 0.9  # hint similarity that merges two labels sharing a Screen State
NEAR_LIST_SIZE = 20


def fragment_file(run: Path, batch_id: str) -> str:
    return str(Path(run) / FRAGMENT_DIR / f"{batch_id}.json")


def tokens(text: Any) -> list[str]:
    return re.findall(r"[^\W_]+", str(text or "").casefold())


def slug(words: list[str], text: Any) -> str:
    """Words joined by "_"; a name with no word characters gets a short hash so it still has an ID."""
    return "_".join(words) or "x" + hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:8]


def screen_key(name: Any) -> str:
    """"Settings page", "Settings screen", and "settings" are one screen."""
    words = tokens(name)
    return slug([w for w in words if w not in GENERIC_SCREEN_WORDS] or words, name)


def state_key(name: Any) -> str:
    words = tokens(name)
    return "default" if " ".join(words) in DEFAULT_STATE_NAMES else slug(words, name)


def component_key(name: Any, kind: Any) -> str:
    """The name, ending in its kind: "Save" and "Save button" (a button) are both save_button."""
    words, kind_words = tokens(name), tokens(kind)
    if kind_words and (not words or words[-1] != kind_words[-1]):
        words = words + kind_words
    return slug(words, name)


def condition_key(name: Any) -> str:
    return "cond." + slug(tokens(name), name)


def hint_text(hint: Any) -> str:
    return " ".join(tokens(hint))


def similar(a: str, b: str, at_least: float) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() >= at_least


def join_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def shape_errors(value: Any, spec: Any, path: str, errors: list[str], batch: dict[str, Any]) -> None:
    """Every place the value differs from the schema, each with its path."""
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            errors.append(f"{path or 'the fragment'}: must be an object")
            return
        for key, sub in spec.items():
            name = key.rstrip("?")
            if name in value:
                shape_errors(value[name], sub, join_path(path, name), errors, batch)
            elif not key.endswith("?"):
                errors.append(f"{join_path(path, name)}: missing")
        known = {key.rstrip("?") for key in spec}
        errors.extend(f"{join_path(path, key)}: not in the schema" for key in value if key not in known)
    elif isinstance(spec, list):
        if not isinstance(value, list):
            errors.append(f"{path}: must be a list")
            return
        for i, item in enumerate(value):
            shape_errors(item, spec[0], f"{path}[{i}]", errors, batch)
    elif isinstance(spec, tuple):
        if value not in spec:
            errors.append(f"{path}: {value!r} is not one of {', '.join(spec)}")
    elif spec == TEXT:
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}: must be a non-empty string")
    elif spec in (NAMES, PLATFORM_LIST, SOURCES):
        if not isinstance(value, list):
            errors.append(f"{path}: must be a list")
            return
        if not value and spec != NAMES:
            errors.append(f"{path}: empty")
        for i, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{path}[{i}]: must be a non-empty string")
            elif spec == PLATFORM_LIST and item not in PLATFORMS:
                errors.append(f"{path}[{i}]: {item!r} is not one of {', '.join(PLATFORMS)}")
            elif spec == SOURCES and item not in batch["case_ids"]:
                errors.append(f"{path}: {item} is not a case in {batch['id']}")


def items(value: Any, key: str) -> list[tuple[int, dict[str, Any]]]:
    found = value.get(key) if isinstance(value, dict) else None
    return [(i, item) for i, item in enumerate(found) if isinstance(item, dict)] if isinstance(found, list) else []


def names(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


class FragmentIndex:
    """What a fragment declares, by key, so its references can be checked and turned into IDs."""

    def __init__(self, fragment: dict[str, Any]):
        self.conditions = {condition_key(c.get("name")) for _, c in items(fragment, "conditions")}
        self.screens: dict[str, dict[str, Any]] = {}
        for _, screen in items(fragment, "screens"):
            key = screen_key(screen.get("name"))
            entry = self.screens.setdefault(key, {"name": screen.get("name"), "states": {"default"}, "components": []})
            entry["states"].update(state_key(s.get("name")) for _, s in items(screen, "states"))
            entry["components"] += [c for _, c in items(screen, "components")]

    def component(self, screen: str, name: Any) -> list[dict[str, Any]]:
        words = tokens(name)
        return [c for c in self.screens.get(screen, {}).get("components", [])
                if tokens(c.get("name")) == words or component_key(c.get("name"), c.get("kind")) == "_".join(words)]


def reference_errors(fragment: dict[str, Any], batch: dict[str, Any]) -> list[str]:
    """Names that point at nothing in the fragment, and steps that break the transition rules."""
    errors: list[str] = []
    index = FragmentIndex(fragment)
    if isinstance(fragment.get("batch"), str) and fragment["batch"] != batch["id"]:
        errors.append(f"batch: is {fragment['batch']!r}, not {batch['id']!r}")

    def conditions(path: str, value: Any) -> None:
        errors.extend(f"{path}: no condition {name!r} in this fragment"
                      for name in names(value) if condition_key(name) not in index.conditions)

    def state_ref(path: str, ref: Any) -> str:
        if not isinstance(ref, dict) or not isinstance(ref.get("screen"), str):
            return ""
        key = screen_key(ref["screen"])
        if key not in index.screens:
            errors.append(f"{path}: no screen {ref['screen']!r} in this fragment")
            return ""
        if "state" in ref and state_key(ref["state"]) not in index.screens[key]["states"]:
            errors.append(f"{path}.state: no state {ref['state']!r} on screen {ref['screen']!r}")
        return key

    for i, screen in items(fragment, "screens"):
        known = index.screens[screen_key(screen.get("name"))]["states"]
        for j, state in items(screen, "states"):
            conditions(f"screens[{i}].states[{j}].requires", state.get("requires"))
        for part in ("components", "labels"):
            for j, item in items(screen, part):
                errors.extend(f"screens[{i}].{part}[{j}].states: {name!r} is not a state of screen {screen.get('name')!r}"
                              for name in names(item.get("states")) if state_key(name) not in known)
    for i, entry in items(fragment, "entries"):
        state_ref(f"entries[{i}]", entry)
        conditions(f"entries[{i}].conditions", entry.get("conditions"))
    for i, step in items(fragment, "transitions"):
        path = f"transitions[{i}]"
        source = state_ref(f"{path}.from", step.get("from"))
        state_ref(f"{path}.to", step.get("to"))
        for key in ("requires", "sets", "clears"):
            conditions(f"{path}.{key}", step.get(key))
        action, component = step.get("action"), step.get("component")
        if action == "external":
            if not step.get("instruction"):
                errors.append(f"{path}.instruction: an external step needs an instruction for a person")
            if component:
                errors.append(f"{path}.component: an external step acts on no component")
        elif step.get("instruction"):
            errors.append(f"{path}.instruction: only an external step has an instruction")
        if action in NEEDS_COMPONENT and not component:
            errors.append(f"{path}.component: a {action} needs the component it acts on")
        if component and source and action != "external":
            found = index.component(source, component)
            screen = index.screens[source]["name"]
            if not found:
                errors.append(f"{path}.component: no component {component!r} on screen {screen!r}")
            elif len(found) > 1:
                errors.append(f"{path}.component: {component!r} names more than one component on screen {screen!r}; "
                              "add its kind to the name")
    return errors


def cited(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set(names(value.get("sources")))
        for key, sub in value.items():
            if key != "sources":
                found |= cited(sub)
        return found
    if isinstance(value, list):
        return set().union(*(cited(v) for v in value)) if value else set()
    return set()


def load_batches(run: Path) -> list[dict[str, Any]]:
    data = bc_run.read_json(run / BATCHES_FILE)
    if not isinstance(data, dict) or not isinstance(data.get("batches"), list):
        raise RunError(f"{BATCHES_FILE} is missing — run batch first.")
    return data["batches"]


def check_fragment(run: Path, batch: dict[str, Any]) -> tuple[Any, list[str], str]:
    """The fragment, every error in it, and a fingerprint of the file (to tell a new attempt from the same one)."""
    path = Path(batch.get("fragment") or fragment_file(run, batch["id"]))
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, [f"no fragment at {path} — the extractor wrote nothing"], "missing"
    fingerprint = hashlib.sha1(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        return None, [f"{path.name} is not valid JSON ({exc})."], fingerprint
    errors: list[str] = []
    shape_errors(data, FRAGMENT_SCHEMA, "", errors, batch)
    if isinstance(data, dict):
        errors += reference_errors(data, batch)
    return data, errors, fingerprint


def cases_text(ids: list[str]) -> str:
    return ", ".join(ids) or "none"


def cmd_validate(run_text: str, batch_id: str) -> dict[str, Any]:
    run = bc_run.run_folder(run_text)
    batch = next((b for b in load_batches(run) if b.get("id") == batch_id), None)
    if batch is None:
        raise RunError(f"no batch {batch_id!r} in {BATCHES_FILE}.")
    data, errors, fingerprint = check_fragment(run, batch)
    log = bc_run.read_json(run / VALIDATION_FILE) or {}
    last = log.get(batch_id) or {}
    attempts = int(last.get("attempts") or 0)
    if fingerprint != last.get("fingerprint"):
        attempts += 1  # re-checking an unchanged file is not a new attempt
    valid = not errors
    status = "ok" if valid else ("retry" if attempts < 2 else "failed")
    log[batch_id] = {"attempts": attempts, "fingerprint": fingerprint, "status": status, "checked_at": bc_run.utc_now()}
    bc_run.write_json(run / VALIDATION_FILE, log)
    result: dict[str, Any] = {"batch": batch_id, "fragment": fragment_file(run, batch_id), "valid": valid,
                              "status": status, "attempts": attempts, "retry": status == "retry", "errors": errors}
    if valid:
        result["uncited"] = sorted(set(batch["case_ids"]) - cited(data), key=natural)
    if status == "failed":
        result["case_ids"] = list(batch["case_ids"])
        result["line"] = (f"Extract: {batch_id} → FAILED twice — cases {cases_text(batch['case_ids'])} "
                          "are not in the graph")
    return result


# ---- Merge: every valid fragment into one graph ---------------------------------------------------------------


def natural(text: str) -> list[tuple[int, int, str]]:
    """TC-9 before TC-10."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", str(text))]


def platforms_of(item: dict[str, Any]) -> set[str]:
    return set(names(item.get("platforms"))) or set(PLATFORMS)


def ordered_platforms(found: set[str]) -> list[str]:
    return [p for p in PLATFORMS if p in found]


def sources_of(item: dict[str, Any]) -> set[str]:
    return set(names(item.get("sources")))


def pick(votes: dict[str, set[str]]) -> str:
    """The wording most cases use; then the shortest; then the first."""
    return min(votes, key=lambda text: (-len(votes[text]), len(text), text)) if votes else ""


def collect(fragments: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Every fragment's items as flat records with fragment-free IDs."""
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in
                                            ("screens", "states", "components", "labels", "conditions", "entries",
                                             "transitions")}
    for fragment in fragments:
        index = FragmentIndex(fragment)

        def state_id(ref: dict[str, Any]) -> str:
            return f"{screen_key(ref['screen'])}.{state_key(ref.get('state', 'default'))}"

        for _, cond in items(fragment, "conditions"):
            out["conditions"].append({"id": condition_key(cond["name"]), "description": cond["description"],
                                      "sources": sources_of(cond)})
        used_defaults: set[str] = set()
        for _, entry in items(fragment, "entries"):
            used_defaults.add(state_id(entry))
            out["entries"].append({"state": state_id(entry),
                                   "conditions": sorted({condition_key(c) for c in names(entry.get("conditions"))}),
                                   "platforms": platforms_of(entry), "sources": sources_of(entry)})
        for _, step in items(fragment, "transitions"):
            source, target = state_id(step["from"]), state_id(step["to"])
            used_defaults |= {source, target}
            record = {"from_state": source, "to_state": target, "action": step["action"],
                      "requires": sorted({condition_key(c) for c in names(step.get("requires"))}),
                      "sets": sorted({condition_key(c) for c in names(step.get("sets"))}),
                      "clears": sorted({condition_key(c) for c in names(step.get("clears"))}),
                      "platforms": platforms_of(step), "sources": sources_of(step)}
            if step.get("component"):
                screen = screen_key(step["from"]["screen"])
                found = index.component(screen, step["component"])[0]
                record["component"] = f"{screen}.{component_key(found['name'], found['kind'])}"
            if step.get("instruction"):
                record["instruction"] = step["instruction"].strip()
            out["transitions"].append(record)
        for _, screen in items(fragment, "screens"):
            sid = screen_key(screen["name"])
            out["screens"].append({"id": sid, "name": screen["name"].strip(), "platforms": platforms_of(screen),
                                   "sources": sources_of(screen)})
            states = items(screen, "states")
            for _, state in states:
                out["states"].append({"id": f"{sid}.{state_key(state['name'])}", "platforms": platforms_of(state),
                                      "requires": {condition_key(c) for c in names(state.get("requires"))},
                                      "sources": sources_of(state)})
            if not states or f"{sid}.default" in used_defaults:
                out["states"].append({"id": f"{sid}.default", "platforms": platforms_of(screen), "requires": set(),
                                      "sources": sources_of(screen)})

            def where(item: dict[str, Any]) -> set[str] | None:
                listed = names(item.get("states"))
                return {f"{sid}.{state_key(s)}" for s in listed} if listed else None

            for _, comp in items(screen, "components"):
                out["components"].append({"id": f"{sid}.{component_key(comp['name'], comp['kind'])}",
                                          "kind": "_".join(tokens(comp["kind"])) or comp["kind"],
                                          "role": comp.get("role", "").strip(), "states": where(comp),
                                          "platforms": platforms_of(comp), "sources": sources_of(comp)})
            for _, label in items(screen, "labels"):
                out["labels"].append({"screen": sid, "text_hint": label["text_hint"].strip(),
                                      "purpose": label["purpose"].strip(), "states": where(label),
                                      "platforms": platforms_of(label), "sources": sources_of(label)})
    return out


class Aliases:
    """The main model's answers: which IDs are the same node (drop → keep), and which listed pairs are not."""

    def __init__(self, decisions: dict[str, Any]):
        self.same = [d for d in decisions.get("same") or [] if isinstance(d, dict)]
        self.different = [d for d in decisions.get("different") or [] if isinstance(d, dict)]
        self.map: dict[str, str] = {}
        for answer in self.same:
            keep, drop = self.resolve(str(answer.get("keep"))), self.resolve(str(answer.get("drop")))
            if keep != drop:
                self.map[drop] = keep

    def resolve(self, node_id: str) -> str:
        """A node's ID once every answer is applied. A state's or component's ID follows its screen's first."""
        seen: set[str] = set()
        while node_id not in seen:
            seen.add(node_id)
            head, dot, rest = node_id.partition(".")
            if dot and head in self.map:  # screen IDs hold no dot, so only a screen's answer matches here
                node_id = f"{self.map[head]}.{rest}"
            elif node_id in self.map:
                node_id = self.map[node_id]
        return node_id

    def kept_apart(self, kind: str, a: str, b: str) -> bool:
        pair = {a, b}
        return any(d.get("kind") == kind and {self.resolve(i) for i in d.get("ids", [])} == pair for d in self.different)


def merged(records: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record[key], []).append(record)
    return groups


def union(records: list[dict[str, Any]], field: str) -> set[str]:
    return set().union(*(r[field] for r in records))


def node_base(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"platforms": ordered_platforms(union(records, "platforms")),
            "sources": sorted(union(records, "sources"), key=natural)}


def placed(groups: list[set[str] | None], all_states: list[str]) -> dict[str, Any]:
    """only_in, written only when the node isn't in every state of its screen."""
    if any(g is None for g in groups):
        return {}
    inside = set().union(*groups) if groups else set()
    return {} if inside >= set(all_states) else {"only_in": sorted(inside)}


def build_graph(records: dict[str, list[dict[str, Any]]], aliases: Aliases) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    resolve = aliases.resolve

    def remap(record: dict[str, Any], *fields: str) -> dict[str, Any]:
        out = dict(record)
        for field in fields:
            value = out.get(field)
            if isinstance(value, str):
                out[field] = resolve(value)
            elif isinstance(value, (set, list)):
                out[field] = {resolve(v) for v in value}
        return out

    conditions = []
    for cid, group in sorted(merged([remap(r, "id") for r in records["conditions"]], "id").items()):
        votes: dict[str, set[str]] = {}
        for r in group:
            votes.setdefault(r["description"].strip(), set()).update(r["sources"])
        conditions.append({"id": cid, "description": pick(votes), "sources": sorted(union(group, "sources"), key=natural)})

    states_by_screen: dict[str, list[dict[str, Any]]] = {}
    for sid, group in sorted(merged([remap(r, "id", "requires") for r in records["states"]], "id").items()):
        states_by_screen.setdefault(sid.split(".")[0], []).append(
            {"id": sid, "requires": sorted(union(group, "requires")), **node_base(group)})
    components_by_screen: dict[str, list[dict[str, Any]]] = {}
    for cid, group in sorted(merged([remap(r, "id", "states") for r in records["components"]], "id").items()):
        kinds = {r["kind"] for r in group}
        roles = sorted({r["role"] for r in group if r["role"]})
        if len(roles) > 1:
            warnings.append(f"{cid} has more than one role ({', '.join(roles)}); kept {roles[0]}.")
        screen = cid.split(".")[0]
        component = {"id": cid, "kind": sorted(kinds)[0], **({"role": roles[0]} if roles else {}),
                     "_states": [r["states"] for r in group], **node_base(group)}
        components_by_screen.setdefault(screen, []).append(component)
    labels = [remap(r, "screen", "states") for r in records["labels"]]

    screens = []
    for sid, group in sorted(merged([remap(r, "id") for r in records["screens"]], "id").items()):
        votes: dict[str, set[str]] = {}
        for r in group:
            votes.setdefault(r["name"], set()).update(r["sources"])
        states = states_by_screen.get(sid, [])
        state_ids = [s["id"] for s in states]
        components = []
        for comp in components_by_screen.get(sid, []):
            where = placed(comp.pop("_states"), state_ids)
            components.append({k: comp[k] for k in ("id", "kind", "role") if k in comp} | where |
                              {"platforms": comp["platforms"], "sources": comp["sources"]})
        screens.append({"id": sid, "name": pick(votes), **node_base(group), "states": states,
                        "components": components,
                        "labels": merge_labels(sid, [r for r in labels if r["screen"] == sid], state_ids)})

    entries = []
    for state, group in sorted(merged([remap(r, "state", "conditions") for r in records["entries"]],
                                      "state").items()):
        for conds, same in sorted(merged([{**r, "_key": "+".join(sorted(r["conditions"]))} for r in group],
                                         "_key").items()):
            entry_id = "entry." + state + "".join("+" + c for c in sorted(same[0]["conditions"]))
            entries.append({"id": entry_id, "state": state, "conditions": sorted(same[0]["conditions"]),
                            **node_base(same)})
    entries.sort(key=lambda e: e["id"])

    steps: dict[tuple, list[dict[str, Any]]] = {}
    for r in records["transitions"]:
        r = remap(r, "from_state", "to_state", "component", "requires", "sets", "clears")
        key = (r["from_state"], r["to_state"], r["action"], r.get("component", ""), r.get("instruction", ""),
               tuple(sorted(r["requires"])), tuple(sorted(r["sets"])), tuple(sorted(r["clears"])))
        steps.setdefault(key, []).append(r)
    transitions = []
    for key in sorted(steps):
        source, target, action, component, instruction, requires, sets, clears = key
        step: dict[str, Any] = {"from_state": source, "to_state": target, "action": action}
        if component:
            step["component"] = component
        if instruction:
            step["instruction"] = instruction
        transitions.append({**step, "requires": list(requires), "sets": list(sets), "clears": list(clears),
                            **node_base(steps[key])})
    graph = {"entries": entries, "conditions": conditions, "screens": screens, "transitions": transitions}
    return graph, warnings


def merge_labels(sid: str, records: list[dict[str, Any]], all_states: list[str]) -> list[dict[str, Any]]:
    """Labels with near-identical hints that share a Screen State are one label."""
    parent = list(range(len(records)))

    def root(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    def overlap(a: set[str] | None, b: set[str] | None) -> bool:
        return a is None or b is None or bool(a & b)

    for i, a in enumerate(records):
        for j in range(i + 1, len(records)):
            b = records[j]
            if overlap(a["states"], b["states"]) and similar(hint_text(a["text_hint"]), hint_text(b["text_hint"]),
                                                              SAME_LABEL):
                parent[root(j)] = root(i)
    groups: dict[int, list[dict[str, Any]]] = {}
    for i, record in enumerate(records):
        groups.setdefault(root(i), []).append(record)
    labels = []
    for group in groups.values():
        hints: dict[str, set[str]] = {}
        purposes: dict[str, set[str]] = {}
        for r in group:
            hints.setdefault(r["text_hint"], set()).update(r["sources"])
            purposes.setdefault(r["purpose"], set()).update(r["sources"])
        hint = pick(hints)
        labels.append({"text_hint": hint, "purpose": pick(purposes), **placed([r["states"] for r in group], all_states),
                       **node_base(group)})
    labels.sort(key=lambda l: (hint_text(l["text_hint"]), l["text_hint"], l.get("only_in", []), l["purpose"]))
    taken: set[str] = set()
    for label in labels:
        base = f"{sid}.label.{slug(tokens(label['text_hint'])[:8], label['text_hint'])}"
        label_id, n = base, 2
        while label_id in taken:
            label_id, n = f"{base}_{n}", n + 1
        taken.add(label_id)
        label["id"] = label_id
    return sorted(({"id": l.pop("id"), **l} for l in labels), key=lambda l: l["id"])


def name_like(a: str, b: str) -> bool:
    """Close enough to ask about: nearly the same spelling, or one name's words inside the other's."""
    wa, wb = set(a.split("_")), set(b.split("_"))
    return similar(a, b, SIMILAR) or (wa < wb or wb < wa)


def near_duplicates(graph: dict[str, Any], aliases: Aliases) -> list[dict[str, Any]]:
    """Pairs of nodes that may be one, which code can't settle: similar names, or screens with the same contents."""
    found: list[dict[str, Any]] = []

    def add(kind: str, a: dict[str, Any], b: dict[str, Any], why: str, name: str = "id") -> None:
        if not aliases.kept_apart(kind, a["id"], b["id"]):
            found.append({"kind": kind, "ids": [a["id"], b["id"]], "names": [a[name], b[name]],
                          "platforms": [a["platforms"], b["platforms"]], "why": why})

    def pairs(nodes: list[dict[str, Any]]):
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                yield a, b

    def rest(node_id: str) -> str:
        return node_id.split(".", 1)[1]

    for a, b in pairs(graph["screens"]):
        inside_a = {rest(c["id"]) for c in a["components"]} | {hint_text(l["text_hint"]) for l in a["labels"]}
        inside_b = {rest(c["id"]) for c in b["components"]} | {hint_text(l["text_hint"]) for l in b["labels"]}
        shared = inside_a & inside_b
        if name_like(a["id"], b["id"]):
            add("screen", a, b, "similar names", "name")
        elif len(shared) >= 2 and len(shared) * 2 >= len(inside_a | inside_b):
            add("screen", a, b, f"they share {len(shared)} of {len(inside_a | inside_b)} components and labels", "name")
    for screen in graph["screens"]:
        for a, b in pairs(screen["states"]):
            if name_like(rest(a["id"]), rest(b["id"])):
                add("state", a, b, "similar names")
        for a, b in pairs(screen["components"]):
            if a["kind"] == b["kind"] and name_like(rest(a["id"]), rest(b["id"])) and not (
                    a.get("role") and a.get("role") == b.get("role")):
                add("component", a, b, "similar names")
    for a, b in pairs(graph["conditions"]):
        if name_like(rest(a["id"]), rest(b["id"])):
            add("condition", a, b, "similar names")
    return found


def parse_same(text: str, listed: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The main model's answer: [[keep, drop], ...], each a pair from the list. Anything else is refused."""
    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise RunError(f"--same is not valid JSON ({exc}); pass a list of [keep, drop] ID pairs, or [] for none.")
    if not isinstance(answer, list) or not all(
            isinstance(p, list) and len(p) == 2 and all(isinstance(i, str) for i in p) for p in answer):
        raise RunError("--same must be a list of [keep, drop] ID pairs from the near-duplicate list, or [] for none.")
    by_pair = {frozenset(p["ids"]): p["kind"] for p in listed}
    same = []
    for keep, drop in answer:
        kind = by_pair.get(frozenset((keep, drop)))
        if kind is None:
            raise RunError(f"{keep} and {drop} are not a pair on the near-duplicate list; answer only listed pairs.")
        same.append({"kind": kind, "keep": keep, "drop": drop})
    return same


def cmd_merge(run_text: str, same_text: str | None = None) -> dict[str, Any]:
    run = bc_run.run_folder(run_text)
    fragments, left_out = [], []
    for batch in load_batches(run):
        data, errors, fingerprint = check_fragment(run, batch)  # merge checks again: an agent's word is not trusted
        if errors:
            reason = "no fragment was written" if fingerprint == "missing" else "the fragment does not match the schema"
            left_out.append({"batch": batch["id"], "case_ids": list(batch["case_ids"]), "reason": reason})
        else:
            fragments.append(data)
    if not fragments:
        raise RunError("no fragment to merge — validate each batch's fragment first; "
                       + "; ".join(f"{o['batch']}: {o['reason']}" for o in left_out))
    records = collect(fragments)
    decisions = bc_run.read_json(run / DECISIONS_FILE) or {}
    aliases = Aliases(decisions)
    graph, warnings = build_graph(records, aliases)
    if same_text is not None:
        listed = near_duplicates(graph, aliases)[:NEAR_LIST_SIZE]
        same = parse_same(same_text, listed)
        chosen = {frozenset((s["keep"], s["drop"])) for s in same}
        decisions = {
            "same": [*aliases.same, *same],
            "different": [*aliases.different, *({"kind": p["kind"], "ids": p["ids"]} for p in listed
                                                if frozenset(p["ids"]) not in chosen)],
        }
        aliases = Aliases(decisions)
        graph, warnings = build_graph(records, aliases)
        bc_run.write_json(run / DECISIONS_FILE, decisions)
    near = near_duplicates(graph, aliases)
    shown = near[:NEAR_LIST_SIZE]
    bc_run.write_json(run / GRAPH_FILE, graph)
    bc_run.write_json(run / NEAR_FILE, {"near_duplicates": shown, "more": len(near) - len(shown)})
    counts = {"screens": len(graph["screens"]),
              "states": sum(len(s["states"]) for s in graph["screens"]),
              "components": sum(len(s["components"]) for s in graph["screens"]),
              "labels": sum(len(s["labels"]) for s in graph["screens"]),
              "conditions": len(graph["conditions"]), "entries": len(graph["entries"]),
              "transitions": len(graph["transitions"])}
    lines = [f"Merge: {len(fragments)} fragments → " + ", ".join(f"{n} {k}" for k, n in counts.items())]
    lines += [f"Extract: {o['batch']} → LEFT OUT — {o['reason']}; cases {cases_text(o['case_ids'])} are not in the graph"
              for o in left_out]
    result: dict[str, Any] = {"graph": str(run / GRAPH_FILE), "counts": counts, "lines": lines,
                              "near_duplicates": shown}
    if len(near) > len(shown):
        result["more_near_duplicates"] = len(near) - len(shown)
    if left_out:
        result["left_out"] = left_out
    if warnings:
        result["warnings"] = warnings
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buddy-Council Walk Plan steps.")
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check-cases", help="check each folder's fetched cases against sources.json")
    check.add_argument("run", help="the run folder")
    check.add_argument("--folder", action="append", required=True, help="a TestRail folder path, e.g. 'App/Settings'")
    batch = sub.add_parser("batch", help="split the checked cases into batches of at most 18 KB, by folder")
    batch.add_argument("run", help="the run folder")
    validate = sub.add_parser("validate", help="check one batch's fragment against the schema")
    validate.add_argument("run", help="the run folder")
    validate.add_argument("batch", help="the batch id, e.g. batch-001")
    merge = sub.add_parser("merge", help="merge every valid fragment into graph.json and list near-duplicates")
    merge.add_argument("run", help="the run folder")
    merge.add_argument("--same", help="answers to the near-duplicate list: '[[\"keep-id\", \"drop-id\"], ...]', "
                                      "or '[]' when none are the same")
    args = parser.parse_args(argv)
    try:
        if args.action == "check-cases":
            result = cmd_check_cases(args.run, args.folder)
        elif args.action == "batch":
            result = cmd_batch(args.run)
        elif args.action == "validate":
            result = cmd_validate(args.run, args.batch)
        else:
            result = cmd_merge(args.run, args.same)
    except (RunError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
