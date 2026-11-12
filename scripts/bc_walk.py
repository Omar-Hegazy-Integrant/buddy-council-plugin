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
  planned-walks.json          plan's separate minimal Walks per Platform, in WALKS_SCHEMA (below)
  plan.json                   the graph.json plan read, the step cap, and the Screen States no Walk reaches
  refine-failures.json        each refined Walks file check replayed, its failures, and which Walks were used
  walks.json                  the Walks to walk: the checked refined ones, or plan's when they failed twice
  walks.md                    the Walks for people to read, with render's summary

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
  plan RUN                                           plan separate minimal Walks per Platform from graph.json:
                                                     no-Condition Walks first, external steps only where nothing
                                                     else reaches a state; name every state no Walk reaches
  check RUN WALKS-FILE                               replay a Walks file against graph.json and list every failure
                                                     by Walk and step; ``retry`` once, and a second failure makes
                                                     walks.json plan's Walks
  render RUN [--batches]                             write walks.md and print the run's summary

Nothing here talks to TestRail, and nothing is written outside the run folder.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import heapq
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
    (run / FRAGMENT_DIR).mkdir(mode=0o700, exist_ok=True)  # an extractor's file tool may not create it
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

# ---- Walks: plan them from graph.json, check any Walks against it, and render them ----------------------------

STEP_CAP = 40  # the soft cap on a Walk's steps; only a path longer than it on its own goes over
SPLIT_RATIO = 0.4  # above this share of single-Platform Screen States, the run recommends two plans
PLANNED_FILE = "planned-walks.json"
PLAN_FILE = "plan.json"
WALKS_FILE = "walks.json"
REFINE_FILE = "refine-failures.json"
WALKS_MD = "walks.md"

# walks.json's shape. A key ending in "?" may be left out. "entry" is an entry's ID from graph.json, "preconditions"
# are the Conditions prepared before the first step, and "compatible_pairs" lists every pair of Conditions the Walk
# holds at the same time. An external step carries the "instruction" a person follows.
WALKS_SCHEMA: dict[str, Any] = {
    "walks": [{
        "id": TEXT,
        "title": TEXT,
        "platform": PLATFORMS,
        "entry": TEXT,
        "preconditions": NAMES,
        "compatible_pairs": [NAMES],
        "steps": [{"from_state": TEXT, "action": ACTIONS, "component?": TEXT, "input?": TEXT, "instruction?": TEXT,
                   "to_state": TEXT, "expect_labels": NAMES}],
    }],
}


def fingerprint(path: Path) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "missing"


def load_graph(run: Path) -> dict[str, Any]:
    graph = bc_run.read_json(run / GRAPH_FILE)
    if not isinstance(graph, dict) or not isinstance(graph.get("screens"), list):
        raise RunError(f"{GRAPH_FILE} is missing — run merge first.")
    return graph


def on(item: dict[str, Any], platform: str) -> bool:
    return platform in (item.get("platforms") or PLATFORMS)


