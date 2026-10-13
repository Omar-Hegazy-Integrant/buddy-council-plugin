#!/usr/bin/env python3
"""Run folders for Buddy-Council analysis commands. Standard library only; Python 3.9+.

A run folder, ``<project>/.buddy-council/runs/<run-id>/``, carries one analysis run's data from the collect
step to the analysis as files, so the data itself never passes through a model:

  requirements.json  written by the Jama server (``save_to``) or the Excel parser
  test_cases.json    written by the TestRail server (``save_to``)
  docs/, docs.json   GitHub docs the requirements link to (``enrich``, gh CLI strategy)
  links.json         requirement <-> test case links, untested, orphans, stranded links (``link``)
  bundles/*.md       one feature at a time, verbatim text, each part small enough for one read (``link``)
  manifest.json      what was collected, when, and the counts

Subcommands, each printing one JSON object to stdout:

  new    --project DIR --command NAME [--scope TEXT]   create a run folder, prune old ones
  enrich --run DIR                                     fetch the linked GitHub docs with the gh CLI
  link   --run DIR                                     cross-link, write links.json and the bundles
  search --run DIR --query TEXT [--concept 'a|b|c']... [--feature TEXT] [--top N]
                                                       rank the run against an ask (BM25), write search/*.md
  show   --run DIR --ids 'ID, ID'                      print those requirements and test cases (Markdown)
  recent --project DIR [--max-age-minutes N]           the newest complete, verified run of the whole scope

Nothing here talks to Jama, TestRail, or Jira, and nothing is written outside the run folder. ``enrich``
reads GitHub only through ``gh api`` GET requests.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import datetime
import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

KEEP_RUNS = 5
MAX_RUN_AGE_SECONDS = 7 * 24 * 3600
PART_BYTES = 18_000  # a bundle part fits one file read in both runtimes (Copilot truncates at 20 KB)
MAX_DOC_CHARS = 50_000
GH_TIMEOUT_SECONDS = 30
GH_WORKERS = 6
MAX_LISTED = 200  # entries per list in links.json; the counts are always complete
MAX_PRINTED_FEATURES = 60
RUN_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9-]+")
NO_FEATURE = "No feature"
RETIRED = re.compile(r"deprecated|deferred", re.IGNORECASE)  # never reported untested (analyze-coverage)
MAX_FIELD_CHARS = 160  # a bundle shows this much of a custom field; long ones are mostly reference lists, and
                       # the full value stays searchable in requirements.json / test_cases.json
BOOKKEEPING = {"jama_id", "item_type", "feature_path", "folder_path", "sequence", "modified_date", "url"}
EMPTY_VALUES = {"", "n/a", "na", "-", "none", "nan", "null", "tbd"}
DEFAULT_REQUIREMENT_FIELD = "custom_jama_req_id"


class RunError(Exception):
    pass


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def write_private(path: Path, text: str) -> None:
    """Write a file atomically, readable only by the user."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path: Path, payload: Any) -> None:
    write_private(path, json.dumps(payload, ensure_ascii=False, indent=1))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise RunError(f"{path.name} is not valid JSON ({exc}) — re-run the collect step.") from None


def run_folder(text: str) -> Path:
    """An existing run folder: <project>/.buddy-council/runs/<run-id>."""
    path = Path(text).expanduser().resolve()
    if not (path.is_dir() and path.parent.name == "runs" and path.parent.parent.name == ".buddy-council"):
        raise RunError(f"{text} is not a run folder (<project>/.buddy-council/runs/<run-id>).")
    return path


def update_manifest(run: Path, **changes: Any) -> None:
    manifest = read_json(run / "manifest.json") or {}
    manifest.update(changes)
    write_json(run / "manifest.json", manifest)


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------