class Ways:
    """One Platform's view of the graph: its Screen States, components, labels, entries, and transitions."""

    def __init__(self, graph: dict[str, Any], platform: str):
        self.platform = platform
        self.names: dict[str, str] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.all_components: set[str] = set()
        self.components: set[str] = set()
        self.labels: dict[str, list[str]] = {}
        for screen in graph.get("screens", []):
            for state in screen.get("states", []):
                self.names[state["id"]] = screen.get("name") or screen["id"]
                if on(screen, platform) and on(state, platform):
                    self.states[state["id"]] = state
            for comp in screen.get("components", []):
                self.all_components.add(comp["id"])
                if on(screen, platform) and on(comp, platform):
                    self.components.add(comp["id"])
            for label in screen.get("labels", []):
                if not (on(screen, platform) and on(label, platform)):
                    continue
                for state in screen.get("states", []):
                    if not label.get("only_in") or state["id"] in label["only_in"]:
                        self.labels.setdefault(state["id"], []).append(label["id"])
        self.conditions = {c["id"] for c in graph.get("conditions", [])}
        self.entries = {e["id"]: e for e in graph.get("entries", []) if on(e, platform) and e["state"] in self.states}
        self.all_entries = {e["id"] for e in graph.get("entries", [])}
        self.all_transitions = list(graph.get("transitions", []))
        self.transitions = [t for t in self.all_transitions
                            if on(t, platform) and t["from_state"] in self.states and t["to_state"] in self.states
                            and (not t.get("component") or t["component"] in self.components)]
        self.out: dict[str, list[dict[str, Any]]] = {}
        for t in self.transitions:
            self.out.setdefault(t["from_state"], []).append(t)
        self.settable = {c for t in self.transitions for c in t.get("sets", [])}

    def requires(self, state: str) -> set[str]:
        return set(self.states[state].get("requires") or [])

    def expect_labels(self, state: str) -> list[str]:
        return sorted(self.labels.get(state, []))

    def title(self, state: str) -> str:
        name = self.names.get(state, state.split(".")[0])
        rest = state.split(".", 1)[1] if "." in state else "default"
        return name if rest == "default" else f"{name} ({rest.replace('_', ' ')})"

    def advance(self, held: frozenset, cleared: frozenset, t: dict[str, Any],
                prepare: bool) -> tuple[frozenset, frozenset, frozenset] | None:
        """The Conditions held and ever cleared after the step, and what had to be prepared for it; None if it can't.
        A Condition prepared now is held from the Walk's start, so one a step already cleared can't be."""
        missing = set(t.get("requires", [])) - held
        if missing and (not prepare or missing & cleared):
            return None
        after = ((held | missing) - set(t.get("clears", []))) | set(t.get("sets", []))
        gone = cleared | set(t.get("clears", []))
        need = self.requires(t["to_state"]) - after
        if need and (not prepare or need & gone):
            return None
        return frozenset(after | need), frozenset(gone), frozenset(missing | need)

    def search(self, starts: list[dict[str, Any]], prepare: bool, external: bool) -> dict[tuple, dict[str, Any]]:
        """Cheapest way to every (state, held, cleared) from the starts. Cost: Conditions prepared that a step could
        set, then external steps, then Conditions to prepare, then steps."""
        best: dict[tuple, dict[str, Any]] = {}
        queue: list[tuple] = []
        for n, node in enumerate(starts):
            heapq.heappush(queue, (self.cost(node), n, node))
        counter = len(starts)
        while queue:
            cost, _, node = heapq.heappop(queue)
            key = (node["state"], node["held"], node["cleared"])
            if key in best:
                continue
            best[key] = node
            for t in self.out.get(node["state"], []):
                if t["action"] == "external" and not external:
                    continue
                moved = self.advance(node["held"], node["cleared"], t, prepare)
                if moved is None:
                    continue
                held, cleared, prepared = moved
                nxt = {"state": t["to_state"], "held": held, "cleared": cleared, "entry": node["entry"],
                       "prepared": node["prepared"] | prepared, "external": node["external"] + (t["action"] == "external"),
                       "steps": node["steps"] + 1, "parent": node, "via": t}
                if (nxt["state"], held, cleared) not in best:
                    counter += 1
                    heapq.heappush(queue, (self.cost(nxt), counter, nxt))
        return best

    def cost(self, node: dict[str, Any]) -> tuple:
        entry = set(self.entries[node["entry"]]["conditions"])
        extra = node["prepared"] - entry
        return (len(extra & self.settable), node["external"], len(entry | node["prepared"]), node["steps"],
                node["state"], sorted(node["prepared"]))

    def start(self, entry_id: str, held: frozenset = frozenset()) -> dict[str, Any] | None:
        entry = self.entries[entry_id]
        have = frozenset(entry["conditions"]) | held
        need = self.requires(entry["state"]) - have
        return {"state": entry["state"], "held": have | need, "cleared": frozenset(), "entry": entry_id,
                "prepared": frozenset(need) | held, "external": 0, "steps": 0, "parent": None, "via": None}