def cmd_new(project_text: str, command: str, scope: str, keep: int = KEEP_RUNS) -> dict[str, Any]:
    project = Path(project_text).expanduser().resolve()
    config_dir = project / ".buddy-council"
    if not config_dir.is_dir():
        raise RunError(f"{project} has no .buddy-council folder — run /bc:setup in this project first.")
    runs = config_dir / "runs"
    runs.mkdir(mode=0o700, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")[:32] or "run"
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
    run, n = runs / base, 1
    while run.exists():
        n += 1
        run = runs / f"{base}-{n}"
    run.mkdir(mode=0o700)
    pruned = prune(runs, keep=keep, current=run.name)
    write_json(
        run / "manifest.json",
        {"run_id": run.name, "command": command, "scope": scope, "project": str(project), "created_at": utc_now()},
    )
    return {
        "run_dir": str(run),
        "requirements": str(run / "requirements.json"),
        "test_cases": str(run / "test_cases.json"),
        "pruned": pruned,
    }


def prune(runs: Path, keep: int, current: str) -> int:
    """Remove all but the newest ``keep`` runs, and any run older than a week. Touches run folders only."""
    folders = sorted(
        (p for p in runs.iterdir() if RUN_ID.fullmatch(p.name) and p.is_dir() and not p.is_symlink()),
        key=lambda p: p.name,
        reverse=True,
    )
    now, kept, removed = time.time(), 0, 0
    for folder in folders:
        if folder.name == current:
            kept += 1
            continue
        if kept >= keep or now - folder.stat().st_mtime > MAX_RUN_AGE_SECONDS:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
        else:
            kept += 1
    return removed


# ---------------------------------------------------------------------------
# enrich — GitHub docs through the gh CLI (the "cli" enrichment strategy)
# ---------------------------------------------------------------------------


def parse_github_url(url: str) -> dict[str, Any]:
    """A github.com blob/tree URL, or a raw.githubusercontent.com one, as {owner, repo, path, ref, anchor, kind}."""
    parsed = urllib.parse.urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
    if parsed.scheme != "https":
        raise RunError("unsupported_url: not an https URL")
    if host == "raw.githubusercontent.com" and len(parts) >= 4:
        owner, repo, ref, rest, kind = parts[0], parts[1], parts[2], parts[3:], "blob"
    elif host == "github.com" and len(parts) >= 4 and parts[2] in ("blob", "tree"):
        owner, repo, kind, ref, rest = parts[0], parts[1], parts[2], parts[3], parts[4:]
    else:
        raise RunError("unsupported_url: not a github.com blob or tree URL")
    if kind == "blob" and not rest:
        raise RunError("unsupported_url: no file path in the URL")
    for piece in [owner, repo, ref, *rest]:
        if piece in (".", "..") or any(c in piece for c in "\n\r?#"):
            raise RunError("unsupported_url: malformed path in the URL")
    return {"owner": owner, "repo": repo, "path": "/".join(rest), "ref": ref, "anchor": parsed.fragment or None, "kind": kind}


def gh_api(endpoint: str, runner: Any = subprocess.run) -> Any:
    """GET one GitHub API endpoint through gh. Raises RunError("<kind>: <detail>") on failure."""
    try:
        done = runner(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", endpoint],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise RunError("gh_missing: the gh CLI is not installed — install it, or switch enrichment to mcp in /bc:setup") from None
    except subprocess.TimeoutExpired:
        raise RunError(f"network: gh api timed out after {GH_TIMEOUT_SECONDS} s") from None
    if done.returncode != 0:
        text = " ".join((done.stderr or done.stdout or "").split())[-300:]
        lowered = text.lower()
        if "rate limit" in lowered:
            raise RunError(f"rate_limit: {text}")
        if "http 404" in lowered or "not found" in lowered:
            raise RunError(f"not_found: {text}")
        if "http 401" in lowered or "http 403" in lowered or "gh auth login" in lowered:
            raise RunError(f"auth_failed: {text}")
        raise RunError(f"unknown: {text or 'gh api failed'}")
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise RunError("unknown: gh returned something other than JSON") from None


def contents_endpoint(locator: dict[str, Any], path: str) -> str:
    quoted = urllib.parse.quote(path, safe="/")
    ref = urllib.parse.quote(locator["ref"], safe="")
    owner = urllib.parse.quote(locator["owner"], safe="")
    repo = urllib.parse.quote(locator["repo"], safe="")
    return f"repos/{owner}/{repo}/contents/{quoted}?ref={ref}"


def fetch_file(locator: dict[str, Any], path: str, runner: Any) -> str:
    body = gh_api(contents_endpoint(locator, path), runner)
    if isinstance(body, list):
        raise RunError("is_directory: the URL points at a folder")
    content = body.get("content") or ""
    if not content and body.get("sha") and (body.get("size") or 0) > 0:  # files over ~1 MB come back empty
        owner, repo = (urllib.parse.quote(locator[k], safe="") for k in ("owner", "repo"))
        sha = urllib.parse.quote(str(body["sha"]), safe="")
        content = gh_api(f"repos/{owner}/{repo}/git/blobs/{sha}", runner).get("content") or ""
    return base64.b64decode(content).decode("utf-8", errors="replace")


def fetch_directory(locator: dict[str, Any], runner: Any) -> tuple[str, str | None]:
    entries = gh_api(contents_endpoint(locator, locator["path"]), runner)
    if not isinstance(entries, list):
        raise RunError("unknown: expected a folder listing")
    readme = next((e for e in entries if str(e.get("name", "")).lower() == "readme.md"), None)
    if readme:
        return fetch_file(locator, str(readme["path"]), runner), None
    markdown = [e for e in entries if e.get("type") == "file" and str(e.get("name", "")).lower().endswith(".md")]
    if not markdown:
        raise RunError("directory_no_markdown: the folder has no README.md or markdown files")
    chosen = markdown[:5]
    content = "\n\n---\n\n".join(fetch_file(locator, str(e["path"]), runner) for e in chosen)
    omitted = len(markdown) - len(chosen)
    return content, (f"{omitted} more markdown files in the folder were not fetched" if omitted else None)


def fetch_doc(url: str, runner: Any = subprocess.run) -> dict[str, Any]:
    """One doc's content, as the gh CLI provider fetches it: a file, or a folder's README or first *.md files."""
    locator = parse_github_url(url)
    try:
        if locator["kind"] == "tree":
            content, note = fetch_directory(locator, runner)
        else:
            content, note = fetch_file(locator, locator["path"], runner), None
    except RunError as exc:
        text = str(exc)
        if text.startswith("is_directory"):
            content, note = fetch_directory(locator, runner)
        elif text.startswith("not_found"):
            owner, repo = (urllib.parse.quote(locator[k], safe="") for k in ("owner", "repo"))
            try:
                gh_api(f"repos/{owner}/{repo}", runner)
            except RunError:
                raise RunError(f"repo_inaccessible: {locator['owner']}/{locator['repo']} is private to you or does not exist") from None
            raise RunError(f"file_not_found: {locator['path']} at {locator['ref']}") from None
        else:
            raise
    return {"locator": {k: locator[k] for k in ("owner", "repo", "path", "ref", "anchor")}, "content": content, "note": note}


IMAGE_MD = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
IMAGE_HTML = re.compile(r"<img\b[^>]*?src=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
ALT_HTML = re.compile(r"alt=[\"']([^\"']*)[\"']", re.IGNORECASE)


def referenced_images(content: str, locator: dict[str, Any]) -> list[dict[str, str]]:
    targets = [(m.group(1), m.group(2)) for m in IMAGE_MD.finditer(content)]
    for m in IMAGE_HTML.finditer(content):
        alt = ALT_HTML.search(m.group(0))
        targets.append((alt.group(1) if alt else "", m.group(1)))
    base_dir = posixpath.dirname(locator["path"])
    found = []
    for alt, target in targets:
        if re.match(r"https?://", target):
            url = target
        else:
            resolved = posixpath.normpath(posixpath.join(base_dir, target.split("#")[0]))
            while resolved.startswith("../") or resolved == "..":
                resolved = resolved[3:]
            resolved = resolved.lstrip("/")
            url = f"https://raw.githubusercontent.com/{locator['owner']}/{locator['repo']}/{locator['ref']}/{resolved}"
        found.append({"alt": alt, "url": url})
    return found


def split_parts(text: str, limit: int = PART_BYTES) -> list[str]:
    """Text cut at line boundaries into pieces of at most ``limit`` UTF-8 bytes (a giant line is cut too)."""
    pieces: list[str] = []
    for line in text.splitlines(keepends=True):
        while len(line.encode("utf-8")) > limit:
            cut = limit // 4  # 4 bytes is the most a character takes, so this always fits
            pieces.append(line[:cut])
            line = line[cut:]
        pieces.append(line)
    parts: list[str] = []
    current, size = "", 0
    for piece in pieces:
        weight = len(piece.encode("utf-8"))
        if current and size + weight > limit:
            parts.append(current)
            current, size = "", 0
        current += piece
        size += weight
    if current or not parts:
        parts.append(current)
    return parts


def doc_slug(locator: dict[str, Any]) -> str:
    name = posixpath.splitext(posixpath.basename(locator["path"].rstrip("/")))[0] or locator["repo"]
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "doc"


def requirement_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get("requirements")
    return [r for r in data or [] if isinstance(r, dict)]


def cmd_enrich(run_text: str, runner: Any = subprocess.run) -> dict[str, Any]:
    run = run_folder(run_text)
    config = ((read_json(run.parent.parent / "sources.json") or {}).get("requirements") or {}).get("enrichment") or {}
    urls: dict[str, list[str]] = {}
    for requirement in requirement_list(read_json(run / "requirements.json")):
        for entry in requirement.get("_enrichment_urls") or []:
            url = str((entry or {}).get("url") or "").strip()
            if url:
                url = urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(query=""))
                urls.setdefault(url, []).append(str(requirement.get("id") or ""))
    if not config.get("enabled"):
        result: dict[str, Any] = {"skipped": "enrichment is disabled in .buddy-council/sources.json", "urls": len(urls)}
        update_manifest(run, enrichment=result)
        return result
    if str(config.get("strategy") or "cli") != "cli":
        pending = [{"url": url, "requirement_ids": ids} for url, ids in urls.items()]
        write_json(run / "docs.json", {"strategy": "mcp", "docs": [], "failed": [], "pending": pending})
        update_manifest(run, enrichment={"urls": len(urls), "pending": len(urls)})
        return {"pending": pending, "line": f"Enrichment: {len(urls)} GitHub-linked docs left for the GitHub MCP tools (mcp strategy)"}

    limit = int(config.get("max_doc_chars") or MAX_DOC_CHARS)
    (run / "docs").mkdir(mode=0o700, exist_ok=True)

    def one(url: str) -> tuple[str, dict[str, Any]]:
        try:
            return url, fetch_doc(url, runner)
        except RunError as exc:
            kind, _, message = str(exc).partition(": ")
            return url, {"error": kind, "message": message or kind}

    with concurrent.futures.ThreadPoolExecutor(max_workers=GH_WORKERS) as pool:
        results = dict(pool.map(one, list(urls)))

    docs, failed, truncated = [], [], []
    for n, (url, ids) in enumerate(urls.items(), start=1):
        outcome = results[url]
        if "error" in outcome:
            failed.append({"url": url, "error": outcome["error"], "message": outcome["message"], "requirement_ids": ids})
            continue
        content = outcome["content"]
        raw_size = len(content)
        if raw_size > limit:
            content = content[:limit] + f"\n\n[...truncated, see {url} for full document]"
            truncated.append(url)
        chunks = split_parts(content, PART_BYTES - 400)
        files = []
        for p, chunk in enumerate(chunks, start=1):
            name = f"docs/{n:02d}-{doc_slug(outcome['locator'])}-{p}.md"
            write_private(run / name, f"<!-- {url} — part {p} of {len(chunks)} -->\n{chunk}")
            files.append(name)
        entry = {
            "url": url,
            "files": files,
            "locator": outcome["locator"],
            "fetched_at": utc_now(),
            "raw_size": raw_size,
            "truncated": raw_size > limit,
            "referenced_images": referenced_images(outcome["content"], outcome["locator"]),
            "requirement_ids": ids,
        }
        if outcome.get("note"):
            entry["note"] = outcome["note"]
        docs.append(entry)
    write_json(run / "docs.json", {"strategy": "cli", "docs": docs, "failed": failed, "pending": []})
    line = f"Enrichment: fetched {len(docs)} of {len(urls)} GitHub-linked requirement docs"
    if truncated:
        line += f" ({len(truncated)} truncated at {limit} characters)"
    update_manifest(run, enrichment={"urls": len(urls), "fetched": len(docs), "failed": len(failed), "truncated": len(truncated)})
    return {"line": line, "urls": len(urls), "fetched": len(docs), "failed": failed, "truncated": truncated}


# ---------------------------------------------------------------------------
# link
# ---------------------------------------------------------------------------


def key(value: Any) -> str:
    return str(value or "").strip().casefold()


def tokens(value: Any) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,;\n]", str(value or ""))
    return [t for t in (str(i).strip() for i in items) if t]


def natural(text: Any) -> tuple:
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p.casefold()) for p in re.split(r"(\d+)", str(text)) if p)