def path_of(node: dict[str, Any], stop: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The transitions from stop (or the Walk's start) to node."""
    steps = []
    while node is not stop and node.get("via") is not None:
        steps.append(node["via"])
        node = node["parent"]
    return steps[::-1]


def nearest(found: dict[tuple, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each state's cheapest node."""
    out: dict[str, dict[str, Any]] = {}
    for node in found.values():
        if node["state"] not in out or node["steps"] < out[node["state"]]["steps"]:
            out[node["state"]] = node
    return out


def replay_conditions(ways: Ways, entry_id: str, steps: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
    """The fewest preconditions a valid sequence of transitions needs, and every pair of Conditions it holds at once."""
    entry = ways.entries[entry_id]
    prepared = set(entry["conditions"]) | ways.requires(entry["state"])
    held = set(prepared)
    for t in steps:
        missing = set(t.get("requires", [])) - held
        prepared |= missing
        held = ((held | missing) - set(t.get("clears", []))) | set(t.get("sets", []))
        need = ways.requires(t["to_state"]) - held
        prepared |= need
        held |= need
    # A Condition prepared for a later step is held from the start, so it is in every moment until a step clears it.
    pairs: set[tuple[str, str]] = set()
    held = set(prepared)
    pairs.update((a, b) for a in held for b in held if a < b)
    for t in steps:
        held = (held - set(t.get("clears", []))) | set(t.get("sets", []))
        pairs.update((a, b) for a in held for b in held if a < b)
    return sorted(prepared), [list(p) for p in sorted(pairs)]


def walk_step(ways: Ways, t: dict[str, Any]) -> dict[str, Any]:
    step: dict[str, Any] = {"from_state": t["from_state"], "action": t["action"]}
    if t.get("component"):
        step["component"] = t["component"]
    if t.get("instruction"):
        step["instruction"] = t["instruction"]
    return {**step, "to_state": t["to_state"], "expect_labels": ways.expect_labels(t["to_state"])}


def plan_platform(ways: Ways) -> tuple[list[dict[str, Any]], list[str]]:
    """Separate minimal Walks for one Platform: no-Condition Walks first, then Condition Walks, then external ones."""
    starts = [s for s in (ways.start(e) for e in sorted(ways.entries)) if s]
    best = nearest(ways.search(starts, prepare=True, external=True))
    targets = sorted(s for s in ways.states if s in best)
    unreached = sorted(s for s in ways.states if s not in best)
    groups: dict[tuple, list[str]] = {}
    for state in targets:
        node = best[state]
        prepared = tuple(sorted(set(ways.entries[node["entry"]]["conditions"]) | node["prepared"]))
        groups.setdefault((node["external"] > 0, len(prepared), node["entry"], prepared), []).append(state)
    covered: set[str] = set()
    walks: list[tuple[str, list[dict[str, Any]]]] = []
    for (external, _, entry_id, prepared), group in sorted(groups.items()):
        while set(group) - covered:
            node = ways.start(entry_id, frozenset(prepared))
            gained = {node["state"]} - covered
            covered.add(node["state"])
            steps: list[dict[str, Any]] = []
            while True:
                found = nearest(ways.search([dict(node, steps=0, external=0, parent=None, via=None)], False, external))
                options = sorted((n["steps"], s) for s, n in found.items() if s not in covered and s in best)
                if not options:
                    break
                pick_state = options[0][1]
                for _, state in options:
                    path = path_of(found[state])
                    after = covered | {t["to_state"] for t in path}
                    onward = nearest(ways.search([dict(found[state], steps=0, external=0, parent=None, via=None)],
                                                 False, external))
                    if any(s not in after and s in best for s in onward):
                        pick_state = state
                        break
                path = path_of(found[pick_state])
                if steps and len(steps) + len(path) > STEP_CAP:
                    break
                steps += path
                for t in path:
                    gained |= {t["to_state"]} - covered
                    covered.add(t["to_state"])
                node = found[pick_state]
            if not gained:  # nothing new from this start: can't happen with a sound search, but never loop
                unreached += sorted(set(group) - covered)
                covered |= set(group)
                break
            walks.append((entry_id, steps))
    out = []
    for n, (entry_id, steps) in enumerate(walks, 1):
        preconditions, pairs = replay_conditions(ways, entry_id, steps)
        start = ways.entries[entry_id]["state"]
        end = steps[-1]["to_state"] if steps else start
        out.append({"id": f"{ways.platform}-{n:02d}", "title": f"{ways.title(start)} to {ways.title(end)}",
                    "platform": ways.platform, "entry": entry_id, "preconditions": preconditions,
                    "compatible_pairs": pairs, "steps": [walk_step(ways, t) for t in steps]})
    return out, sorted(unreached)


def stale(run: Path) -> None:
    for name in (WALKS_FILE, REFINE_FILE, WALKS_MD):
        (run / name).unlink(missing_ok=True)


def cmd_plan(run_text: str) -> dict[str, Any]:
    run = bc_run.run_folder(run_text)
    graph = load_graph(run)
    walks: list[dict[str, Any]] = []
    unreached: list[dict[str, str]] = []
    for platform in PLATFORMS:
        found, missed = plan_platform(Ways(graph, platform))
        walks += found
        unreached += [{"platform": platform, "state": s} for s in missed]
    over = [{"walk": w["id"], "steps": len(w["steps"])} for w in walks if len(w["steps"]) > STEP_CAP]
    stale(run)
    bc_run.write_json(run / PLANNED_FILE, {"walks": walks})
    bc_run.write_json(run / PLAN_FILE, {"graph": fingerprint(run / GRAPH_FILE), "step_cap": STEP_CAP,
                                        "longest": max([len(w["steps"]) for w in walks] or [0]),
                                        "unreached": unreached})
    counts = {p: sum(1 for w in walks if w["platform"] == p) for p in PLATFORMS}
    lines = ["Plan: " + ", ".join(f"{p} {n} Walks" for p, n in counts.items())
             + f" ({sum(1 for w in walks if not w['preconditions'])} need no Condition); step cap {STEP_CAP}"]
    lines += [f"Plan: {o['walk']} has {o['steps']} steps, over the cap of {STEP_CAP} — its path is that long"
              for o in over]
    lines += [f"Plan: {u['platform']}: {u['state']} → NOT REACHED — no entry and transitions lead to it"
              for u in unreached]
    return {"walks_file": str(run / PLANNED_FILE), "walks": counts, "step_cap": STEP_CAP, "over_cap": over,
            "unreached": unreached, "lines": lines}


def walks_path(run: Path, text: str) -> Path:
    path = Path(text).expanduser()
    path = (path if path.is_absolute() else run / path).resolve()
    if run not in path.parents:
        raise RunError(f"{text} is not in the run folder {run}; write the Walks there.")
    return path


def replay(ways: Ways, walk: dict[str, Any], allowed: int) -> tuple[list[dict[str, Any]], set[str]]:
    """Every failure in one Walk, by step, and the Screen States it reaches."""
    failures: list[dict[str, Any]] = []

    def fail(step: int | None, problem: str) -> None:
        failures.append({"walk": walk["id"], "step": step, "problem": problem})

    entry = ways.entries.get(walk["entry"])
    if entry is None:
        fail(None, f"entry {walk['entry']} is " + ("not on " + ways.platform if walk["entry"] in ways.all_entries
                                                     else "not in the graph"))
        return failures, set()
    for c in walk["preconditions"]:
        if c not in ways.conditions:
            fail(None, f"precondition {c} is not a Condition in the graph")
    compatible = {tuple(sorted(p)) for p in walk["compatible_pairs"] if len(p) == 2}
    reported: set[tuple[str, str]] = set()

    def pairs(step: int | None, held: frozenset) -> None:
        for a, b in sorted((a, b) for a in held for b in held if a < b):
            if (a, b) not in compatible and (a, b) not in reported:
                reported.add((a, b))
                fail(step, f"{a} and {b} are held together but not in compatible_pairs")

    def why(c: str, cleared_at: dict[str, int]) -> str:
        return f"which step {cleared_at[c]} cleared" if c in cleared_at else "which is not prepared or set earlier"

    held = frozenset(entry["conditions"]) | frozenset(walk["preconditions"])
    for c in sorted(ways.requires(entry["state"]) - held):
        fail(None, f"{entry['state']} needs {c}, which is not prepared")
    pairs(None, held)
    cleared_at: dict[str, int] = {}
    position, reached = entry["state"], {entry["state"]}
    for n, step in enumerate(walk["steps"], 1):
        if step["from_state"] != position:
            fail(n, f"starts at {step['from_state']}, but the previous step ended at {position}")
        position = step["to_state"]
        reached.add(position)
        component = step.get("component")
        if component and component not in ways.components:
            fail(n, f"{component} is " + ("not on " + ways.platform if component in ways.all_components
                                          else "not a component in the graph"))
            continue
        alike = [t for t in ways.all_transitions
                 if (t["from_state"], t["to_state"], t["action"], t.get("component")) ==
                 (step["from_state"], step["to_state"], step["action"], component)
                 and (not step.get("instruction") or t.get("instruction") == step["instruction"])]
        what = f"the {step['action']} from {step['from_state']} to {step['to_state']}" + (
            f" with {component}" if component else "")
        if not alike:
            fail(n, f"{what} is not a transition in the graph")
            continue
        here = [t for t in alike if t in ways.transitions]
        if not here:
            fail(n, f"{what} is not on {ways.platform}")
            continue
        done = None
        for t in here:
            moved = ways.advance(held, frozenset(cleared_at), t, prepare=False)
            if moved is not None:
                done = t
                break
        if done is None:
            t = here[0]
            for c in sorted(set(t.get("requires", [])) - held):
                fail(n, f"the step needs {c}, {why(c, cleared_at)}")
            after = (held - set(t.get("clears", []))) | set(t.get("sets", []))
            for c in sorted(ways.requires(t["to_state"]) - after):
                fail(n, f"{t['to_state']} needs {c}, {why(c, {k: v for k, v in cleared_at.items() if k not in after})}")
            done = t
        held = (held - set(done.get("clears", []))) | set(done.get("sets", []))
        for c in done.get("clears", []):
            cleared_at[c] = n
        for c in done.get("sets", []):
            cleared_at.pop(c, None)
        pairs(n, held)
    if len(walk["steps"]) > allowed:
        fail(None, f"{len(walk['steps'])} steps; the cap is {allowed}")
    return failures, reached


def check_walks(graph: dict[str, Any], data: Any, planned: list[dict[str, Any]],
                allowed: int) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return [{"walk": None, "step": None, "problem": "the file must be an object with \"walks\""}]
    errors: list[str] = []
    shape_errors(data, WALKS_SCHEMA, "", errors, {})
    for i, walk in enumerate(data.get("walks") if isinstance(data.get("walks"), list) else []):
        for j, pair in enumerate(walk.get("compatible_pairs") or [] if isinstance(walk, dict) else []):
            if isinstance(pair, list) and len(pair) != 2:
                errors.append(f"walks[{i}].compatible_pairs[{j}]: must name two Conditions")
    ids = [w.get("id") for w in data.get("walks", []) if isinstance(w, dict)]
    errors += [f"walk id {i} is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if errors:
        return [{"walk": None, "step": None, "problem": e} for e in errors]
    failures: list[dict[str, Any]] = []
    ways = {p: Ways(graph, p) for p in PLATFORMS}
    reached: dict[str, set[str]] = {p: set() for p in PLATFORMS}
    for walk in data["walks"]:
        found, states = replay(ways[walk["platform"]], walk, allowed)
        failures += found
        reached[walk["platform"]] |= states
    for platform in PLATFORMS:
        needed = set().union(*[replay(ways[platform], w, 10 ** 9)[1] for w in planned if w["platform"] == platform])
        failures += [{"walk": None, "step": None, "problem": f"{platform}: {s} is no longer reached"}
                     for s in sorted(needed - reached[platform])]
    return failures


def keep_walks(data: dict[str, Any]) -> dict[str, Any]:
    return {"walks": data["walks"]}


def cmd_check(run_text: str, walks_text: str) -> dict[str, Any]:
    run = bc_run.run_folder(run_text)
    path = walks_path(run, walks_text)
    plan = bc_run.read_json(run / PLAN_FILE)
    planned = bc_run.read_json(run / PLANNED_FILE)
    if not isinstance(plan, dict) or not isinstance(planned, dict):
        raise RunError(f"{PLANNED_FILE} is missing — run plan first.")
    graph = load_graph(run)
    if fingerprint(run / GRAPH_FILE) != plan.get("graph"):
        raise RunError(f"{GRAPH_FILE} changed since plan — plan again, then refine and check its Walks.")
    allowed = max(STEP_CAP, int(plan.get("longest") or 0))
    own = path == (run / PLANNED_FILE).resolve()
    print_name = path.name
    mark = fingerprint(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data, failures = None, [{"walk": None, "step": None, "problem": f"no file at {path}"}]
    except ValueError as exc:
        data, failures = None, [{"walk": None, "step": None, "problem": f"{print_name} is not valid JSON ({exc})"}]
    if data is not None:
        failures = check_walks(graph, data, planned["walks"], allowed)
    log = bc_run.read_json(run / REFINE_FILE) or {"attempts": []}
    if not own and not any(a.get("fingerprint") == mark for a in log["attempts"]):
        log["attempts"].append({"file": str(path), "fingerprint": mark, "checked_at": bc_run.utc_now(),
                                "failures": failures})
    attempt = 0 if own else next(i for i, a in enumerate(log["attempts"], 1) if a["fingerprint"] == mark)
    result: dict[str, Any] = {"walks_file": str(path), "failures": failures, "attempt": attempt}
    if not failures:
        used = "planned" if own else "refined"
        bc_run.write_json(run / WALKS_FILE, keep_walks(data))
        log["used"] = used
        result |= {"status": "ok", "used": used,
                   "lines": [f"Check: {print_name} → ok, {len(data['walks'])} Walks; walks.json is "
                             + ("plan's Walks" if own else "the refined Walks")]}
    elif own:
        result |= {"status": "failed", "lines": [f"Check: {print_name} → {len(failures)} failures in plan's own "
                                                 "Walks — a bug in bc_walk.py; nothing to fall back to"]}
    elif attempt < 2:
        result |= {"status": "retry", "lines": [f"Check: {print_name} → {len(failures)} failures; give them to the "
                                                "refiner and check its next file once"]}
    else:
        log["used"] = "planned"
        bc_run.write_json(run / WALKS_FILE, keep_walks(planned))
        result |= {"status": "failed", "used": "planned",
                   "lines": [f"Check: {print_name} → FAILED again ({len(failures)} failures); walks.json is plan's "
                             "separate Walks (the fallback)"]}
    bc_run.write_json(run / REFINE_FILE, log)
    return result


def own_walk_states(ways: Ways, walks: list[dict[str, Any]]) -> list[str]:
    """States a Walk was made for alone: it needs an external step or a prepared Condition, and reaches exactly one
    Screen State no other Walk on its Platform reaches."""
    reach = [replay(ways, w, 10 ** 9)[1] for w in walks]
    out = []
    for i, walk in enumerate(walks):
        others = set().union(*(r for j, r in enumerate(reach) if j != i))
        alone = reach[i] - others
        needs = walk["preconditions"] or any(s["action"] == "external" for s in walk["steps"])
        if needs and len(alone) == 1:
            out += sorted(alone)
    return sorted(out)


def cmd_render(run_text: str, batches: bool = False) -> dict[str, Any]:
    run = bc_run.run_folder(run_text)
    walks_data = bc_run.read_json(run / WALKS_FILE)
    log = bc_run.read_json(run / REFINE_FILE) or {}
    if not isinstance(walks_data, dict) or not log.get("used"):
        raise RunError(f"{WALKS_FILE} is missing — run check first.")
    graph = load_graph(run)
    planned = (bc_run.read_json(run / PLANNED_FILE) or {}).get("walks", [])
    walks = walks_data["walks"]
    batch_list = (bc_run.read_json(run / BATCHES_FILE) or {}).get("batches", [])
    states = [s for screen in graph["screens"] for s in screen["states"]]
    single = sum(1 for s in states if len(set(s.get("platforms") or PLATFORMS) & set(PLATFORMS)) == 1)
    ratio = round(single / len(states), 2) if states else 0.0
    ways = {p: Ways(graph, p) for p in PLATFORMS}
    by_platform = {p: [w for w in walks if w["platform"] == p] for p in PLATFORMS}
    decisions = bc_run.read_json(run / DECISIONS_FILE) or {}
    shown_together = {tuple(sorted(p)) for w in planned for p in w["compatible_pairs"]}
    summary = {
        "cases_read": len({c for b in batch_list for c in b.get("case_ids", [])}),
        "screens": len(graph["screens"]),
        "screen_states": len(states),
        "walks": {p: len(by_platform[p]) for p in PLATFORMS},
        "conditions_to_prepare": sorted({c for w in walks for c in w["preconditions"]}),
        "external_steps": sum(1 for w in walks for s in w["steps"] if s["action"] == "external"),
        "names_merged": len(decisions.get("same") or []),
        "own_walk_states": {p: own_walk_states(ways[p], by_platform[p]) for p in PLATFORMS},
        "walks_used": log["used"],
        "single_platform_ratio": ratio,
        "split_recommended": ratio > SPLIT_RATIO,
    }
    lines = [
        f"Walks: " + ", ".join(f"{p} {n}" for p, n in summary["walks"].items())
        + f" — from {summary['cases_read']} cases, {summary['screens']} screens, {summary['screen_states']} Screen States",
        f"Walks: {len(summary['conditions_to_prepare'])} Conditions to prepare, {summary['external_steps']} external "
        f"steps, {summary['names_merged']} names merged",
        "Walks: " + ("the refiner's Walks, checked" if log["used"] == "refined" and any(
            a.get("failures") == [] for a in log.get("attempts", [])) else
            "plan's separate Walks" + (" (fallback: the refined Walks failed the check twice)"
                                       if len(log.get("attempts", [])) >= 2 else "")),
    ]
    for p in PLATFORMS:
        if summary["own_walk_states"][p]:
            lines.append(f"Walks: {p}: a Walk of their own for " + ", ".join(summary["own_walk_states"][p]))
    lines.append(f"Walks: {round(ratio * 100)}% of Screen States are on one Platform only"
                 + (f" — above {round(SPLIT_RATIO * 100)}%, so two plans (one per Platform) are recommended"
                    if summary["split_recommended"] else ""))

    conditions = {c["id"]: c.get("description", "") for c in graph.get("conditions", [])}
    md = ["# Walk Plan", "", *[f"- {line[len('Walks: '):]}" for line in lines], ""]
    for p in PLATFORMS:
        md += [f"## {p}", ""]
        for walk in by_platform[p]:
            md += [f"### {walk['id']}: {walk['title']}", "", f"Starts at `{walk['entry']}`.", ""]
            if walk["preconditions"]:
                md += ["Prepare first:", ""] + [f"- `{c}` — {conditions.get(c, '')}" for c in walk["preconditions"]] + [""]
            if walk["compatible_pairs"]:
                md += ["Conditions held together:", ""]
                md += [f"- `{a}` + `{b}` — " + ("shown together by the cases" if tuple(sorted((a, b))) in shown_together
                                                 else "judged compatible by the refiner")
                       for a, b in walk["compatible_pairs"]] + [""]
            md += ["| # | From | Do | To | Expect |", "|---|---|---|---|---|"]
            for n, s in enumerate(walk["steps"], 1):
                do = s["action"] + (f" `{s['component']}`" if s.get("component") else "") + (
                    f" “{s['input']}”" if s.get("input") else "") + (f": {s['instruction']}" if s.get("instruction") else "")
                md.append(f"| {n} | `{s['from_state']}` | {do} | `{s['to_state']}` | "
                          + ", ".join(f"`{l}`" for l in s["expect_labels"]) + " |")
            md.append("")
    if batches:
        md += ["## Batches", ""] + [f"- {b['id']} ({b.get('folder', '')}): {cases_text(b.get('case_ids', []))}"
                                    for b in batch_list] + [""]
    bc_run.write_private(run / WALKS_MD, "\n".join(md))
    return {"walks_md": str(run / WALKS_MD), "summary": summary, "lines": lines}



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
    plan = sub.add_parser("plan", help="plan the separate minimal Walks per Platform from graph.json")
    plan.add_argument("run", help="the run folder")
    check_walks_cmd = sub.add_parser("check", help="replay a Walks file against graph.json")
    check_walks_cmd.add_argument("run", help="the run folder")
    check_walks_cmd.add_argument("walks", help="the Walks file in the run folder, e.g. refined-1.json")
    render = sub.add_parser("render", help="write walks.md and print the summary")
    render.add_argument("run", help="the run folder")
    render.add_argument("--batches", action="store_true", help="list which cases were batched together")
    args = parser.parse_args(argv)
    try:
        if args.action == "check-cases":
            result = cmd_check_cases(args.run, args.folder)
        elif args.action == "batch":
            result = cmd_batch(args.run)
        elif args.action == "validate":
            result = cmd_validate(args.run, args.batch)
        elif args.action == "merge":
            result = cmd_merge(args.run, args.same)
        elif args.action == "plan":
            result = cmd_plan(args.run)
        elif args.action == "check":
            result = cmd_check(args.run, args.walks)
        else:
            result = cmd_render(args.run, args.batches)
    except (RunError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