def case_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get("test_cases")
    return [c for c in data or [] if isinstance(c, dict)]


def scope_of(requirements_data: Any, manifest: dict[str, Any]) -> tuple[str, str]:
    """("all" or "scoped", the scope text) — from the requirements file, else the run's manifest."""
    scope = (requirements_data.get("scope") or {}) if isinstance(requirements_data, dict) else {}
    given = scope.get("input")
    text = str(given if given is not None else manifest.get("scope") or "").strip()
    kind = scope.get("kind") or ("all" if text.casefold() in ("", "all") else "scoped")
    return ("all" if kind == "all" else "scoped"), text


def words(text: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").casefold()))


def folder_match(case: dict[str, Any], folder_names: dict[str, str]) -> str | None:
    """The feature whose name a folder in the case's path contains as whole words — the longest such name."""
    path = str((case.get("raw_fields") or {}).get("section_path") or case.get("feature") or "")
    best = ""
    for segment in path.split("/"):
        tokens = words(segment).split()
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens) + 1):
                phrase = " ".join(tokens[i:j])
                if phrase in folder_names and len(phrase) > len(best):
                    best = phrase
    return folder_names[best] if best else None


def cmd_link(run_text: str) -> dict[str, Any]:
    run = run_folder(run_text)
    manifest = read_json(run / "manifest.json") or {}
    requirements_data = read_json(run / "requirements.json")
    cases_data = read_json(run / "test_cases.json")
    docs_data = read_json(run / "docs.json") or {}
    if requirements_data is None:
        raise RunError("requirements.json is missing — the requirements fetch did not complete.")
    warnings: list[str] = []
    if cases_data is None:
        warnings.append("test_cases.json is missing — the test case fetch did not complete, so coverage cannot be measured.")
    requirements = requirement_list(requirements_data)
    cases = case_list(cases_data)
    mode, scope_text = scope_of(requirements_data, manifest)

    by_id: dict[str, dict[str, Any]] = {}
    features: list[str] = []
    for requirement in requirements:
        requirement["feature"] = str(requirement.get("feature") or "").strip() or NO_FEATURE
        rid = key(requirement.get("id"))
        if rid and rid not in by_id:
            by_id[rid] = requirement
            if requirement["feature"] not in features:
                features.append(requirement["feature"])
    if len(by_id) != len(requirements):
        warnings.append(f"{len(requirements) - len(by_id)} requirements repeat an ID already seen, or have none; the first was kept.")

    # Folder names that place an unlinked case in a feature: the features themselves, plus — for a scope
    # narrower than one feature, such as a sub-folder — the scope's own last segment.
    folder_names = {words(f): f for f in features if f != NO_FEATURE and words(f)}
    if mode == "scoped" and scope_text:
        last = scope_text.split("/")[-1]
        if words(last) and key(last) not in by_id and words(last) not in folder_names and len(features) == 1:
            folder_names[words(last)] = features[0]

    tested_by: dict[str, list[str]] = {rid: [] for rid in by_id}
    in_scope: list[dict[str, Any]] = []
    case_feature: dict[str, str] = {}
    unknown: dict[str, int] = {}
    for case in cases:
        cid = str(case.get("id") or "")
        links = [key(t) for t in tokens(case.get("linked_ids"))]
        known = [rid for rid in dict.fromkeys(links) if rid in by_id]
        folder_feature = folder_match(case, folder_names)
        if mode == "scoped" and not known and not folder_feature:
            continue
        in_scope.append(case)
        for rid in known:
            tested_by[rid].append(cid)
        if mode == "all":
            for rid in links:
                if rid not in by_id:
                    unknown[rid] = unknown.get(rid, 0) + 1
        case_feature[cid] = by_id[known[0]]["feature"] if known else (folder_feature or NO_FEATURE)

    orphans, stranded = [], []
    for case in in_scope:
        if tokens(case.get("linked_ids")):
            continue
        raw = case.get("raw_fields") or {}
        entry: dict[str, Any] = {
            "id": case.get("id"),
            "title": case.get("title"),
            "section": raw.get("section_path") or case.get("feature") or "",
            "feature": case_feature.get(str(case.get("id") or ""), NO_FEATURE),
            "refs": raw.get("refs") or "",
        }
        in_refs = list(dict.fromkeys(by_id[key(t)]["id"] for t in tokens(raw.get("refs")) if key(t) in by_id))
        if in_refs:
            entry["stranded_ids"] = in_refs
            stranded.append(entry)
        orphans.append(entry)
    stranded_ids = {key(i) for entry in stranded for i in entry["stranded_ids"]}

    for rid, requirement in by_id.items():
        requirement["linked_ids"] = sorted(dict.fromkeys(tested_by[rid]), key=natural)
    retired = {rid for rid, r in by_id.items() if RETIRED.search(str(r.get("status") or ""))}
    untested = []
    for rid, requirement in by_id.items():
        if not tested_by[rid] and rid not in retired:
            row = {k: requirement.get(k) or "" for k in ("id", "title", "feature", "status")}
            if rid in stranded_ids:
                row["stranded"] = True
            untested.append(row)

    docs_by_req: dict[str, list[dict[str, Any]]] = {}
    for doc in docs_data.get("docs") or []:
        for rid in doc.get("requirement_ids") or []:
            docs_by_req.setdefault(key(rid), []).append(doc)
    missing_by_req: dict[str, list[dict[str, Any]]] = {}
    for doc in (docs_data.get("failed") or []) + (docs_data.get("pending") or []):
        for rid in doc.get("requirement_ids") or []:
            missing_by_req.setdefault(key(rid), []).append(doc)

    config = read_json(run.parent.parent / "sources.json") or {}
    mismatches = verify_run(config, manifest, requirements_data, cases_data)
    lines, missing, enrichment = report_lines(config, requirements_data, requirements, cases_data, cases, docs_data)

    bundles = run / "bundles"
    bundles.mkdir(mode=0o700, exist_ok=True)
    for old in bundles.glob("*.md"):
        old.unlink()
    cases_by_id = {str(c.get("id") or ""): c for c in in_scope}
    rows: list[dict[str, Any]] = []
    groups = [(f, [r for r in by_id.values() if r["feature"] == f], [o for o in orphans if o["feature"] == f]) for f in features]
    leftover = [o for o in orphans if o["feature"] not in features]
    if leftover:
        groups.append((NO_FEATURE, [], leftover))
    for n, (feature, members, feature_orphans) in enumerate(groups, start=1):
        files = write_bundle(run, n, len(groups), feature, members, cases_by_id, feature_orphans, docs_by_req, missing_by_req)
        active = [r for r in members if key(r.get("id")) not in retired]
        tested = sum(1 for r in active if r["linked_ids"])
        rows.append(
            {
                "feature": feature,
                "requirements": len(members),
                "tested": tested,
                "untested": len(active) - tested,
                "retired": len(members) - len(active),
                "test_cases": len({c for r in members for c in r["linked_ids"]} | {o["id"] for o in feature_orphans}),
                "orphans": len(feature_orphans),
                "coverage_pct": round(100.0 * tested / len(active), 1) if active else None,
                "bundles": files,
                "docs": feature_docs(members, docs_by_req),
            }
        )

    active_total = len(by_id) - len(retired)
    tested_total = sum(1 for rid in by_id if tested_by[rid] and rid not in retired)
    counts = {
        "requirements": len(by_id),
        "test_cases_in_scope": len(in_scope),
        "test_cases_read": len(cases),
        "tested": tested_total,
        "untested": active_total - tested_total,
        "retired": len(retired),
        "coverage_pct": round(100.0 * tested_total / active_total, 1) if active_total else None,
        "orphans": len(orphans),
        "stranded": len(stranded),
        "unknown_linked_ids": len(unknown),
        "features": len(features),
    }
    readiness = f'Readiness: {len(by_id)} requirements, {len(in_scope)} test cases for scope "{scope_text or "all"}"'
    lines.append(readiness)
    if unknown:
        sample = ", ".join(sorted(unknown, key=natural)[:5])
        warnings.append(
            f"{len(unknown)} requirement IDs that test cases link to are not among the fetched requirements "
            f"(e.g. {sample}) — outside the requirements root, or no longer in the source."
        )
    links = {
        "kind": "links",
        "linked_at": utc_now(),
        "scope": {"mode": mode, "input": scope_text},
        "readiness": readiness,
        "lines": lines,
        "missing": missing,
        "mismatches": mismatches,
        "enrichment": enrichment,
        "counts": counts,
        "features": rows,
        "untested": untested[:MAX_LISTED],
        "orphans": orphans[:MAX_LISTED],
        "stranded": stranded[:MAX_LISTED],
        "unknown_linked_ids": [
            {"id": i, "cases": c} for i, c in sorted(unknown.items(), key=lambda kv: (-kv[1], natural(kv[0])))
        ][:MAX_LISTED],
        "by_requirement": {r.get("id"): r["linked_ids"] for r in by_id.values() if r["linked_ids"]},
        "lists_truncated": any(len(x) > MAX_LISTED for x in (untested, orphans, stranded, unknown)),
        "warnings": warnings,
    }
    write_json(run / "links.json", links)
    update_manifest(run, linked_at=links["linked_at"], readiness=readiness, counts=counts, lines=lines, missing=missing, mismatches=mismatches)
    printed = [
        {"feature": r["feature"], "requirements": r["requirements"], "untested": r["untested"], "orphans": r["orphans"], "parts": len(r["bundles"])}
        for r in rows[:MAX_PRINTED_FEATURES]
    ]
    return {
        "run_dir": str(run),
        "readiness": readiness,
        "lines": lines,
        "missing": missing,
        "mismatches": mismatches,
        "counts": counts,
        "features": printed,
        "more_features": max(0, len(rows) - MAX_PRINTED_FEATURES),
        "links": str(run / "links.json"),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Bundles — Markdown a model reads one part at a time; text verbatim
# ---------------------------------------------------------------------------


def one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def title_of(item: dict[str, Any]) -> str:
    """"ID — title", or just the ID when there is no title."""
    title = one_line(item.get("title"))
    return f"{item.get('id')} — {title}" if title else str(item.get("id"))


def shown_value(value: Any) -> str | None:
    """A field value on one line, or None when it says nothing (empty, N/A, an unticked box)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else None
    if isinstance(value, list):
        items = [v for v in (shown_value(item) for item in value) if v]
        return ", ".join(items) or None
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else one_line(value)
    if text.casefold() in EMPTY_VALUES:
        return None
    if len(text) > MAX_FIELD_CHARS:
        text = f"{text[:MAX_FIELD_CHARS]}… [{len(text) - MAX_FIELD_CHARS} more characters — search the run's JSON for the ID]"
    return text


def field_lines(fields: dict[str, Any], skip: set[str]) -> list[str]:
    lines = []
    for label, value in fields.items():
        text = None if label in skip else shown_value(value)
        if text:
            lines.append(f"- {label}: {text}")
    return ["Fields:", *lines] if lines else []


def feature_docs(members: list[dict[str, Any]], docs_by_req: dict[str, list[dict[str, Any]]]) -> list[str]:
    files: list[str] = []
    for requirement in members:
        for doc in docs_by_req.get(key(requirement.get("id")), []):
            files.extend(f for f in doc.get("files") or [] if f not in files)
    return files


def requirement_body(
    requirement: dict[str, Any],
    docs_by_req: dict[str, list[dict[str, Any]]],
    missing_by_req: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """A requirement's facts, text, custom fields, and linked docs — everything but its heading and test cases."""
    raw = requirement.get("raw_fields") or {}
    facts = [f"Status: {requirement.get('status') or '—'}"]
    if raw.get("item_type"):
        facts.append(f"Type: {raw['item_type']}")
    location = raw.get("folder_path") or raw.get("feature_path")
    if location:
        facts.append(f"Folder: {location}")
    lines = [" · ".join(facts), ""]
    lines += ["Description:", str(requirement.get("description") or "(empty)").strip(), ""]
    if str(requirement.get("rationale") or "").strip():
        lines += ["Rationale:", str(requirement["rationale"]).strip(), ""]
    extra = field_lines(raw, BOOKKEEPING)
    if extra:
        lines += extra + [""]
    for doc in docs_by_req.get(key(requirement.get("id")), []):
        files = ", ".join(doc.get("files") or [])
        lines.append(f"Linked doc: {doc['url']} → {files}{' (truncated)' if doc.get('truncated') else ''}")
    for doc in missing_by_req.get(key(requirement.get("id")), []):
        lines.append(f"Linked doc not in this run: {doc['url']} ({doc.get('error') or 'left for the main thread'})")
    return lines


def requirement_block(
    requirement: dict[str, Any],
    docs_by_req: dict[str, list[dict[str, Any]]],
    missing_by_req: dict[str, list[dict[str, Any]]],
) -> str:
    lines = [f"## {title_of(requirement)}"]
    lines += requirement_body(requirement, docs_by_req, missing_by_req)
    linked = requirement["linked_ids"]
    lines.append(f"Test cases: {', '.join(linked)}" if linked else "Test cases: none — UNTESTED")
    return "\n".join(lines).rstrip() + "\n\n"


def case_block(case: dict[str, Any], extra: str = "") -> str:
    raw = case.get("raw_fields") or {}
    facts = []
    if raw.get("section_path") or case.get("feature"):
        facts.append(f"Folder: {raw.get('section_path') or case.get('feature')}")
    links = tokens(case.get("linked_ids"))
    facts.append(f"Links: {', '.join(links) if links else 'none'}")
    if raw.get("refs"):
        facts.append(f"References: {raw['refs']}")
    for label in ("priority", "type"):
        if isinstance(raw.get(label), str) and raw[label]:
            facts.append(f"{label.capitalize()}: {raw[label]}")
    lines = [f"### {title_of(case)}", " · ".join(facts)]
    if extra:
        lines.append(extra)
    lines += field_lines(raw.get("custom") or {}, set())
    if str(case.get("description") or "").strip():
        lines += ["", str(case["description"]).strip()]
    steps = raw.get("steps") or []
    if steps:
        lines += ["", "Steps:"]
        for i, step in enumerate(steps, start=1):
            lines.append(f"{i}. {str(step.get('content') or '').strip()}")
            if str(step.get("expected") or "").strip():
                lines.append(f"   Expected: {str(step['expected']).strip()}")
    return "\n".join(lines).rstrip() + "\n\n"


def pack_blocks(blocks: list[tuple[str, str]]) -> list[str]:
    """Blocks packed into parts of at most PART_BYTES; a part that starts mid-section opens with its restart header."""
    parts: list[str] = []
    current, size = "", 0
    for text, restart in blocks:
        weight = len(text.encode("utf-8"))
        if current and size + weight > PART_BYTES:
            parts.append(current)
            current, size = restart, len(restart.encode("utf-8"))
        current += text
        size += weight
    if current or not parts:
        parts.append(current)
    return parts


def write_bundle(
    run: Path,
    n: int,
    total: int,
    feature: str,
    members: list[dict[str, Any]],
    cases_by_id: dict[str, dict[str, Any]],
    feature_orphans: list[dict[str, Any]],
    docs_by_req: dict[str, list[dict[str, Any]]],
    missing_by_req: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Each requirement followed by the cases that test it (a case shown once), then the feature's orphans."""
    blocks: list[tuple[str, str]] = []  # (text, header that restarts its section in a new part)
    shown: dict[str, str] = {}
    for requirement in members:
        blocks.append((requirement_block(requirement, docs_by_req, missing_by_req), ""))
        restart = f"## {title_of(requirement)} (continued)\n\n"
        for cid in requirement["linked_ids"]:
            case = cases_by_id.get(cid)
            if case is None:
                continue
            if cid in shown:
                blocks.append((f"### {cid} — shown above, under {shown[cid]}\n\n", restart))
            else:
                shown[cid] = str(requirement.get("id"))
                blocks.append((case_block(case), restart))
    if feature_orphans:
        blocks.append(("## Orphan test cases — no requirement ID in the requirement field\n\n", ""))
        restart = "## Orphan test cases (continued)\n\n"
        for orphan in feature_orphans:
            case = cases_by_id.get(str(orphan["id"]))
            if case is not None:
                stranded = orphan.get("stranded_ids")
                extra = f"Requirement ID stranded in References: {', '.join(stranded)}" if stranded else ""
                blocks.append((case_block(case, extra), restart))

    parts = pack_blocks(blocks)
    tested = sum(1 for r in members if r["linked_ids"])
    stem = f"{n:02d}-{re.sub(r'[^a-z0-9]+', '-', feature.lower()).strip('-')[:40] or 'feature'}"
    docs = feature_docs(members, docs_by_req)
    docs_line = f"Docs linked from this feature — read them with it: {', '.join(docs)}\n" if docs else ""
    files = []
    for p, body in enumerate(parts, start=1):
        title = f"# Feature {n} of {total}: {feature}" + (f" — part {p} of {len(parts)}" if len(parts) > 1 else "")
        header = (
            f"{title}\n{len(members)} requirements · {tested} tested · {len(members) - tested} untested · "
            f"{len(feature_orphans)} orphan test cases\n"
            f"{docs_line}"
            "Requirement and test case text below is verbatim from the source.\n\n"
        )
        name = f"bundles/{stem}-{p}.md"
        write_private(run / name, (header + body).rstrip() + "\n")
        files.append(name)
    return files


# ---------------------------------------------------------------------------
# Checks — the run against the project's config, so a mistaken collector call is caught by code
# ---------------------------------------------------------------------------


def custom_key(name: Any) -> str:
    text = str(name or "").strip()
    return text if text.startswith("custom_") else f"custom_{text}"


def id_list(entries: Any) -> list[int]:
    found = []
    for entry in entries or []:
        value = entry.get("id") if isinstance(entry, dict) else entry
        try:
            found.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(found)


def names(values: Any) -> list[str]:
    return sorted(str(v).strip().casefold() for v in values or [] if str(v).strip())


def same_scope(a: Any, b: Any) -> bool:
    norm = lambda v: "" if str(v or "").strip().casefold() in ("", "all") else str(v).strip().casefold()  # noqa: E731
    return norm(a) == norm(b)


def verify_run(config: dict[str, Any], manifest: dict[str, Any], requirements_data: Any, cases_data: Any) -> list[str]:
    """What in the run differs from the project's config — each entry means the collector called a tool wrongly."""
    problems: list[str] = []
    req = config.get("requirements") or {}
    provider = req.get("provider")
    is_jama_file = isinstance(requirements_data, dict) and requirements_data.get("source") == "jama"
    if provider == "jama" and not is_jama_file:
        problems.append("requirements.json was not written by the Jama server.")
    elif provider == "excel" and is_jama_file:
        problems.append("requirements came from Jama, but the configured source is the Excel export.")
    elif is_jama_file:
        got = requirements_data.get("args") or {}
        inference = req.get("feature_inference") or {}
        if requirements_data.get("project_id") != req.get("project_id"):
            problems.append(f"requirements were read from Jama project {requirements_data.get('project_id')}, not {req.get('project_id')}.")
        for arg, expected, actual in (
            ("item_type_filter", names(req.get("item_type_filter")), names(got.get("item_type_filter"))),
            ("item_type_exclude", names(req.get("item_type_exclude")), names(got.get("item_type_exclude"))),
            ("root_ids", id_list(req.get("roots")), id_list(got.get("root_ids"))),
            ("exclude_ids", id_list(req.get("exclude")), id_list(got.get("exclude_ids"))),
        ):
            if expected != actual:
                problems.append(f"requirements were read with {arg}={actual or 'none'}, but the config says {expected or 'none'}.")
        if req.get("roots") and int(got.get("feature_depth") or 1) != int(inference.get("depth") or 1):
            problems.append(f"requirements were read with feature_depth={got.get('feature_depth')}, but the config says {inference.get('depth') or 1}.")
        if str(got.get("feature_item_type") or "Folder").casefold() != str(inference.get("folder_item_type") or "Folder").casefold():
            problems.append(f"requirements were read with feature_item_type={got.get('feature_item_type')}, but the config says {inference.get('folder_item_type')}.")
        wanted = {k: v for k, v in (req.get("field_mapping") or {}).items() if v}
        used = {k: v for k, v in (got.get("field_mapping") or {}).items() if v}
        if wanted != used:
            problems.append(f"requirements were read with field_mapping={used or 'none'}, but the config says {wanted}.")
        scope = (requirements_data.get("scope") or {}).get("input")
        if not same_scope(scope, manifest.get("scope")):
            problems.append(f"requirements were read for scope {scope!r}, but this run is for {manifest.get('scope')!r}.")
    tests = config.get("test_cases") or {}
    if not tests:
        return problems
    if isinstance(cases_data, dict):
        field = custom_key((tests.get("authoring") or {}).get("requirement_field") or DEFAULT_REQUIREMENT_FIELD)
        if cases_data.get("project_id") != tests.get("project_id") or (cases_data.get("suite_id") or None) != (tests.get("suite_id") or None):
            problems.append(
                f"test cases were read from TestRail project {cases_data.get('project_id')} suite {cases_data.get('suite_id')}, "
                f"not project {tests.get('project_id')} suite {tests.get('suite_id')}."
            )
        if cases_data.get("requirement_field") != field:
            problems.append(f"test cases were linked through {cases_data.get('requirement_field')}, but the config says {field}.")
        selection = cases_data.get("filter") or {}
        if selection.get("requirement_ids") or selection.get("section") or selection.get("unlinked"):
            problems.append("test cases were filtered; the collect step needs every case in the suite.")
    elif cases_data is not None:
        problems.append("test_cases.json was not written by the TestRail server.")
    return problems


def report_lines(
    config: dict[str, Any], requirements_data: Any, requirements: list[dict[str, Any]],
    cases_data: Any, cases: list[dict[str, Any]], docs_data: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    """The Fetch and Enrichment lines, from the files the servers and scripts wrote — not from any model."""
    lines: list[str] = []
    missing: list[str] = []
    if isinstance(requirements_data, dict) and requirements_data.get("source") == "jama":
        summary = requirements_data.get("summary") or ""
        lines.append(f"Fetch: requirements → {len(requirements)} from Jama project {requirements_data.get('project_id')} ({summary})")
    else:
        lines.append(f"Fetch: requirements → {len(requirements)} from the Excel export")
    if cases_data is None:
        lines.append("Fetch: test cases → FAILED — no test case file in the run")
        missing.append("test cases")
    else:
        suite = cases_data.get("suite_id") if isinstance(cases_data, dict) else None
        lines.append(f"Fetch: test cases → {len(cases)} read from TestRail suite {suite}")
    enrichment_config = (config.get("requirements") or {}).get("enrichment") or {}
    urls = {str(u.get("url")) for r in requirements for u in r.get("_enrichment_urls") or [] if isinstance(u, dict)}
    docs, failed, pending = docs_data.get("docs") or [], docs_data.get("failed") or [], docs_data.get("pending") or []
    if enrichment_config.get("enabled"):
        if not docs_data and urls:
            lines.append(f"Enrichment: not run — {len(urls)} GitHub-linked docs were not fetched")
            missing.append("enrichment")
        elif pending:
            lines.append(f"Enrichment: {len(pending)} GitHub-linked docs to fetch with the GitHub MCP tools (mcp strategy)")
        else:
            truncated = sum(1 for d in docs if d.get("truncated"))
            line = f"Enrichment: fetched {len(docs)} of {len(docs) + len(failed)} GitHub-linked requirement docs"
            lines.append(line + (f" ({truncated} truncated)" if truncated else ""))
            if failed and not docs:
                missing.append("enrichment")
    elif urls:
        lines.append("Note: the requirements source has GitHub doc links but enrichment is disabled. Run /bc:setup to enable.")
    enrichment = {
        "failed": [{k: f.get(k) for k in ("url", "error", "message")} for f in failed][:MAX_LISTED],
        "pending": len(pending),
    }
    return lines, missing, enrichment


# ---------------------------------------------------------------------------
# search — rank a run against an ask in code (BM25 over concepts the caller words), no model involved
# ---------------------------------------------------------------------------

SEARCH_TOP = 25  # requirements written out in full
SEARCH_NEXT = 25  # further candidates listed by ID and title
SEARCH_CASES = 10
SEARCH_DOCS = 5
MAX_SHOW_IDS = 20
MAX_CONCEPTS = 8
SNIPPET_CHARS = 220
RECENT_MINUTES = 30
K1, B = 1.2, 0.75
OTHER_WORDING = 0.8  # a concept's alternative wordings count a little less than the user's own words
COMMON = 0.5  # a word in more than half the requirements says nothing about where a topic lives
WORD = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")
STOPWORDS = frozenset(
    "a about above after again against all also am an and any are as at be because been before being below between "
    "both but by can could did do does doing during each either else few for from further had has have having he her "
    "here hers him his how i if in into is it its itself just me more most my neither no nor not now of on once only "
    "or other our ours per same she should so some such than that the their theirs them then there these they this "
    "those through thus to too until very via was we were what when where whether which while who whom whose why "
    "will with within without would yet you your shall must may might".split()
)


def stem(word: str) -> str:
    """A light English stemmer, so alarm/alarms, mute/muted/muting, and battery/batteries meet."""
    if len(word) <= 3 or not word.isalpha():
        return word
    if word.endswith(("ies", "ied")) and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    elif word.endswith("ed") and len(word) > 4 and not word.endswith("eed"):
        word = word[:-2]
    if word.endswith("e") and len(word) > 3 and not word.endswith("ee"):
        word = word[:-1]
    return word


def surface_words(text: Any) -> list[str]:
    """The words of a text as written (lower-cased), apostrophes dropped, hyphenated words split."""
    found = []
    for match in WORD.finditer(str(text or "").casefold().replace("\u2019", "'")):
        word = match.group()
        if word.endswith("'s"):
            word = word[:-2]
        found.extend(part for part in word.replace("'", "").split("-") if part)
    return found


def analyze(text: Any) -> tuple[list[str], list[str]]:
    """(stems in order, stopwords dropped; joined compounds) — "warm-up" also yields "warmup"."""
    found: list[str] = []
    joined: list[str] = []
    for match in WORD.finditer(str(text or "").casefold().replace("\u2019", "'")):
        word = match.group()
        if word.endswith("'s"):
            word = word[:-2]
        parts = [part for part in word.replace("'", "").split("-") if part]
        if len(parts) > 1:
            joined.append(stem("".join(parts)))
        found.extend(stem(part) for part in parts if part not in STOPWORDS)
    return found, joined


def flat_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, list):
        return " ".join(flat_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(flat_text(v) for v in value.values())
    return str(value)


class Record:
    """One searchable item, its fields as (weight, stems, counts), and its weighted length."""

    __slots__ = ("kind", "id", "item", "fields", "length", "texts")

    def __init__(self, kind: str, rid: str, item: Any, fields: list[tuple[float, Any]], texts: list[str] | None = None):
        self.kind, self.id, self.item, self.texts = kind, rid, item, texts or []
        self.fields: list[tuple[float, list[str], Counter]] = []
        self.length = 0.0
        for weight, text in fields:
            found, joined = analyze(text)
            if not found and not joined:
                continue
            counts = Counter(found)
            counts.update(joined)
            self.fields.append((weight, found, counts))
            self.length += weight * len(found)


def requirement_record(item: dict[str, Any]) -> Record:
    raw = item.get("raw_fields") or {}
    custom = " ".join(flat_text(value) for label, value in raw.items() if label not in BOOKKEEPING)
    location = raw.get("folder_path") or raw.get("feature_path") or item.get("feature") or ""
    fields = [
        (2.5, item.get("title")), (1.0, item.get("description")), (0.6, item.get("rationale")),
        (1.5, location), (0.4, custom),
    ]
    return Record("requirement", str(item.get("id") or ""), item, fields)


def case_record(item: dict[str, Any]) -> Record:
    raw = item.get("raw_fields") or {}
    steps: list[str] = []
    for step in raw.get("steps") or []:
        if isinstance(step, dict):
            steps.append(str(step.get("content") or ""))
            if str(step.get("expected") or "").strip():
                steps.append(f"Expected: {step['expected']}")
    custom = " ".join(flat_text(v) for v in (raw.get("custom") or {}).values())
    fields = [
        (2.5, item.get("title")), (1.0, item.get("description")), (1.0, "\n".join(steps)),
        (1.2, raw.get("section_path") or item.get("feature")), (0.3, custom),
    ]
    texts = [str(item.get("description") or ""), *steps]  # the title is shown anyway; the snippet adds what it doesn't say
    return Record("test_case", str(item.get("id") or ""), item, fields, texts)


def parse_concepts(concept_args: list[str], query: str) -> list[dict[str, Any]]:
    """Concepts from "--concept 'mute|silence|suppress'" — the first wording is the user's own — else the query's words."""
    raw_concepts = [c for c in concept_args or [] if str(c).strip()]
    if not raw_concepts:
        raw_concepts = list(dict.fromkeys(w for w in surface_words(query) if w not in STOPWORDS))
    concepts = []
    for raw in raw_concepts[:MAX_CONCEPTS]:
        alternatives = []
        for n, text in enumerate(t.strip() for t in str(raw).split("|")):
            found, joined = analyze(text)
            if not found and not joined:
                continue
            compound = joined[0] if len(joined) == 1 and " " not in text else None
            alternatives.append({"label": text, "terms": found, "joined": compound, "weight": 1.0 if n == 0 else OTHER_WORDING})
        if alternatives:
            concepts.append({"name": alternatives[0]["label"], "alternatives": alternatives})
    return concepts


def phrase_count(found: list[str], terms: list[str]) -> int:
    size = len(terms)
    if size == 1:
        return found.count(terms[0])
    first = terms[0]
    return sum(1 for i in range(len(found) - size + 1) if found[i] == first and found[i:i + size] == terms)


def near(found: list[str], terms: list[str]) -> bool:
    """Whether every term occurs within a few words of one occurrence of the first, in any order."""
    window = len(terms) + 2
    positions = {term: [i for i, word in enumerate(found) if word == term] for term in set(terms)}
    return any(all(any(abs(j - i) <= window for j in positions[t]) for t in terms[1:]) for i in positions[terms[0]])


def field_tf(found: list[str], counts: Counter, alternative: dict[str, Any]) -> float:
    """How often a field says an alternative: a word, a hyphenated word in any spelling, or a phrase.

    A phrase counts in full where its words appear in order, and half where they appear close together in
    another order ("warm-up of the sensor" for "sensor warm-up") — never for words scattered across a field.
    """
    terms, joined = alternative["terms"], alternative["joined"]
    if len(terms) == 1 and not joined:
        return float(counts.get(terms[0], 0))
    best = float(counts.get(joined, 0)) if joined else 0.0
    if terms and all(term in counts for term in terms):
        run = phrase_count(found, terms)
        best = max(best, float(run) if run else (0.5 if len(terms) > 1 and near(found, terms) else 0.0))
    return best


def rank(records: list[Record], concepts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Records scored by BM25, best wording per concept, scaled by the share of concepts matched.

    Returns (ranked hits, for each concept the wordings nothing matched).
    """
    unmatched: dict[str, list[str]] = {}
    if not records or not concepts:
        return [], unmatched
    total_docs = len(records)
    average = sum(r.length for r in records) / total_docs or 1.0
    per_concept: list[dict[int, tuple[float, str]]] = []
    for concept in concepts:
        scores: dict[int, tuple[float, str]] = {}
        for alternative in concept["alternatives"]:
            tfs = {}
            for i, record in enumerate(records):
                tf = sum(weight * field_tf(found, counts, alternative) for weight, found, counts in record.fields)
                if tf > 0:
                    tfs[i] = tf
            if not tfs:
                unmatched.setdefault(concept["name"], []).append(alternative["label"])
                continue
            idf = math.log(1 + (total_docs - len(tfs) + 0.5) / (len(tfs) + 0.5))
            for i, tf in tfs.items():
                norm = K1 * (1 - B + B * records[i].length / average)
                score = alternative["weight"] * idf * tf * (K1 + 1) / (tf + norm)
                if score > scores.get(i, (0.0, ""))[0]:
                    scores[i] = (score, alternative["label"])
        per_concept.append(scores)
    ranked = []
    for i, record in enumerate(records):
        hits = [(concept["name"], scores[i]) for concept, scores in zip(concepts, per_concept) if i in scores]
        if hits:
            total = sum(score for _, (score, _) in hits) * len(hits) / len(concepts)
            ranked.append({"record": record, "score": total, "matched": [(name, label) for name, (_, label) in hits]})
    ranked.sort(key=lambda hit: (-hit["score"], -len(hit["matched"]), natural(hit["record"].id)))
    return ranked, unmatched


def best_line(texts: list[str], concepts: list[dict[str, Any]]) -> str:
    """The line or sentence that matches the most concepts, verbatim, cut to SNIPPET_CHARS."""
    best, best_hits = "", 0
    for text in texts:
        for line in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")):
            line = line.strip()
            found, joined = analyze(line)
            if not found and not joined:
                continue
            counts = Counter(found)
            counts.update(joined)
            hits = sum(1 for c in concepts if any(field_tf(found, counts, a) > 0 for a in c["alternatives"]))
            if hits > best_hits:
                best, best_hits = line, hits
    return best if len(best) <= SNIPPET_CHARS else best[: SNIPPET_CHARS - 1] + "…"


def vocabulary(top: list[Record], records: list[Record], concepts: list[dict[str, Any]]) -> list[str]:
    """Words and word pairs the top results use that the search did not — the project's own wording."""
    asked = {term for c in concepts for a in c["alternatives"] for term in a["terms"]}
    asked |= {a["joined"] for c in concepts for a in c["alternatives"] if a["joined"]}
    document_frequency: Counter = Counter()
    for record in records:
        document_frequency.update({term for _, _, counts in record.fields for term in counts})
    total = len(records) or 1

    def idf(term: str) -> float:
        return math.log(1 + total / (1 + document_frequency[term]))

    def useful(term: str) -> bool:
        return len(term) > 2 and not term.isdigit() and term not in asked and document_frequency[term] / total <= COMMON

    weights: Counter = Counter()
    pairs: Counter = Counter()
    surfaces: dict[str, Counter] = {}
    for record in top:
        seen_pairs = set()
        for weight, found, counts in record.fields:
            for term, count in counts.items():
                if useful(term):
                    weights[term] += weight * count * idf(term)
            seen_pairs.update(zip(found, found[1:]))
        pairs.update(pair for pair in seen_pairs if all(useful(t) for t in pair))
        for text in [record.item.get("title"), record.item.get("description"), record.item.get("rationale")]:
            for word in surface_words(text):
                if word not in STOPWORDS:
                    surfaces.setdefault(stem(word), Counter())[word] += 1

    def shown(term: str) -> str:
        return surfaces[term].most_common(1)[0][0] if term in surfaces else term

    phrases = [f"{shown(a)} {shown(b)}" for (a, b), n in sorted(pairs.items(), key=lambda kv: (-kv[1], -idf(kv[0][0]) - idf(kv[0][1]))) if n >= 2][:6]
    singles = [shown(term) for term, _ in weights.most_common(12)]
    return phrases + [w for w in singles if not any(w in p.split() for p in phrases)]


def location_of(item: dict[str, Any]) -> str:
    raw = item.get("raw_fields") or {}
    return str(raw.get("folder_path") or raw.get("feature_path") or item.get("feature") or "")


def where_of(item: dict[str, Any]) -> str:
    """The folders that place a requirement beyond its feature name: below it (with roots), or just above it."""
    parts = [part.strip() for part in location_of(item).split("/") if part.strip()]
    if not parts:
        return ""
    if parts[-1] != str(item.get("feature") or ""):
        return parts[-1]
    return " / ".join(parts[-3:-1])


def search_slug(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", " ".join(surface_words(query)[:6])).strip("-")[:40] or "search"


def case_titles(cases: list[dict[str, Any]], limit: int = 6) -> str:
    if not cases:
        return "Test cases: none — UNTESTED"
    shown = "; ".join(f"{c.get('id')} \"{one_line(c.get('title'))}\"" for c in cases[:limit])
    more = f"; +{len(cases) - limit} more" if len(cases) > limit else ""
    return f"Test cases: {shown}{more}"


def cmd_search(run_text: str, query: str, concept_args: list[str], feature: str = "", top: int = SEARCH_TOP) -> dict[str, Any]:
    run = run_folder(run_text)
    requirements_data = read_json(run / "requirements.json")
    if requirements_data is None:
        raise RunError("requirements.json is missing — collect the run first.")
    concepts = parse_concepts(concept_args, query)
    if not concepts:
        raise RunError("Nothing to search for — pass --concept 'word|other wording', or a --query with words in it.")
    top = max(5, min(int(top or SEARCH_TOP), 60))
    requirements: dict[str, dict[str, Any]] = {}
    for item in requirement_list(requirements_data):
        rid = key(item.get("id"))
        if rid and rid not in requirements:
            requirements[rid] = item
    cases = case_list(read_json(run / "test_cases.json"))
    docs_data = read_json(run / "docs.json") or {}
    tested_by: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        for link in dict.fromkeys(key(t) for t in tokens(case.get("linked_ids"))):
            if link in requirements:
                tested_by.setdefault(link, []).append(case)

    warnings: list[str] = []
    wanted = words(feature)
    in_scope = list(requirements.values())
    if wanted:
        in_scope = [r for r in in_scope if f" {wanted} " in f" {words(location_of(r))} "]
        if not in_scope:
            features = sorted({str(r.get("feature") or NO_FEATURE) for r in requirements.values()})
            raise RunError(f"No requirement sits in a folder matching {feature!r}. Features in this run: {', '.join(features[:40])}")
    kept = {key(r.get("id")) for r in in_scope}
    scoped_cases = [
        c for c in cases
        if not wanted
        or f" {wanted} " in f" {words((c.get('raw_fields') or {}).get('section_path'))} "
        or any(key(t) in kept for t in tokens(c.get("linked_ids")))
    ]

    requirement_records = [requirement_record(r) for r in in_scope]
    ranked, unmatched = rank(requirement_records, concepts)
    case_hits, _ = rank([case_record(c) for c in scoped_cases], concepts)
    doc_records = []
    for doc in docs_data.get("docs") or []:
        if wanted and not any(key(r) in kept for r in doc.get("requirement_ids") or []):
            continue
        for name in doc.get("files") or []:
            path = run / name
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                doc_records.append(Record("doc", name, doc, [(1.0, text)], [text]))
    doc_hits, _ = rank(doc_records, concepts)

    shown_ranked = ranked[:top]
    rank_of = {key(hit["record"].id): n for n, hit in enumerate(shown_ranked, start=1)}
    all_concepts = sum(1 for hit in ranked if len(hit["matched"]) == len(concepts))
    for name, labels in unmatched.items():
        if len(labels) == len(next(c for c in concepts if c["name"] == name)["alternatives"]):
            warnings.append(f"No requirement matches the concept {name!r} in any of its wordings ({', '.join(labels)}).")
    suggestions = vocabulary([hit["record"] for hit in shown_ranked[:10]], requirement_records, concepts)

    def matched_text(hit: dict[str, Any]) -> str:
        return " · ".join(name if key(name) == key(label) else f"{name} ({label})" for name, label in hit["matched"])

    groups: dict[str, dict[str, Any]] = {}
    for hit in shown_ranked:
        item = hit["record"].item
        group = groups.setdefault(str(item.get("feature") or NO_FEATURE), {"count": 0, "folders": []})
        group["count"] += 1
        folder = where_of(item)
        if folder and folder not in group["folders"]:
            group["folders"].append(folder)

    blocks: list[tuple[str, str]] = []
    if shown_ranked:
        table = ["## Where the matches are", "", "| Feature | In the top results | Folders |", "|---|---|---|"]
        for name, group in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
            folders = ", ".join(group["folders"][:4]) + (" …" if len(group["folders"]) > 4 else "")
            table.append(f"| {name} | {group['count']} | {folders} |")
        blocks.append(("\n".join(table) + "\n\n", ""))
        blocks.append(("## Ranked requirements — text verbatim\n\n", ""))
    else:
        blocks.append(("## No requirement matches any of the concepts\n\n", ""))
    for n, hit in enumerate(shown_ranked, start=1):
        item = hit["record"].item
        lines = [
            f"### {n}. {title_of(item)}",
            f"Match: {matched_text(hit)} — {len(hit['matched'])} of {len(concepts)} concepts · score {hit['score']:.1f}",
        ]
        lines += requirement_body(item, {}, {})
        url = (item.get("raw_fields") or {}).get("url")
        if url:
            lines.append(f"Link: {url}")
        lines.append(case_titles(tested_by.get(key(item.get("id")), [])))
        blocks.append(("\n".join(lines).rstrip() + "\n\n", "## Ranked requirements (continued)\n\n"))
    if case_hits:
        blocks.append((f"## Test cases that mention it — {len(case_hits)} match, the top {min(len(case_hits), SEARCH_CASES)} shown\n\n", ""))
        for hit in case_hits[:SEARCH_CASES]:
            item = hit["record"].item
            links = [f"{t} (#{rank_of[key(t)]})" if key(t) in rank_of else t for t in tokens(item.get("linked_ids"))]
            folder = (item.get("raw_fields") or {}).get("section_path") or item.get("feature") or ""
            line = (f"- {title_of(item)} · Folder: {folder} · "
                    f"Links: {', '.join(links) if links else 'none'} · matches: {matched_text(hit)}")
            snippet = best_line(hit["record"].texts, concepts)
            blocks.append((line + (f"\n  > {snippet}" if snippet else "") + "\n", "## Test cases that mention it (continued)\n\n"))
        blocks.append(("\n", ""))
    if doc_hits:
        blocks.append(("## Linked docs that mention it\n\n", ""))
        for hit in doc_hits[:SEARCH_DOCS]:
            doc = hit["record"].item
            line = (f"- {hit['record'].id} ({doc.get('url')}) — linked from {', '.join(doc.get('requirement_ids') or [])} · "
                    f"matches: {matched_text(hit)}")
            snippet = best_line(hit["record"].texts, concepts)
            blocks.append((line + (f"\n  > {snippet}" if snippet else "") + "\n", "## Linked docs that mention it (continued)\n\n"))
        blocks.append(("\n", ""))
    following = ranked[top:top + SEARCH_NEXT]
    if following:
        blocks.append(("## Next candidates — open any with show\n\n", ""))
        for n, hit in enumerate(following, start=top + 1):
            item = hit["record"].item
            blocks.append((
                f"- {n}. {title_of(item)[:100]} · {item.get('feature') or NO_FEATURE} · "
                f"matches: {matched_text(hit)} · score {hit['score']:.1f}\n",
                "## Next candidates (continued)\n\n",
            ))
        blocks.append(("\n", ""))
    if suggestions:
        blocks.append(("## Words the top results use that this search did not\n\n" + ", ".join(suggestions) + "\n", ""))

    folder = run / "search"
    folder.mkdir(mode=0o700, exist_ok=True)
    number = 1 + len({p.name.split("-", 1)[0] for p in folder.glob("[0-9][0-9]-*.md")})
    parts = pack_blocks(blocks)
    concept_line = " · ".join(f"{c['name']} = {' | '.join(a['label'] for a in c['alternatives'])}" for c in concepts)
    scope_note = f" in folders matching {feature!r}" if wanted else ""
    files = []
    for p, body in enumerate(parts, start=1):
        title = f"# Search {number}: {one_line(query)}" + (f" — part {p} of {len(parts)}" if len(parts) > 1 else "")
        header = (
            f"{title}\nConcepts: {concept_line}\n"
            f"Searched {len(in_scope)} requirements, {len(scoped_cases)} test cases, and {len(doc_records)} doc parts{scope_note}. "
            f"Requirements matching every concept: {all_concepts} · matching at least one: {len(ranked)} · shown in full: {len(shown_ranked)}\n\n"
        )
        name = f"search/{number:02d}-{search_slug(query)}-{p}.md"
        write_private(run / name, (header + body).rstrip() + "\n")
        files.append(name)
    return {
        "search": number,
        "files": files,
        "concepts": [{"name": c["name"], "wordings": [a["label"] for a in c["alternatives"]]} for c in concepts],
        "searched": {"requirements": len(in_scope), "test_cases": len(scoped_cases), "doc_parts": len(doc_records)},
        "matched": {"requirements": len(ranked), "every_concept": all_concepts, "test_cases": len(case_hits), "doc_parts": len(doc_hits)},
        "top": [
            {"rank": n, "id": hit["record"].id, "score": round(hit["score"], 1), "matches": [name for name, _ in hit["matched"]],
             "feature": hit["record"].item.get("feature") or NO_FEATURE}
            for n, hit in enumerate(shown_ranked[:10], start=1)
        ],
        "by_feature": [{"feature": name, "count": g["count"]} for name, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"])],
        "unmatched_wordings": unmatched,
        "suggested_terms": suggestions,
        "warnings": warnings,
    }


def cmd_show(run_text: str, ids_text: str) -> str:
    """Markdown for the requested requirements and test cases, verbatim."""
    run = run_folder(run_text)
    wanted = [t for t in re.split(r"[\s,;]+", ids_text or "") if t][:MAX_SHOW_IDS]
    if not wanted:
        raise RunError("Pass --ids with the requirement or test case IDs to show.")
    requirements: dict[str, dict[str, Any]] = {}
    for item in requirement_list(read_json(run / "requirements.json")):
        requirements.setdefault(key(item.get("id")), item)
    cases = case_list(read_json(run / "test_cases.json"))
    cases_by_id = {key(c.get("id")): c for c in cases}
    tested_by: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        for link in dict.fromkeys(key(t) for t in tokens(case.get("linked_ids"))):
            tested_by.setdefault(link, []).append(case)
    out = [f"# From run {run.name} — text verbatim", ""]
    missing = []
    for raw in wanted:
        item = requirements.get(key(raw))
        if item is not None:
            lines = [f"## {title_of(item)}", *requirement_body(item, {}, {})]
            url = (item.get("raw_fields") or {}).get("url")
            if url:
                lines.append(f"Link: {url}")
            lines.append(case_titles(tested_by.get(key(item.get("id")), []), limit=20))
            out.append("\n".join(lines).rstrip() + "\n")
            continue
        number = re.fullmatch(r"(?i)(?:tc-?|c)?(\d+)", raw)
        case = cases_by_id.get(key(f"TC-{number.group(1)}")) if number else None
        if case is not None:
            out.append(case_block(case).replace("### ", "## ", 1).rstrip() + "\n")
        else:
            missing.append(raw)
    if missing:
        out.append(f"Not in this run: {', '.join(missing)}")
    return "\n".join(out).rstrip() + "\n"


def cmd_recent(project_text: str, max_age_minutes: int = RECENT_MINUTES) -> dict[str, Any]:
    """The newest run of the whole scope, collected within max_age_minutes, complete, and still matching the config."""
    project = Path(project_text).expanduser().resolve()
    runs = project / ".buddy-council" / "runs"
    if not runs.is_dir():
        return {"run_dir": None, "reason": "no runs yet"}
    config = read_json(project / ".buddy-council" / "sources.json") or {}
    now = datetime.datetime.now(datetime.timezone.utc)
    folders = sorted((p for p in runs.iterdir() if RUN_ID.fullmatch(p.name) and p.is_dir() and not p.is_symlink()), key=lambda p: p.name, reverse=True)
    for folder in folders:
        try:
            manifest = read_json(folder / "manifest.json") or {}
            created = datetime.datetime.fromisoformat(str(manifest.get("created_at")))
            age = (now - created).total_seconds() / 60
            if age > max_age_minutes or age < 0:
                continue
            links = read_json(folder / "links.json") or {}
            if (links.get("scope") or {}).get("mode") != "all" or links.get("missing") or not links.get("lines"):
                continue
            if verify_run(config, manifest, read_json(folder / "requirements.json"), read_json(folder / "test_cases.json")):
                continue
        except (RunError, ValueError, TypeError):
            continue
        return {"run_dir": str(folder), "age_minutes": int(age), "lines": links["lines"], "counts": links.get("counts")}
    return {"run_dir": None, "reason": f"no complete, verified run of the whole scope in the last {max_age_minutes} minutes"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buddy-Council run folders: new, enrich, link, search, show, recent.")
    sub = parser.add_subparsers(dest="action", required=True)
    new = sub.add_parser("new", help="create a run folder and prune old ones")
    new.add_argument("--project", required=True, help="absolute path of the project folder that holds .buddy-council")
    new.add_argument("--command", dest="command_name", required=True, help="the analysis command, e.g. coverage")
    new.add_argument("--scope", default="", help="the scope the run analyzes, as the user gave it")
    for name, text in (("enrich", "fetch the GitHub docs the requirements link to (gh CLI)"), ("link", "cross-link and write links.json and the bundles")):
        sub.add_parser(name, help=text).add_argument("--run", required=True, help="the run folder")
    search = sub.add_parser("search", help="rank the run's requirements, test cases, and docs against an ask")
    search.add_argument("--run", required=True)
    search.add_argument("--query", required=True, help="the ask, as the user put it")
    search.add_argument("--concept", action="append", default=[], help="one idea of the ask: 'own words|other wording|...'")
    search.add_argument("--feature", default="", help="only requirements in folders whose path contains these words")
    search.add_argument("--top", type=int, default=SEARCH_TOP, help="requirements written out in full (5-60)")
    show = sub.add_parser("show", help="print requirements and test cases by ID, as Markdown")
    show.add_argument("--run", required=True)
    show.add_argument("--ids", required=True)
    recent = sub.add_parser("recent", help="the newest complete, verified run of the whole scope")
    recent.add_argument("--project", required=True)
    recent.add_argument("--max-age-minutes", type=int, default=RECENT_MINUTES)
    args = parser.parse_args(argv)
    try:
        if args.action == "show":
            print(cmd_show(args.run, args.ids), end="")
            return 0
        if args.action == "new":
            result = cmd_new(args.project, args.command_name, args.scope)
        elif args.action == "enrich":
            result = cmd_enrich(args.run)
        elif args.action == "search":
            result = cmd_search(args.run, args.query, args.concept, args.feature, args.top)
        elif args.action == "recent":
            result = cmd_recent(args.project, args.max_age_minutes)
        else:
            result = cmd_link(args.run)
    except RunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
