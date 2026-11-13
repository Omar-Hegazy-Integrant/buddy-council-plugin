#!/usr/bin/env python3
"""Run folders for Buddy-Council analysis commands. Standard library only; Python 3.9+.

A run folder, ``<project>/.buddy-council/runs/<run-id>/``, carries one analysis run's data from the collect
step to the analysis as files, so the data itself never passes through a model:

  requirements.json  written by the Jama server (``save_to``)
  background.json    lookups on Jama only: the rest of the project, context for answers (``save_to``)
  product.json       contradiction runs on part of the product: the whole product, to compare it with (``save_to``)
  test_cases.json    written by the TestRail server (``save_to``)
  docs/, docs.json   GitHub docs the requirements link to (``enrich``, the cli strategy)
  links.json         requirement <-> test case links, untested, orphans, stranded links (``link``)
  bundles/*.md       one feature at a time, verbatim text, each part small enough for one read (``link``); in a
                     contradiction run, a feature too big for one slice is cut between requirements, and each piece
                     stands alone
  lines/*.json       each slice reader's subject lines: what each item is about, and what it says (``subjects``)
  pairs.json, pairs/ items on the same subject in different slices, or outside the scope, in slices of pairs (``match``)
  tickets.json, tickets/  the board tickets the session read, each with its subject line, and slices of tickets, each
                     with the requirements and test cases it is compared with (``tickets``)
  findings/*.json    what /bc:contradiction found, each checked against the run (``findings``)
  report-N.md        the contradiction report, written from the findings, in parts (``report``)
  manifest.json      what was collected, when, and the counts

Subcommands, each printing one JSON object to stdout:

  new    --project DIR --command NAME [--scope TEXT]   create a run folder, prune old ones
  enrich --run DIR                                     fetch the linked GitHub docs with the first sign-in
                                                       that works: gh, GH_TOKEN/GITHUB_TOKEN, the host's saved
                                                       key, then none (public repositories)
  link   --run DIR                                     cross-link, write links.json and the bundles
  search --run DIR --query TEXT [--concept 'a|b|c']... [--feature TEXT] [--top N]
                                                       rank the run against an ask (BM25), write search/*.md
  show   --run DIR --ids 'ID, ID'                      print those requirements, background items, and test
                                                       cases (Markdown)
  summary --run DIR [--feature NAME]                   what a model reads of links.json, small enough to read
                                                       at once: the checks, lines, counts, and feature rows —
                                                       or one feature's bundle parts, docs, and lists
  recent --project DIR [--max-age-minutes N]           the newest complete, verified run of the whole scope
  doc    --url URL [--max-chars N]                     print one GitHub doc, read with the first sign-in that
                                                       works (for commands that enrich without a run folder)
  findings --run DIR --add JSON [--slice N]            check findings against the run: save each whose IDs and
                                                       quotes are in it, send back the rest with the reason; with
                                                       --slice, a save that leaves nothing rejected marks the slice
                                                       checked, --add '[]' included
  findings --run DIR [--numbers 'N, N']                the saved findings, numbered as in the report
  slices --run DIR [--slice N]                         a contradiction run's slices, each small enough for one
                                                       reader to check at once, and which are checked — or one
                                                       slice's bundle parts and docs, as paths, and the items it
                                                       still needs a subject line for
  subjects --run DIR --slice N --add JSON              save a slice reader's subject lines, one per item; a slice
                                                       matched with others is checked once each item has one
  match  --run DIR                                     pair the items on the same subject in different slices — or,
                                                       in a run on part of the product, with the rest of it — and
                                                       cut the pairs into slices of their own
  tickets --run DIR --add JSON                         save the board tickets, each with its subject line; find for
                                                       each the requirements it links to and the requirements and
                                                       test cases on its subject; cut them into slices of their own
  report --run DIR [--lines JSON] [--partial TEXT]     write the contradiction report from the saved findings,
                                                       in parts small enough for one read

It also saves the progress logs beside the settings, so neither prompts:

  progress --project DIR --log onboarding|vnv --patch JSON   merge a change into onboarding-progress.json or
                                                       vnv-progress.json (created when absent)
  note     --project DIR --title TEXT --text TEXT      append one entry to onboarding-notes.md
  archive  --project DIR --log onboarding|vnv          set a log (and its notes) aside under a dated name

``--patch``, ``--add``, and ``--lines`` are JSON on one line, with every ' written as \u0027, so the command stays one
plain line the bash hook can approve. It merges like the settings do — objects key by key, anything else replaces — except that the log's
own list merges item by item: onboarding ``features`` by ``name``, V&V ``stories`` by ``dev_key``.

Nothing here talks to Jama, TestRail, or Jira, and nothing is written outside the project's .buddy-council folder.
``enrich`` reads GitHub only with GET requests, never following a redirect with a token.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import contextlib
import copy
import datetime
import difflib
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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bc_keys  # noqa: E402

KEEP_RUNS = 5
MAX_RUN_AGE_SECONDS = 7 * 24 * 3600
PART_BYTES = 18_000  # a bundle part fits one file read in both runtimes (Copilot truncates at 20 KB)
SLICE_BYTES = 90_000  # what one reader checks at once: about five bundle parts, with the docs they cite
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
        if command != "setup":
            raise RunError(f"{project} has no .buddy-council folder — run /bc:setup in this project first.")
        config_dir.mkdir()  # setup's own run comes before any settings exist
        exclude_from_git(project)
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
    paths = {
        "run_dir": str(run),
        "requirements": str(run / "requirements.json"),
        "test_cases": str(run / "test_cases.json"),
    }
    if command == "lookup":  # only a lookup searches the rest of the project
        paths["background"] = str(run / "background.json")
    if command == "contradiction" and scope.strip().casefold() not in ("", "all"):
        paths["product"] = str(run / "product.json")  # a run on part of the product is compared with the rest of it
    return {**paths, "pruned": pruned}


def exclude_from_git(project: Path) -> str | None:
    """Keep .buddy-council/ out of git through the clone's own info/exclude, never a tracked .gitignore."""
    try:
        done = subprocess.run(["git", "-C", str(project), "rev-parse", "--git-path", "info/exclude"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    path = Path(done.stdout.strip())
    path = path if path.is_absolute() else project / path
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if any(line.strip().strip("/") == ".buddy-council" for line in text.splitlines()):
            return str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(("" if not text or text.endswith("\n") else "\n") + ".buddy-council/\n")
    except OSError:
        return None
    return str(path)


def merge(base: dict, patch: dict) -> dict:
    """Merge a patch in place: objects key by key, anything else — a list, a number, null — replaces."""
    for name, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(name), dict):
            merge(base[name], value)
        else:
            base[name] = copy.deepcopy(value)
    return base


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
# enrich — GitHub docs, read with the first sign-in that works (the "cli" enrichment strategy)
# ---------------------------------------------------------------------------

URLOPEN = None  # tests put a fake here; None is urllib's own opener, which never follows a redirect


def parse_github_url(url: str) -> dict[str, Any]:
    """A GitHub blob/tree URL — github.com, raw.githubusercontent.com, or a GitHub Enterprise host — as
    {owner, repo, path, ref, anchor, kind, host}."""
    parsed = urllib.parse.urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
    if parsed.scheme != "https":
        raise RunError("unsupported_url: not an https URL")
    if host == "raw.githubusercontent.com" and len(parts) >= 4:
        owner, repo, ref, rest, kind = parts[0], parts[1], parts[2], parts[3:], "blob"
        host = "github.com"
    elif bc_keys.github_host(host) and len(parts) >= 4 and parts[2] in ("blob", "tree"):
        owner, repo, kind, ref, rest = parts[0], parts[1], parts[2], parts[3], parts[4:]
        host = "github.com" if host == "www.github.com" else host
    else:
        raise RunError("unsupported_url: not a GitHub blob or tree URL")
    if kind == "blob" and not rest:
        raise RunError("unsupported_url: no file path in the URL")
    for piece in [owner, repo, ref, *rest]:
        if piece in (".", "..") or any(c in piece for c in "\n\r?#"):
            raise RunError("unsupported_url: malformed path in the URL")
    return {"owner": owner, "repo": repo, "path": "/".join(rest), "ref": ref, "anchor": parsed.fragment or None, "kind": kind,
            "host": host}


GITHUB_TOKENS = ("GH_TOKEN", "GITHUB_TOKEN")
ENTERPRISE_TOKENS = ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def gh_env(host: str) -> dict[str, str]:
    """The environment gh runs in for one host, without the tokens that belong elsewhere: gh sends GH_TOKEN to
    github.com and *.ghe.com hosts, and GH_ENTERPRISE_TOKEN to whatever Enterprise host it targets."""
    if host == bc_keys.gh_host():
        return dict(os.environ)
    drop = set(ENTERPRISE_TOKENS) | (set() if host == "github.com" else set(GITHUB_TOKENS))
    return {name: value for name, value in os.environ.items() if name not in drop}


def gh_api(endpoint: str, runner: Any = subprocess.run, host: str = "github.com") -> Any:
    """GET one GitHub API endpoint through gh. Raises RunError("<kind>: <detail>") on failure."""
    try:
        done = runner(
            ["gh", "api", "--hostname", host, "-H", "Accept: application/vnd.github+json", endpoint],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            env=gh_env(host),
        )
    except FileNotFoundError:
        raise RunError("gh_missing: the gh CLI is not installed") from None
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A token is sent only to the host it belongs to, so a redirect is an error, never followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib's signature
        raise urllib.error.HTTPError(req.full_url, code, f"redirected to {newurl}, which is not followed", headers, fp)


def http_api(host: str, token: str | None, endpoint: str) -> Any:
    """GET one GitHub API endpoint over HTTPS, signed in with `token` or not at all."""
    base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "buddy-council"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base}/{endpoint}", headers=headers)
    try:
        opener = URLOPEN or urllib.request.build_opener(_NoRedirect).open
        with opener(request, timeout=GH_TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read(300).decode("utf-8", "replace")
        remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
        if exc.code == 404:
            raise RunError(f"not_found: HTTP 404 from {host}") from None
        if exc.code in (403, 429) and (remaining == "0" or "rate limit" in detail.lower()):
            raise RunError(f"rate_limit: {host} rate-limited the request") from None
        if exc.code in (401, 403):
            raise RunError(f"auth_failed: HTTP {exc.code} from {host}") from None
        raise RunError(f"unknown: HTTP {exc.code} from {host} ({exc.msg})") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RunError(f"network: {getattr(exc, 'reason', exc)}") from None
    try:
        return json.loads(body)
    except ValueError:
        raise RunError(f"unknown: {host} returned something other than JSON") from None


def env_tokens(host: str) -> tuple[str, ...]:
    """The environment variables whose token may go to `host`: GH_TOKEN or GITHUB_TOKEN for github.com, and for
    the Enterprise host the user named in GH_HOST, its token — never one for a host the user didn't name."""
    if host == "github.com":
        return GITHUB_TOKENS
    if host != bc_keys.gh_host():
        return ()
    return GITHUB_TOKENS if host.endswith(".ghe.com") else ENTERPRISE_TOKENS


def readers(host: str, runner: Any = subprocess.run) -> list[tuple[str, Any]]:
    """The ways to read GitHub at a host, in the order they are tried: gh, a token in the environment set for the
    host (env_tokens), the key saved for the host, then no sign-in, which reads public repositories."""
    found: list[tuple[str, Any]] = [("gh", lambda endpoint: gh_api(endpoint, runner, host))]
    for name in env_tokens(host):
        token = os.environ.get(name, "").strip()
        if token:
            found.append((name, lambda endpoint, token=token: http_api(host, token, endpoint)))
            break
    if any(entry["site"] == host for entry in bc_keys.sites("github")):
        try:
            token = bc_keys.read_key(host, "github", migrate=False)["token"]
            found.append(("saved key", lambda endpoint, token=token: http_api(host, token, endpoint)))
        except bc_keys.KeyStoreError:
            pass
    found.append(("no sign-in", lambda endpoint: http_api(host, None, endpoint)))
    return found


# The errors the chain reports when no sign-in could read a doc, most telling first. A missing file seen by a reader
# that could read the repository is final: another sign-in won't find it either.
ERROR_ORDER = ("file_not_found", "directory_no_markdown", "repo_inaccessible", "rate_limit", "auth_failed", "network",
               "unknown", "not_found", "gh_missing")
FINAL = {"file_not_found", "directory_no_markdown"}


def read_doc(url: str, runner: Any = subprocess.run) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """One doc, read with the first sign-in that works. Returns the doc, the sign-in that read it, and what was tried;
    raises the most telling error when none could, with `.tried` attached."""
    locator = parse_github_url(url)
    tried: list[dict[str, Any]] = []
    errors: list[RunError] = []
    for name, get in readers(locator["host"], runner):
        try:
            doc = fetch_doc(url, get)
        except RunError as exc:
            kind = str(exc).partition(":")[0]
            tried.append({"reader": name, "error": kind})
            errors.append(exc)
            if kind in FINAL:
                break
            continue
        tried.append({"reader": name, "ok": True})
        return doc, name, tried
    order = {kind: n for n, kind in enumerate(ERROR_ORDER)}
    best = min(errors, key=lambda exc: order.get(str(exc).partition(":")[0], len(order)))
    best.tried = tried  # type: ignore[attr-defined]
    raise best


def contents_endpoint(locator: dict[str, Any], path: str) -> str:
    quoted = urllib.parse.quote(path, safe="/")
    ref = urllib.parse.quote(locator["ref"], safe="")
    owner = urllib.parse.quote(locator["owner"], safe="")
    repo = urllib.parse.quote(locator["repo"], safe="")
    return f"repos/{owner}/{repo}/contents/{quoted}?ref={ref}"


def fetch_file(locator: dict[str, Any], path: str, get: Any) -> str:
    body = get(contents_endpoint(locator, path))
    if isinstance(body, list):
        raise RunError("is_directory: the URL points at a folder")
    content = body.get("content") or ""
    if not content and body.get("sha") and (body.get("size") or 0) > 0:  # files over ~1 MB come back empty
        owner, repo = (urllib.parse.quote(locator[k], safe="") for k in ("owner", "repo"))
        sha = urllib.parse.quote(str(body["sha"]), safe="")
        content = get(f"repos/{owner}/{repo}/git/blobs/{sha}").get("content") or ""
    return base64.b64decode(content).decode("utf-8", errors="replace")


def fetch_directory(locator: dict[str, Any], get: Any) -> tuple[str, str | None]:
    entries = get(contents_endpoint(locator, locator["path"]))
    if not isinstance(entries, list):
        raise RunError("unknown: expected a folder listing")
    readme = next((e for e in entries if str(e.get("name", "")).lower() == "readme.md"), None)
    if readme:
        return fetch_file(locator, str(readme["path"]), get), None
    markdown = [e for e in entries if e.get("type") == "file" and str(e.get("name", "")).lower().endswith(".md")]
    if not markdown:
        raise RunError("directory_no_markdown: the folder has no README.md or markdown files")
    chosen = markdown[:5]
    content = "\n\n---\n\n".join(fetch_file(locator, str(e["path"]), get) for e in chosen)
    omitted = len(markdown) - len(chosen)
    return content, (f"{omitted} more markdown files in the folder were not fetched" if omitted else None)


def fetch_doc(url: str, get: Any) -> dict[str, Any]:
    """One doc's content, read through `get` (one sign-in): a file, or a folder's README or first *.md files."""
    locator = parse_github_url(url)
    try:
        if locator["kind"] == "tree":
            content, note = fetch_directory(locator, get)
        else:
            content, note = fetch_file(locator, locator["path"], get), None
    except RunError as exc:
        text = str(exc)
        if text.startswith("is_directory"):
            content, note = fetch_directory(locator, get)
        elif text.startswith("not_found"):
            owner, repo = (urllib.parse.quote(locator[k], safe="") for k in ("owner", "repo"))
            try:
                get(f"repos/{owner}/{repo}")
            except RunError:
                raise RunError(f"repo_inaccessible: {locator['owner']}/{locator['repo']} is private to you or does not exist") from None
            raise RunError(f"file_not_found: {locator['path']} at {locator['ref']}") from None
        else:
            raise
    keep = ("owner", "repo", "path", "ref", "anchor") + (() if locator["host"] == "github.com" else ("host",))
    return {"locator": {k: locator[k] for k in keep}, "content": content, "note": note}


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
            url = (f"https://raw.githubusercontent.com/{locator['owner']}/{locator['repo']}/{locator['ref']}/{resolved}"
                   if locator.get("host", "github.com") == "github.com" else
                   f"https://{locator['host']}/{locator['owner']}/{locator['repo']}/raw/{locator['ref']}/{resolved}")
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
            doc, reader, _ = read_doc(url, runner)
            return url, {**doc, "read_with": reader}
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
        entry["read_with"] = outcome["read_with"]
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


def doc_index(docs_data: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """The docs each requirement links to, by its key: those in the run, and those that are not — failed, or left to
    the reader's own GitHub connection."""
    docs_by_req: dict[str, list[dict[str, Any]]] = {}
    for doc in docs_data.get("docs") or []:
        for rid in doc.get("requirement_ids") or []:
            docs_by_req.setdefault(key(rid), []).append(doc)
    missing_by_req: dict[str, list[dict[str, Any]]] = {}
    for doc in (docs_data.get("failed") or []) + (docs_data.get("pending") or []):
        for rid in doc.get("requirement_ids") or []:
            missing_by_req.setdefault(key(rid), []).append(doc)
    return docs_by_req, missing_by_req


def cmd_link(run_text: str) -> dict[str, Any]:
    run = run_folder(run_text)
    manifest = read_json(run / "manifest.json") or {}
    requirements_data = read_json(run / "requirements.json")
    cases_data = read_json(run / "test_cases.json")
    docs_data = read_json(run / "docs.json") or {}
    if requirements_data is None:
        raise RunError("requirements.json is missing — the requirements fetch did not complete.")
    config = read_json(run.parent.parent / "sources.json") or {}
    warnings: list[str] = []
    if cases_data is None and (config.get("test_cases") or {}).get("provider") == "testrail":
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

    docs_by_req, missing_by_req = doc_index(docs_data)

    lookup = manifest.get("command") == "lookup"
    background_data = read_json(run / "background.json") if lookup else None  # only a lookup reads the rest
    expect_background = lookup and (config.get("requirements") or {}).get("provider") == "jama"
    # A contradiction run on part of the product is compared with the rest of it, which is matched, never counted.
    across = manifest.get("command") == "contradiction" and mode == "scoped"
    product_data = read_json(run / "product.json") if across else None
    rest = None  # how many requirements and test cases the rest of the product holds
    if product_data is not None:
        in_scope_ids = {key(c.get("id")) for c in in_scope}
        rest = (len({key(r.get("id")) for r in requirement_list(product_data)} - set(by_id) - {""}),
                sum(1 for c in cases if key(c.get("id")) not in in_scope_ids))
    mismatches = verify_run(config, manifest, requirements_data, cases_data, background_data, product_data)
    lines, missing, enrichment = report_lines(
        config, requirements_data, requirements, cases_data, cases, docs_data, background_data, expect_background,
        rest, across,
    )

    bundles = run / "bundles"
    bundles.mkdir(mode=0o700, exist_ok=True)
    for old in bundles.glob("*.md"):
        old.unlink()
    cases_by_id = {str(c.get("id") or ""): c for c in in_scope}
    rows: list[dict[str, Any]] = []
    slicing = manifest.get("command") == "contradiction"  # only its readers check a run a slice at a time
    pieces: list[list[dict[str, Any]]] = []  # each feature's pieces: one, or several when it is too big for a slice
    groups = [(f, [r for r in by_id.values() if r["feature"] == f], [o for o in orphans if o["feature"] == f]) for f in features]
    leftover = [o for o in orphans if o["feature"] not in features]
    if leftover:
        groups.append((NO_FEATURE, [], leftover))
    for n, (feature, members, feature_orphans) in enumerate(groups, start=1):
        bundle = write_bundle(run, n, len(groups), feature, members, cases_by_id, feature_orphans, docs_by_req,
                              missing_by_req, SLICE_BYTES if slicing else 0)
        files = [part for piece in bundle for part in piece["parts"]]
        pieces.append(bundle)
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
    if background_data is not None:
        counts["background"] = len(requirement_list(background_data))
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
    if slicing:
        links["slices"] = slice_run(run, pieces)
        # Items in different slices, or outside the scope, are matched by the subject lines their readers save.
        links["subject_lines"] = len(links["slices"]) > 1 or bool(rest and any(rest))
        if rest is not None:
            links["outside"] = {"requirements": rest[0], "test_cases": rest[1]}
        clear_later_slices(run)
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
        if doc.get("error"):
            lines.append(f"Linked doc not in this run: {doc['url']} ({doc['error']})")
        else:  # the mcp strategy: read by whoever reads this requirement, a helper included
            lines.append(f"Linked doc not in this run: {doc['url']} — read it once through your GitHub connection: "
                         "providers/github/fetch-mcp.md")
    return lines


def requirement_block(
    requirement: dict[str, Any],
    docs_by_req: dict[str, list[dict[str, Any]]],
    missing_by_req: dict[str, list[dict[str, Any]]],
    continued: bool = False,
) -> str:
    lines = [f"## {title_of(requirement)}{' (continued)' if continued else ''}"]
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


def text_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def pack_blocks(blocks: list[tuple[str, str]]) -> list[str]:
    """Blocks packed into parts of at most PART_BYTES; a part that starts mid-section opens with its restart header."""
    parts: list[str] = []
    current, size = "", 0
    for text, restart in blocks:
        weight = text_bytes(text)
        if current and size + weight > PART_BYTES:
            parts.append(current)
            current, size = restart, text_bytes(restart)
        current += text
        size += weight
    if current or not parts:
        parts.append(current)
    return parts


ORPHANS_HEADING = "## Orphan test cases — no requirement ID in the requirement field\n\n"


def bundle_units(
    members: list[dict[str, Any]],
    cases_by_id: dict[str, dict[str, Any]],
    feature_orphans: list[dict[str, Any]],
    docs_by_req: dict[str, list[dict[str, Any]]],
    missing_by_req: dict[str, list[dict[str, Any]]],
    doc_size: dict[str, int],
    cap: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """A feature as units no slice splits — each requirement with the cases that test it, then each orphan case — and
    each linked case's text. With a cap, a requirement with more cases than a slice of cap bytes holds beside its text
    and docs is shared out over several units, each with the requirement, so whoever reads a share can compare its
    cases with it."""
    case_text: dict[str, str] = {}
    units: list[dict[str, Any]] = []
    for requirement in members:
        head = text_bytes(requirement_block(requirement, docs_by_req, missing_by_req))
        linked = [cid for cid in requirement["linked_ids"] if cid in cases_by_id]
        for cid in linked:
            case_text.setdefault(cid, case_block(cases_by_id[cid]))
        docs = feature_docs([requirement], docs_by_req)
        sizes = [(text_bytes(case_text[cid]), []) for cid in linked]
        room = max(cap - head - sum(doc_size.get(doc, 0) for doc in docs), cap // 3)  # a third, however big the docs
        whole = not cap or sum(size for size, _ in sizes) <= room
        shares = [list(range(len(linked)))] if whole else split_evenly(sizes, {}, room)
        for k, share in enumerate(shares):
            units.append({"requirement": requirement, "continued": k > 0, "cases": [linked[i] for i in share],
                          "bytes": head, "docs": docs})
    for orphan in feature_orphans:
        case = cases_by_id.get(str(orphan["id"]))
        if case is not None:
            stranded = orphan.get("stranded_ids")
            text = case_block(case, f"Requirement ID stranded in References: {', '.join(stranded)}" if stranded else "")
            units.append({"orphan": str(orphan["id"]), "text": text, "bytes": text_bytes(text), "docs": []})
    return units, case_text


def piece_blocks(
    units: list[dict[str, Any]],
    case_text: dict[str, str],
    docs_by_req: dict[str, list[dict[str, Any]]],
    missing_by_req: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, str]]:
    """One piece of a feature as blocks to pack: a case is written out once in it, and pointed to after that."""
    blocks: list[tuple[str, str]] = []  # (text, header that restarts its section in a new part)
    shown: dict[str, str] = {}
    orphans_open = False
    for unit in units:
        if "orphan" in unit:
            if not orphans_open:
                blocks.append((ORPHANS_HEADING, ""))
                orphans_open = True
            blocks.append((unit["text"], "## Orphan test cases (continued)\n\n"))
            continue
        requirement = unit["requirement"]
        blocks.append((requirement_block(requirement, docs_by_req, missing_by_req, unit["continued"]), ""))
        restart = f"## {title_of(requirement)} (continued)\n\n"
        for cid in unit["cases"]:
            if cid in shown:
                blocks.append((f"### {cid} — shown above, under {shown[cid]}\n\n", restart))
            else:
                shown[cid] = str(requirement.get("id"))
                blocks.append((case_text[cid], restart))
    return blocks


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
    cap: int = 0,
) -> list[dict[str, Any]]:
    """Each requirement followed by the cases that test it, then the feature's orphans. With a cap, a feature bigger
    than cap bytes is cut between requirements into pieces that each stand alone; without one, it is one piece. Each
    piece is packed into parts small enough for one read, numbered across the feature."""
    doc_size = {doc: file_bytes(run, [doc]) for doc in feature_docs(members, docs_by_req)}
    units, case_text = bundle_units(members, cases_by_id, feature_orphans, docs_by_req, missing_by_req, doc_size, cap)
    # A doc, or a case linked to several requirements, is written out once in each piece that holds it.
    sizes = {**doc_size, **{f"case:{cid}": text_bytes(text) for cid, text in case_text.items()}}
    weights = [(u["bytes"], u["docs"] + [f"case:{cid}" for cid in u.get("cases", [])]) for u in units]
    groups = (split_evenly(weights, sizes, cap) if cap else []) or [list(range(len(units)))]
    pieces: list[tuple[list[str], dict[str, Any]]] = []  # each piece's part texts, and what it holds
    for group in groups:
        mine = [units[i] for i in group]
        pieces.append((pack_blocks(piece_blocks(mine, case_text, docs_by_req, missing_by_req)), {
            "features": [feature],
            "docs": list(dict.fromkeys(doc for unit in mine for doc in unit["docs"])),
            "requirements": list(dict.fromkeys(str(u["requirement"].get("id")) for u in mine if "requirement" in u)),
            "test_cases": list(dict.fromkeys([c for u in mine for c in u.get("cases", [])]
                                             + [u["orphan"] for u in mine if "orphan" in u])),
        }))
    count = sum(len(texts) for texts, _ in pieces)
    tested = sum(1 for r in members if r["linked_ids"])
    stem = f"{n:02d}-{re.sub(r'[^a-z0-9]+', '-', feature.lower()).strip('-')[:40] or 'feature'}"
    p = 0
    for texts, piece in pieces:
        docs_line = f"Docs linked from this feature — read them with it: {', '.join(piece['docs'])}\n" if piece["docs"] else ""
        piece["parts"] = []
        for body in texts:
            p += 1
            title = f"# Feature {n} of {total}: {feature}" + (f" — part {p} of {count}" if count > 1 else "")
            header = (
                f"{title}\n{len(members)} requirements · {tested} tested · {len(members) - tested} untested · "
                f"{len(feature_orphans)} orphan test cases\n"
                f"{docs_line}"
                "Requirement and test case text below is verbatim from the source.\n\n"
            )
            name = f"bundles/{stem}-{p}.md"
            write_private(run / name, (header + body).rstrip() + "\n")
            piece["parts"].append(name)
    return [piece for _, piece in pieces]


# ---------------------------------------------------------------------------
# Slices — the run cut into parts one reader checks at once
# ---------------------------------------------------------------------------


def file_bytes(run: Path, names: list[str]) -> int:
    return sum((run / name).stat().st_size for name in names if (run / name).is_file())


Weight = tuple[int, list[str]]  # what a unit weighs: its own bytes, and the shared items it holds — docs, shared cases


def group_bytes(weights: list[Weight], item_size: dict[str, int], group: list[int]) -> int:
    """A group's bytes: its units' own, and each shared item once."""
    items = {item for i in group for item in weights[i][1]}
    return sum(weights[i][0] for i in group) + sum(item_size.get(item, 0) for item in items)


def fill_groups(weights: list[Weight], item_size: dict[str, int], cap: int) -> list[list[int]]:
    """Consecutive groups, each filled up to cap bytes: the fewest that fit. A unit bigger than cap sits alone."""
    groups: list[list[int]] = []
    size, seen = 0, set()
    for i, (own, items) in enumerate(weights):
        grow = own + sum(item_size.get(item, 0) for item in set(items) - seen)
        if not groups or size + grow > cap:
            groups.append([])
            size, seen = 0, set()
            grow = own + sum(item_size.get(item, 0) for item in set(items))
        groups[-1].append(i)
        size += grow
        seen |= set(items)
    return groups


def split_evenly(weights: list[Weight], item_size: dict[str, int], cap: int) -> list[list[int]]:
    """Consecutive groups of units, as few as fit in cap bytes each, cut as near an equal share as the units allow.
    A shared item — a doc, or a case several units hold — counts once in each group that holds it."""
    fewest = fill_groups(weights, item_size, cap)
    count = len(fewest)
    if count < 2:
        return fewest
    running, ends, seen = 0, [], set()  # the bytes up to the end of each unit, a shared item counted where it first is
    for own, items in weights:
        running += own + sum(item_size.get(item, 0) for item in set(items) - seen)
        seen |= set(items)
        ends.append(running)
    cuts, start = [], 0
    for share in range(1, count):
        target = running * share / count
        last = len(weights) - (count - share)  # leave a unit for each group still to come
        cut = min(range(start + 1, last + 1), key=lambda c: abs(ends[c - 1] - target))
        cuts.append(cut)
        start = cut
    groups = [list(range(a, b)) for a, b in zip([0, *cuts], [*cuts, len(weights)])]
    if all(len(group) == 1 or group_bytes(weights, item_size, group) <= cap for group in groups):
        return groups
    low, high = 1, cap  # the smallest limit that needs no more groups: the biggest group as small as it can be
    while low < high:
        middle = (low + high) // 2
        if len(fill_groups(weights, item_size, middle)) <= count:
            high = middle
        else:
            low = middle + 1
    return fill_groups(weights, item_size, low)


def slice_run(run: Path, by_feature: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The run's slices, in reading order, from each feature's pieces: features that fit one slice packed together up
    to SLICE_BYTES, and each piece of a bigger feature on its own."""
    doc_size = {doc: file_bytes(run, [doc]) for pieces in by_feature for piece in pieces for doc in piece["docs"]}
    groups: list[list[dict[str, Any]]] = []
    whole: list[dict[str, Any]] = []

    def pack() -> None:
        weights = [(file_bytes(run, piece["parts"]), piece["docs"]) for piece in whole]
        groups.extend([whole[i] for i in group] for group in split_evenly(weights, doc_size, SLICE_BYTES))
        whole.clear()

    for pieces in by_feature:
        if len(pieces) == 1:
            whole.append(pieces[0])
            continue
        pack()
        groups.extend([piece] for piece in pieces)
    pack()
    slices = []
    for members in groups:
        docs = list(dict.fromkeys(d for p in members for d in p["docs"]))
        parts = [part for p in members for part in p["parts"]]
        slices.append({
            "features": list(dict.fromkeys(f for p in members for f in p["features"])),
            "parts": parts,
            "docs": docs,
            "requirements": list(dict.fromkeys(r for p in members for r in p["requirements"])),
            "test_cases": list(dict.fromkeys(c for p in members for c in p["test_cases"])),
            "bytes": file_bytes(run, parts) + sum(doc_size.get(d, 0) for d in docs),
        })
    return slices


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


def jama_arg_problems(req: dict[str, Any], got: dict[str, Any], what: str) -> list[str]:
    """The selection arguments a Jama file was read with that differ from the config."""
    problems = []
    for arg, expected, actual in (
        ("item_type_filter", names(req.get("item_type_filter")), names(got.get("item_type_filter"))),
        ("item_type_exclude", names(req.get("item_type_exclude")), names(got.get("item_type_exclude"))),
        ("root_ids", id_list(req.get("roots")), id_list(got.get("root_ids"))),
        ("exclude_ids", id_list(req.get("exclude")), id_list(got.get("exclude_ids"))),
    ):
        if expected != actual:
            problems.append(f"{what} were read with {arg}={actual or 'none'}, but the config says {expected or 'none'}.")
    return problems


def site_problems(provider: str, configured: Any, data: dict[str, Any], what: str) -> list[str]:
    """A file read from another site than the config names: two sites can both have a project 42. The servers
    record the site they read; a file without one is not judged."""
    got = data.get("site")
    if not got or not configured:
        return []
    try:
        wanted = bc_keys.site_id(bc_keys.base_url(provider, configured))
    except bc_keys.KeyStoreError:
        return []
    return [] if got == wanted else [f"{what} were read from {got}, but the config names {wanted}."]


def jama_requirements_problems(req: dict[str, Any], data: dict[str, Any], what: str) -> list[str]:
    """How a requirements file the Jama server wrote differs from the config: its site, project, and arguments."""
    got = data.get("args") or {}
    inference = req.get("feature_inference") or {}
    problems = site_problems("jama", req.get("base_url"), data, what)
    if data.get("project_id") != req.get("project_id"):
        problems.append(f"{what} were read from Jama project {data.get('project_id')}, not {req.get('project_id')}.")
    problems += jama_arg_problems(req, got, what)
    if req.get("roots") and int(got.get("feature_depth") or 1) != int(inference.get("depth") or 1):
        problems.append(f"{what} were read with feature_depth={got.get('feature_depth')}, but the config says {inference.get('depth') or 1}.")
    if str(got.get("feature_item_type") or "Folder").casefold() != str(inference.get("folder_item_type") or "Folder").casefold():
        problems.append(f"{what} were read with feature_item_type={got.get('feature_item_type')}, but the config says {inference.get('folder_item_type')}.")
    wanted = {k: v for k, v in (req.get("field_mapping") or {}).items() if v}
    used = {k: v for k, v in (got.get("field_mapping") or {}).items() if v}
    if wanted != used:
        problems.append(f"{what} were read with field_mapping={used or 'none'}, but the config says {wanted}.")
    return problems


def is_jama_requirements(data: Any) -> bool:
    return isinstance(data, dict) and data.get("source") == "jama" and data.get("kind", "requirements") == "requirements"


def verify_run(
    config: dict[str, Any], manifest: dict[str, Any], requirements_data: Any, cases_data: Any, background_data: Any = None,
    product_data: Any = None,
) -> list[str]:
    """What in the run differs from the project's config — each entry means the collector called a tool wrongly."""
    problems: list[str] = []
    req = config.get("requirements") or {}
    provider = req.get("provider")
    is_jama_file = isinstance(requirements_data, dict) and requirements_data.get("source") == "jama"
    if provider == "jama" and not is_jama_file:
        problems.append("requirements.json was not written by the Jama server.")
    elif provider and provider != "jama":
        problems.append(f"the project reads requirements from {provider!r}, but only Jama is read now — run /bc:setup with a link to the Jama project.")
    elif is_jama_file:
        problems += jama_requirements_problems(req, requirements_data, "requirements")
        scope = (requirements_data.get("scope") or {}).get("input")
        if not same_scope(scope, manifest.get("scope")):
            problems.append(f"requirements were read for scope {scope!r}, but this run is for {manifest.get('scope')!r}.")
    if product_data is not None and provider == "jama":
        if not is_jama_requirements(product_data):
            problems.append("product.json was not written by the Jama server.")
        else:
            problems += jama_requirements_problems(req, product_data, "the product's requirements")
            scope = (product_data.get("scope") or {}).get("input")
            if not same_scope(scope, ""):
                problems.append(f"the product's requirements were read for scope {scope!r}, but the whole product is read "
                                "with scope all.")
    if background_data is not None and provider == "jama":
        if not (isinstance(background_data, dict) and background_data.get("source") == "jama"
                and background_data.get("kind") == "background"):
            problems.append("background.json was not written by the Jama server with background=true.")
        else:
            problems += site_problems("jama", req.get("base_url"), background_data, "background items")
            if background_data.get("project_id") != req.get("project_id"):
                problems.append(f"background was read from Jama project {background_data.get('project_id')}, not {req.get('project_id')}.")
            problems += jama_arg_problems(req, background_data.get("args") or {}, "background items")
    tests = config.get("test_cases") or {}
    if not tests:
        return problems
    if isinstance(cases_data, dict):
        field = custom_key((tests.get("authoring") or {}).get("requirement_field") or DEFAULT_REQUIREMENT_FIELD)
        problems += site_problems("testrail", tests.get("base_url"), cases_data, "test cases")
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
    background_data: Any, expect_background: bool, rest: tuple[int, int] | None, expect_product: bool,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """The Fetch and Enrichment lines, from the files the servers and scripts wrote — not from any model. ``rest``
    counts the requirements and test cases a run on part of the product read from the rest of it."""
    lines: list[str] = []
    missing: list[str] = []
    if isinstance(requirements_data, dict) and requirements_data.get("source") == "jama":
        summary = requirements_data.get("summary") or ""
        lines.append(f"Fetch: requirements → {len(requirements)} from Jama project {requirements_data.get('project_id')} ({summary})")
    else:
        lines.append(f"Fetch: requirements → {len(requirements)}")
    if isinstance(background_data, dict):
        summary = background_data.get("summary") or ""
        lines.append(
            f"Fetch: background → {len(requirement_list(background_data))} items from the rest of Jama project "
            f"{background_data.get('project_id')}, context for answers, never counted ({summary})"
        )
    elif expect_background:
        lines.append("Fetch: background → FAILED — no background file in the run")
        missing.append("background")
    if rest is not None:
        lines.append(f"Fetch: the rest of the product → {plural(rest[0], 'requirement')} and "
                     f"{plural(rest[1], 'test case')} outside the scope, to compare it with, never counted")
    elif expect_product:
        lines.append("Fetch: the rest of the product → FAILED — no file in the run")
        missing.append("the rest of the product")
    if cases_data is None and (config.get("test_cases") or {}).get("provider") != "testrail":
        lines.append("Fetch: test cases → skipped, not used in this project")
    elif cases_data is None:
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
SEARCH_BACKGROUND = 10
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


def background_record(item: dict[str, Any]) -> Record:
    """An item from the rest of the project, weighed like a requirement, with its text for a snippet."""
    record = requirement_record(item)
    record.kind = "background"
    record.texts = [str(item.get("description") or ""), str(item.get("rationale") or "")]
    return record


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
    background_records = [background_record(item) for item in requirement_list(read_json(run / "background.json"))]
    background_hits, _ = rank(background_records, concepts)

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
    if background_hits:
        blocks.append((
            f"## Elsewhere in the project — background, not requirements in scope: {len(background_hits)} match, "
            f"the top {min(len(background_hits), SEARCH_BACKGROUND)} shown\n\n",
            "",
        ))
        for hit in background_hits[:SEARCH_BACKGROUND]:
            item = hit["record"].item
            raw = item.get("raw_fields") or {}
            facts = [str(raw["item_type"])] if raw.get("item_type") else []
            if item.get("status"):
                facts.append(f"Status: {item['status']}")
            if location_of(item):
                facts.append(f"Folder: {location_of(item)}")
            facts.append(f"matches: {matched_text(hit)}")
            line = f"- {title_of(item)} · " + " · ".join(facts)
            snippet = best_line(hit["record"].texts, concepts)
            link = f"\n  Link: {raw['url']}" if raw.get("url") else ""
            blocks.append((line + (f"\n  > {snippet}" if snippet else "") + link + "\n", "## Elsewhere in the project (continued)\n\n"))
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
        searched = f"{len(in_scope)} requirements, {len(scoped_cases)} test cases, and {len(doc_records)} doc parts"
        if background_records:
            searched = (f"{len(in_scope)} requirements, {len(scoped_cases)} test cases, {len(doc_records)} doc parts, "
                        f"and {len(background_records)} background items elsewhere in the project")
        header = (
            f"{title}\nConcepts: {concept_line}\n"
            f"Searched {searched}{scope_note}. "
            f"Requirements matching every concept: {all_concepts} · matching at least one: {len(ranked)} · shown in full: {len(shown_ranked)}\n\n"
        )
        name = f"search/{number:02d}-{search_slug(query)}-{p}.md"
        write_private(run / name, (header + body).rstrip() + "\n")
        files.append(name)
    return {
        "search": number,
        "files": files,
        "concepts": [{"name": c["name"], "wordings": [a["label"] for a in c["alternatives"]]} for c in concepts],
        "searched": {"requirements": len(in_scope), "test_cases": len(scoped_cases), "doc_parts": len(doc_records),
                     "background": len(background_records)},
        "matched": {"requirements": len(ranked), "every_concept": all_concepts, "test_cases": len(case_hits), "doc_parts": len(doc_hits),
                    "background": len(background_hits)},
        "top": [
            {"rank": n, "id": hit["record"].id, "score": round(hit["score"], 1), "matches": [name for name, _ in hit["matched"]],
             "feature": hit["record"].item.get("feature") or NO_FEATURE}
            for n, hit in enumerate(shown_ranked[:10], start=1)
        ],
        "top_background": [
            {"id": hit["record"].id, "score": round(hit["score"], 1), "matches": [name for name, _ in hit["matched"]],
             "folder": location_of(hit["record"].item)}
            for hit in background_hits[:5]
        ],
        "by_feature": [{"feature": name, "count": g["count"]} for name, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"])],
        "unmatched_wordings": unmatched,
        "suggested_terms": suggestions,
        "warnings": warnings,
    }


def cmd_show(run_text: str, ids_text: str) -> str:
    """Markdown for the requested requirements, background items, and test cases, verbatim."""
    run = run_folder(run_text)
    wanted = [t for t in re.split(r"[\s,;]+", ids_text or "") if t][:MAX_SHOW_IDS]
    if not wanted:
        raise RunError("Pass --ids with the requirement or test case IDs to show.")
    requirements: dict[str, dict[str, Any]] = {}
    for item in requirement_list(read_json(run / "requirements.json")):
        requirements.setdefault(key(item.get("id")), item)
    background: dict[str, dict[str, Any]] = {}
    for item in requirement_list(read_json(run / "background.json")):
        background.setdefault(key(item.get("id")), item)
    elsewhere: dict[str, dict[str, Any]] = {}  # a run on part of the product: the rest of it
    for item in requirement_list(read_json(run / "product.json")):
        if key(item.get("id")) not in requirements:
            elsewhere.setdefault(key(item.get("id")), item)
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
        outside = item is None and key(raw) in elsewhere
        item = elsewhere.get(key(raw)) if outside else item
        if item is not None:
            heading = f"## {title_of(item)}" + (f" — in {item.get('feature') or NO_FEATURE}, outside this run's scope" if outside else "")
            lines = [heading, *requirement_body(item, {}, {})]
            url = (item.get("raw_fields") or {}).get("url")
            if url:
                lines.append(f"Link: {url}")
            lines.append(case_titles(tested_by.get(key(item.get("id")), []), limit=20))
            out.append("\n".join(lines).rstrip() + "\n")
            continue
        item = background.get(key(raw))
        if item is not None:
            lines = [f"## {title_of(item)} — background, not a requirement in scope", *requirement_body(item, {}, {})]
            url = (item.get("raw_fields") or {}).get("url")
            if url:
                lines.append(f"Link: {url}")
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


ROW_FIELDS = ("feature", "requirements", "tested", "untested", "retired", "test_cases", "orphans", "coverage_pct")


def cmd_summary(run_text: str, feature: str = "") -> dict[str, Any]:
    """What a model reads of links.json, small enough to read at once: links.json itself is for code, and a whole
    project's runs it at 100 KB. Without a feature: the checks, the lines, the counts, and one short row per
    feature. With one: that feature's bundle parts and docs as paths, and its untested requirements, orphans,
    and stranded links."""
    run = run_folder(run_text)
    links = read_json(run / "links.json")
    if not isinstance(links, dict):
        raise RunError("links.json is missing — the link step did not run, so the collection did not finish.")
    rows = links.get("features") or []
    if feature.strip():
        wanted = feature.strip().casefold()
        row = next((r for r in rows if str(r.get("feature") or "").casefold() == wanted), None)
        if row is None:
            names = ", ".join(str(r.get("feature")) for r in rows)
            raise RunError(f"no feature {feature.strip()!r} in this run; its features are: {names}")

        def mine(name: str) -> list[dict[str, Any]]:
            return [entry for entry in links.get(name) or [] if entry.get("feature") == row.get("feature")]

        return {
            "run_dir": str(run),
            "feature": {**{k: row.get(k) for k in ROW_FIELDS},
                        "bundles": [str(run / part) for part in row.get("bundles") or []],
                        "docs": [str(run / doc) for doc in row.get("docs") or []]},
            "untested": mine("untested"),
            "orphans": mine("orphans"),
            "stranded": mine("stranded"),
            "lists_truncated": links.get("lists_truncated", False),
        }
    manifest = read_json(run / "manifest.json") or {}
    return {
        "run_dir": str(run),
        "command": manifest.get("command"),
        "scope": links.get("scope"),
        "lines": links.get("lines") or [],
        "missing": links.get("missing") or [],
        "mismatches": links.get("mismatches") or [],
        "warnings": links.get("warnings") or [],
        "enrichment": links.get("enrichment") or {},
        "counts": links.get("counts") or {},
        "features": [{**{k: r.get(k) for k in ROW_FIELDS}, "parts": len(r.get("bundles") or []),
                      "docs": len(r.get("docs") or [])} for r in rows],
        "unknown_linked_ids": (links.get("unknown_linked_ids") or [])[:20],
        "lists_truncated": links.get("lists_truncated", False),
    }


MAX_SLICE_FEATURES = 5  # feature names a slice's row shows; the rest are counted


def checked_slices(run: Path) -> tuple[set[int], dict[int, int]]:
    """The slices whose reader saved with nothing left rejected — `[]` included — and, in a run matched across slices,
    a subject line for each of their items; and how many findings each saved."""
    checked: set[int] = set()
    found: dict[int, set[str]] = {}
    folder = run / "findings"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = read_json(path)
        if isinstance(data, dict) and isinstance(data.get("slice"), int):
            found.setdefault(data["slice"], set()).update(finding_key(f) for f in data.get("findings") or [])
            if data.get("checked") is True:
                checked.add(data["slice"])
    links = read_json(run / "links.json")
    if isinstance(links, dict) and links.get("subject_lines"):
        missing = missing_lines(run, links.get("slices") or [])
        checked = {n for n in checked if not missing.get(n)}
    return checked, {n: len(keys) for n, keys in found.items()}


MAX_LINE_CHARS = 200  # a subject line names what an item is about, then what it says: one line, never its text


def saved_lines(run: Path) -> dict[str, str]:
    """Every subject line saved in the run, by item key; a line saved again replaces the first."""
    lines: dict[str, str] = {}
    folder = run / "lines"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = read_json(path)
        if isinstance(data, dict) and isinstance(data.get("lines"), dict):
            lines.update({key(rid): str(text) for rid, text in data["lines"].items()})
    return lines


def slice_items(entry: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys([*(entry.get("requirements") or []), *(entry.get("test_cases") or [])]))


def missing_lines(run: Path, slices: list[dict[str, Any]]) -> dict[int, list[str]]:
    """For each slice of requirements and test cases, its items with no subject line yet."""
    saved = saved_lines(run)
    return {n: [rid for rid in slice_items(entry) if key(rid) not in saved]
            for n, entry in enumerate(slices, start=1) if not entry.get("kind")}


def as_case_key(text: Any) -> str:
    """A bare test case number or a C-number, keyed as the run keys test cases: TC-<number>."""
    number = re.fullmatch(r"(?i)(?:tc-?|c)?(\d+)", str(text or "").strip())
    return key(f"TC-{number.group(1)}") if number else ""


def cmd_subjects(run_text: str, number: int, add_text: str) -> dict[str, Any]:
    """Save a slice reader's subject lines — one per requirement and test case: what it is about, then what it says —
    which code matches to find items on the same subject in other slices, or outside the scope."""
    run = run_folder(run_text)
    links = read_json(run / "links.json") or {}
    slices = run_slices(run)
    entry = slice_at(slices, number)
    if not links.get("subject_lines"):
        raise RunError("this run needs no subject lines: it is one slice, with nothing outside it to compare it with.")
    if entry.get("kind") == "pairs":
        raise RunError(f"slice {number} holds pairs: their items' lines were saved with the slices they come from.")
    if (run / "pairs.json").is_file():
        raise RunError("this run is matched already, from the lines saved before: collect a new run to change them.")
    try:
        batch = json.loads(add_text)
    except ValueError as exc:
        raise RunError(f"--add isn't JSON ({exc}). Pass it on one line, with every ' written as \\u0027.") from None
    if not isinstance(batch, dict):
        raise RunError('--add takes one JSON object: {"<ID>": "<subject>: <what it says>", …}')
    items = {key(rid): rid for rid in slice_items(entry)}
    saved: dict[str, str] = {}
    rejected: list[dict[str, str]] = []
    for rid, text in batch.items():
        found = items.get(key(rid)) or items.get(as_case_key(rid))
        line = one_line(text) if isinstance(text, str) else ""
        if found is None:
            rejected.append({"id": str(rid), "problem": f"not in slice {number}: save a line for each ID in its needs_lines"})
        elif not line or len(line) > MAX_LINE_CHARS:
            rejected.append({"id": found, "problem": f"one line of at most {MAX_LINE_CHARS} characters: its subject, "
                                                     "a colon, then what it says"})
        else:
            saved[found] = line
    if saved:
        folder = run / "lines"
        folder.mkdir(mode=0o700, exist_ok=True)
        write_json(folder / f"{time.time_ns():020d}-{os.getpid()}.json",
                   {"slice": number, "saved_at": utc_now(), "lines": saved})
    return {"saved": len(saved), "rejected": rejected, "missing": missing_lines(run, slices).get(number, []),
            "checked": number in checked_slices(run)[0]}


def all_slices(run: Path, links: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's slices: those `link` cut, then the slices of pairs `match` made and the slices of board tickets
    `tickets` made, numbered on from them."""
    later = [read_json(run / name) for name in ("pairs.json", "tickets.json")]
    return [*(links.get("slices") or []), *(entry for made in later if isinstance(made, dict) for entry in made.get("slices") or [])]


def run_slices(run: Path) -> list[dict[str, Any]]:
    links = read_json(run / "links.json")
    if not isinstance(links, dict):
        raise RunError("links.json is missing — the link step did not run, so the collection did not finish.")
    if not isinstance(links.get("slices"), list):
        raise RunError("this run has no slices: only a contradiction run is cut into them, when it is linked.")
    return all_slices(run, links)


def slice_at(slices: list[dict[str, Any]], number: int) -> dict[str, Any]:
    if not 1 <= number <= len(slices):
        raise RunError(f"no slice {number}: this run has {len(slices)}")
    return slices[number - 1]


def cmd_slices(run_text: str, number: int = 0) -> dict[str, Any]:
    """The run's slices, each small enough for one reader to check at once, and whether each is checked — or one
    slice's bundle parts and docs, as paths."""
    run = run_folder(run_text)
    slices = run_slices(run)
    checked, counts = checked_slices(run)

    def row(n: int, entry: dict[str, Any]) -> dict[str, Any]:
        names = entry.get("features") or []
        found = {"slice": n, "features": names[:MAX_SLICE_FEATURES]}
        if len(names) > MAX_SLICE_FEATURES:
            found["more_features"] = len(names) - MAX_SLICE_FEATURES
        return {**found, "requirements": len(entry.get("requirements") or []), "test_cases": len(entry.get("test_cases") or []),
                "parts": len(entry.get("parts") or []), "docs": len(entry.get("docs") or []),
                "kb": round(int(entry.get("bytes") or 0) / 1000), "checked": n in checked, "findings": counts.get(n, 0),
                **({entry["kind"]: len(entry.get(entry["kind"]) or [])} if entry.get("kind") else {})}

    if number:
        entry = slice_at(slices, number)
        answer = {"run_dir": str(run), **row(number, entry), "of": len(slices),
                  "parts": [str(run / part) for part in entry.get("parts") or []],
                  "docs": [str(run / doc) for doc in entry.get("docs") or []]}
        if not entry.get("kind"):
            links = read_json(run / "links.json") or {}
            answer["needs_lines"] = missing_lines(run, slices).get(number, []) if links.get("subject_lines") else []
        else:
            answer["needs_lines"] = []
        return answer
    rows = [row(n, entry) for n, entry in enumerate(slices, start=1)]
    return {"run_dir": str(run), "total": len(rows), "checked": sum(1 for r in rows if r["checked"]), "slices": rows}


SUBJECT_WEIGHT = 3  # the words before a subject line's colon name what the item is about, so they count most
SAME_SUBJECT = 0.25  # how alike two items' words must be, from 0 to 1, for them to be on the same subject
MATCHES_PER_ITEM = 3  # each item is paired with at most its closest few on the same subject
PAIRS_INTRO = (
    "Each pair is two items on the same subject from different parts of the product. Compare the two with each other, "
    "and nothing else. A test case comes with the requirements it tests, to tell which side is right.\n"
    "Requirement and test case text below is verbatim from the source.\n\n"
)


def subject_vector(subject: Any, said: Any) -> dict[str, float]:
    """An item's words, the ones that name its subject counting SUBJECT_WEIGHT times the rest."""
    vector: dict[str, float] = {}
    for weight, text in ((1.0, said), (float(SUBJECT_WEIGHT), subject)):
        found, joined = analyze(text)
        for term in found + joined:
            vector[term] = max(vector.get(term, 0.0), weight)
    return vector


def line_vector(line: str) -> dict[str, float]:
    subject, colon, said = line.partition(":")
    return subject_vector(subject, said) if colon else subject_vector("", line)


def closest(vectors: list[dict[str, float]], queries: list[int], allowed: Any) -> dict[int, list[int]]:
    """For each query, the items most like it, at most MATCHES_PER_ITEM above SAME_SUBJECT: the cosine of their words,
    each weighted by how rare it is, where a word either item names as its subject counts as a subject word for both.
    A word more than half the items use names no subject."""
    total = len(vectors)
    seen = Counter(term for vector in vectors for term in vector)
    rarity = {term: math.log(total / n) for term, n in seen.items() if n <= COMMON * total}
    kept = [{term: weight for term, weight in vector.items() if term in rarity} for vector in vectors]
    norms = [math.sqrt(sum((weight * rarity[term]) ** 2 for term, weight in vector.items())) or 1.0 for vector in kept]
    postings: dict[str, list[tuple[int, float]]] = {}
    for i, vector in enumerate(kept):
        for term, weight in vector.items():
            postings.setdefault(term, []).append((i, weight))
    found: dict[int, list[int]] = {}
    for i in queries:
        shared: dict[int, float] = {}
        for term, weight in kept[i].items():
            for j, other in postings[term]:
                if j != i:
                    shared[j] = shared.get(j, 0.0) + (max(weight, other) * rarity[term]) ** 2
        scores = {j: min(1.0, s / (norms[i] * norms[j])) for j, s in shared.items()}
        alike = sorted((j for j, s in scores.items() if s >= SAME_SUBJECT and allowed(i, j)), key=lambda j: (-scores[j], j))
        found[i] = alike[:MATCHES_PER_ITEM]
    return found


def clear_later_slices(run: Path) -> None:
    """What `match` and `tickets` made goes with the slices it was made from."""
    for kind in ("pairs", "tickets"):
        (run / f"{kind}.json").unlink(missing_ok=True)
        for old in (run / kind).glob("*.md") if (run / kind).is_dir() else []:
            old.unlink()


def own_words_vector(item: dict[str, Any]) -> dict[str, float]:
    """An item no reader wrote a subject line for, by its own words: its title names the subject."""
    expected = [step.get("expected") for step in (item.get("raw_fields") or {}).get("steps") or [] if isinstance(step, dict)]
    return subject_vector(item.get("title"), flat_text([item.get("description"), *expected]))


def write_unit_slices(run: Path, units: list[dict[str, Any]], texts: dict[str, str], first: int, kind: str, title: str,
                      intro: str, requirements: dict[str, Any], cases: dict[str, Any]) -> list[tuple[list[int], dict[str, Any]]]:
    """Units — each a heading, then the items it shows — cut into slices of their own, numbered on from `first`, each
    written to `<kind>/` in parts small enough for one read, an item shown once a slice. Each slice's units and entry."""
    sizes = {**{doc: file_bytes(run, [doc]) for unit in units for doc in unit["docs"]},
             **{rid: text_bytes(text) for rid, text in texts.items()}}
    weights = [(text_bytes(u["head"]), u["show"] + u["docs"]) for u in units]
    groups = split_evenly(weights, sizes, SLICE_BYTES) if units else []
    made = []
    (run / kind).mkdir(mode=0o700, exist_ok=True)
    for number, group in enumerate(groups, start=first):
        blocks: list[tuple[str, str]] = []
        shown: dict[str, str] = {}
        for unit in (units[i] for i in group):
            blocks.append((unit["head"], ""))
            for rid in unit["show"]:
                if rid in shown:
                    blocks.append((f"### {rid} — shown above, {shown[rid]}\n\n", unit["restart"]))
                else:
                    shown[rid] = unit["label"]
                    blocks.append((texts[rid], unit["restart"]))
        bodies = pack_blocks(blocks)
        names = []
        for p, body in enumerate(bodies, start=1):
            heading = f"# Slice {number}: {title}" + (f" — part {p} of {len(bodies)}" if len(bodies) > 1 else "")
            names.append(f"{kind}/{number:02d}-{p}.md")
            write_private(run / names[-1], f"{heading}\n{intro}{body}".rstrip() + "\n")
        members = list(dict.fromkeys(rid for i in group for rid in units[i]["members"]))
        made.append((group, {
            "kind": kind,
            "features": list(dict.fromkeys(item_feature(rid, requirements, cases) for rid in members)),
            "parts": names, "docs": list(dict.fromkeys(doc for i in group for doc in units[i]["docs"])),
            "requirements": [rid for rid in members if find_item(rid, requirements, cases)[0] == "requirement"],
            "test_cases": [rid for rid in members if find_item(rid, requirements, cases)[0] == "test case"],
            "bytes": file_bytes(run, names),
        }))
    return made


def item_text(rid: str, requirements: dict[str, Any], cases: dict[str, Any], docs_by_req: dict[str, list[dict[str, Any]]],
              missing_by_req: dict[str, list[dict[str, Any]]]) -> str:
    kind, item = find_item(rid, requirements, cases)
    if kind == "requirement":
        return "\n".join([f"### {title_of(item)}", *requirement_body(item, docs_by_req, missing_by_req)]).rstrip() + "\n\n"
    return case_block(item)


def item_feature(rid: str, requirements: dict[str, Any], cases: dict[str, Any]) -> str:
    return str((find_item(rid, requirements, cases)[1] or {}).get("feature") or NO_FEATURE)


def pairs_answer(feature_slices: int, pairs: dict[str, Any]) -> dict[str, Any]:
    made = pairs.get("slices") or []
    return {"pairs": int(pairs.get("pairs") or 0), "slices": list(range(feature_slices + 1, feature_slices + len(made) + 1)),
            "total": feature_slices + len(made)}


def cmd_match(run_text: str) -> dict[str, Any]:
    """Pair the items that share a subject but no slice — and, in a run on part of the product, its items with the rest
    of the product — by their subject lines, and cut the pairs into slices of their own, checked like the others."""
    run = run_folder(run_text)
    run_slices(run)
    links = read_json(run / "links.json")
    feature_slices = links.get("slices") or []
    if not links.get("subject_lines"):
        return {"pairs": 0, "slices": [], "total": len(feature_slices),
                "note": "one slice, with nothing outside it to compare it with: there is nothing to match"}
    existing = read_json(run / "pairs.json")
    if isinstance(existing, dict):
        return pairs_answer(len(feature_slices), existing)
    checked = checked_slices(run)[0]
    unchecked = [str(n) for n in range(1, len(feature_slices) + 1) if n not in checked]
    if unchecked:
        raise RunError(f"check {'slice' if len(unchecked) == 1 else 'slices'} {and_list(unchecked)} first: every item "
                       "needs its subject line before items can be matched.")
    requirements, cases = run_items(run)
    lines = saved_lines(run)
    where: dict[str, set[int]] = {}
    items: list[str] = []  # the items to match: the scope's, by their subject lines, then the rest of the product's
    for n, entry in enumerate(feature_slices, start=1):
        for rid in slice_items(entry):
            if key(rid) not in where and key(rid) in lines:
                items.append(rid)
            where.setdefault(key(rid), set()).add(n)
    vectors = [line_vector(lines[key(rid)]) for rid in items]
    in_scope = len(items)
    if links.get("outside"):  # the rest of the product, matched by its own words: its titles name the subject
        for item in [*requirements.values(), *cases.values()]:
            if str(item.get("id") or "") and key(item.get("id")) not in where:
                items.append(str(item["id"]))
                vectors.append(own_words_vector(item))

    def allowed(i: int, j: int) -> bool:
        return j >= in_scope or not where[key(items[i])] & where[key(items[j])]

    best = closest(vectors, list(range(in_scope)), allowed)
    matched = sorted({(min(i, j), max(i, j)) for i, alike in best.items() for j in alike})
    docs_by_req, missing_by_req = doc_index(read_json(run / "docs.json") or {})
    units, texts = [], {}
    for n, (i, j) in enumerate(matched, start=1):
        ids = [items[i], items[j]]
        context: list[str] = []
        for rid in ids:
            kind, item = find_item(rid, requirements, cases)
            for link in tokens(item.get("linked_ids")) if kind == "test case" else []:
                tested = requirements.get(key(link))
                if tested is not None and str(tested.get("id")) not in ids + context:
                    context.append(str(tested.get("id")))
        head = f"## Pair {n}: {ids[0]} ↔ {ids[1]}\n"
        said = [f'{rid} "{lines[key(rid)]}"' for rid in ids if key(rid) in lines and key(rid) in where]
        if said:
            head += f"Subject lines: {' · '.join(said)}\n"
        if context:
            head += f"Also here, to tell which side is right: {', '.join(context)} — what the pair's test cases test.\n"
        for rid in ids + context:
            texts.setdefault(rid, item_text(rid, requirements, cases, docs_by_req, missing_by_req))
        docs = feature_docs([requirements[key(rid)] for rid in ids + context if key(rid) in requirements], docs_by_req)
        units.append({"head": head + "\n", "show": ids + context, "members": ids, "docs": docs,
                      "label": f"in pair {n}", "restart": f"## Pair {n} (continued)\n\n"})
    made = []
    for group, entry in write_unit_slices(run, units, texts, len(feature_slices) + 1, "pairs",
                                          "items on the same subject, in pairs", PAIRS_INTRO, requirements, cases):
        entry["pairs"] = [units[i]["members"] for i in group]
        made.append(entry)
    record = {"matched_at": utc_now(), "pairs": len(units), "slices": made}
    write_json(run / "pairs.json", record)
    return pairs_answer(len(feature_slices), record)


TICKETS_INTRO = (
    "Each ticket is work in progress on the board; its own text is in your board read, not here. Compare each ticket with "
    "the requirements and test cases under it, and nothing else: the ones it links to, and the ones on its subject.\n"
    "Requirement and test case text below is verbatim from the source.\n\n"
)


def saved_tickets(run: Path) -> dict[str, dict[str, Any]]:
    """The board tickets saved in the run, by key."""
    data = read_json(run / "tickets.json")
    found = data.get("tickets") if isinstance(data, dict) else None
    return {key(t.get("id")): t for t in found or [] if isinstance(t, dict)}


def check_ticket(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """One board ticket as the session saves it — key, title, link, linked IDs, subject line — or what is wrong with it."""
    if not isinstance(raw, dict):
        return None, 'each ticket is {"id": …, "title": …, "url": …, "linked_ids": […], "line": …}'
    tid, title = one_line(raw.get("id")), one_line(raw.get("title"))
    line = one_line(raw.get("line")) if isinstance(raw.get("line"), str) else ""
    if not tid or not title:
        return None, "id and title: the ticket's key and title, as the board shows them"
    if not line or len(line) > MAX_LINE_CHARS:
        return None, f"line: one line of at most {MAX_LINE_CHARS} characters: its subject, a colon, then what it will change"
    url = str(raw.get("url") or "").strip()
    linked = raw.get("linked_ids")
    return {"id": tid, "title": title, "url": url if url.startswith(("https://", "http://")) else "",
            "linked_ids": tokens(linked) if isinstance(linked, (list, str)) else [], "line": line}, ""


def cmd_tickets(run_text: str, add_text: str) -> dict[str, Any]:
    """Save the board tickets the session read, each with its subject line; find for each the requirements it links to
    and the requirements and test cases on its subject; and cut them into slices of their own, which the session checks:
    only it holds the tickets' text."""
    run = run_folder(run_text)
    run_slices(run)
    links = read_json(run / "links.json")
    if (run / "tickets.json").is_file():
        raise RunError("the tickets are saved already, and their slices cut: collect a new run to change them.")
    if links.get("subject_lines") and not (run / "pairs.json").is_file():
        raise RunError("run match first: the tickets' slices are numbered after the slices of pairs.")
    try:
        batch = json.loads(add_text)
    except ValueError as exc:
        raise RunError(f"--add isn't JSON ({exc}). Pass it on one line, with every ' written as \\u0027.") from None
    batch = [batch] if isinstance(batch, dict) else batch
    if not isinstance(batch, list) or not batch:
        raise RunError("--add takes the board tickets, as a JSON list: with no ticket, there is nothing to save.")
    tickets: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for n, raw in enumerate(batch, start=1):
        ticket, problem = check_ticket(raw)
        if ticket and any(key(t["id"]) == key(ticket["id"]) for t in tickets):
            ticket, problem = None, f"{ticket['id']} is listed twice"
        if ticket:
            tickets.append(ticket)
        else:
            rejected.append({"ticket": n, "id": str(raw.get("id") if isinstance(raw, dict) else ""), "problem": problem})
    if rejected:  # all or nothing: the slices are cut once, from every ticket
        return {"saved": 0, "rejected": rejected}
    requirements, cases = run_items(run)
    lines = saved_lines(run)
    items = [*requirements.values(), *cases.values()]
    kinds = ["requirement"] * len(requirements) + ["test case"] * len(cases)
    vectors = [line_vector(lines[key(i.get("id"))]) if key(i.get("id")) in lines else own_words_vector(i) for i in items]
    vectors += [line_vector(t["line"]) for t in tickets]
    queries = list(range(len(items), len(vectors)))
    alike = {kind: closest(vectors, queries, lambda i, j, kind=kind: j < len(items) and kinds[j] == kind)
             for kind in ("requirement", "test case")}
    docs_by_req, missing_by_req = doc_index(read_json(run / "docs.json") or {})
    units, texts, alone = [], {}, []
    for q, ticket in zip(queries, tickets):
        linked = list(dict.fromkeys(str(found["id"]) for found in (find_item(rid, requirements, cases)[1] for rid in ticket["linked_ids"])
                                    if found is not None))
        near = [str(items[j]["id"]) for kind in ("requirement", "test case") for j in alike[kind][q]]
        near = [rid for rid in near if rid not in linked]
        ticket["compare"] = linked + near
        if not ticket["compare"]:
            alone.append(ticket["id"])
            continue
        said = [f"{', '.join(linked)} (it links to {'it' if len(linked) == 1 else 'them'})" if linked else "",
                f"{', '.join(near)} (on its subject)" if near else ""]
        head = (f"## {ticket['id']} — {ticket['title']}\n" + (f"Link: {ticket['url']}\n" if ticket["url"] else "")
                + f"Subject line: \"{ticket['line']}\"\nCompare with: {' · '.join(s for s in said if s)}\n\n")
        for rid in ticket["compare"]:
            texts.setdefault(rid, item_text(rid, requirements, cases, docs_by_req, missing_by_req))
        docs = feature_docs([requirements[key(rid)] for rid in ticket["compare"] if key(rid) in requirements], docs_by_req)
        units.append({"ticket": ticket["id"], "head": head, "show": ticket["compare"], "members": ticket["compare"],
                      "docs": docs, "label": f"with {ticket['id']}", "restart": f"## {ticket['id']} (continued)\n\n"})
    first = len(all_slices(run, links)) + 1
    made = []
    for group, entry in write_unit_slices(run, units, texts, first, "tickets", "board tickets, each with what it is "
                                          "compared with", TICKETS_INTRO, requirements, cases):
        entry["tickets"] = [units[i]["ticket"] for i in group]
        made.append(entry)
    write_json(run / "tickets.json", {"saved_at": utc_now(), "tickets": tickets, "alone": alone, "slices": made})
    return {"saved": len(tickets), "rejected": [], "alone": alone,
            "slices": list(range(first, first + len(made))), "total": first - 1 + len(made)}


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
            background = read_json(folder / "background.json")
            if (config.get("requirements") or {}).get("provider") == "jama" and background is None:
                continue  # a lookup on Jama searches the rest of the project too
            if verify_run(config, manifest, read_json(folder / "requirements.json"), read_json(folder / "test_cases.json"), background):
                continue
        except (RunError, ValueError, TypeError):
            continue
        return {"run_dir": str(folder), "age_minutes": int(age), "lines": links["lines"], "counts": links.get("counts")}
    return {"run_dir": None, "reason": f"no complete, verified run of the whole scope in the last {max_age_minutes} minutes"}


# ---------------------------------------------------------------------------
# findings, report — /bc:contradiction's findings, checked against the run, and the report written from them
# ---------------------------------------------------------------------------

SEVERITIES = ("critical", "high", "medium", "low")
SEVERITY_MEANS = {
    "critical": "a broken product would still pass the test",
    "high": "the items ask for different results in the same situation",
    "medium": "they may conflict, and a person decides",
    "low": "a test case doesn't really test its requirement",
}
BOARD = "board ticket"
ITEM_TYPES = (BOARD, "requirement", "test case")  # the order a finding's kind names them in
DECIDE = "decide"
MAX_FINDING_ITEMS = 8
MAX_QUOTE_CHARS = 500  # a phrase or a sentence: enough to find the place, and a report part stays one read
MAX_WHY_CHARS = 600
CLOSEST_RATIO = 0.5  # a sentence this much like a quote that isn't in an item is offered as the likely fix
BUNDLE_PREFIX = re.compile(r"^(?:\d+\. )?(?:expected(?: result)?: ?)?")  # what a bundle prints before a step's text
LABELLED = re.compile(r"([A-Z][A-Za-z ]{2,30}): ")  # a description paragraph such as "Preconditions: device paired"
LOOKALIKES = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'", "\u00b4": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
})
WRAPPING = "\"'\u201c\u201d\u2018\u2019 \t\r\n"


def folded(text: str) -> tuple[str, list[int]]:
    """Text as a quote is checked against it — case, spacing, and the style of quote marks and dashes set aside —
    and, for each of its characters, the position it came from in the original."""
    chars: list[str] = []
    origin: list[int] = []
    for position, char in enumerate(text):
        for piece in unicodedata.normalize("NFKC", char).translate(LOOKALIKES).casefold():
            if piece.isspace():
                if chars and chars[-1] != " ":
                    chars.append(" ")
                    origin.append(position)
            else:
                chars.append(piece)
                origin.append(position)
    if chars and chars[-1] == " ":
        chars.pop()
        origin.pop()
    return "".join(chars), origin


def quote_fragments(quote: str) -> tuple[list[str], bool, bool]:
    """A quote's folded fragments — "…" marks words left out — and whether it starts and ends mid-text. A step
    number or "Expected:" copied from a bundle along with the words is set aside."""
    text = BUNDLE_PREFIX.sub("", folded(str(quote).strip(WRAPPING))[0], count=1)
    fragments = [piece.strip() for piece in text.split("...") if piece.strip()]
    return fragments, text.startswith("..."), text.endswith("...")


def find_words(text: str, fragment: str, start: int) -> int:
    """Where a fragment starts in folded text, at or after start, on word edges: "30 mins" is not in "130 mins"."""
    while True:
        at = text.find(fragment, start)
        if at < 0:
            return -1
        end = at + len(fragment)
        clean_start = at == 0 or not (text[at - 1].isalnum() and fragment[0].isalnum())
        clean_end = end == len(text) or not (text[end].isalnum() and fragment[-1].isalnum())
        if clean_start and clean_end:
            return at
        start = at + 1


def description_spots(description: Any) -> list[tuple[str, Any]]:
    """A test case's description, one spot per paragraph, each named by its label: "Preconditions: …" is the
    preconditions."""
    spots: list[tuple[str, Any]] = []
    for paragraph in re.split(r"\n\s*\n", str(description or "")):
        label = LABELLED.match(paragraph.strip())
        spots.append((label.group(1).casefold() if label else "description", paragraph))
    return spots


def spots_of(item: dict[str, Any], kind: str) -> list[tuple[str, str]]:
    """An item's text, field by field, each with the name a finding gives its place: "step 3, expected result".
    The title comes last, so words it shares with the body are placed in the body."""
    raw = item.get("raw_fields") or {}
    spots: list[tuple[str, Any]]
    if kind == "requirement":
        spots = [("description", item.get("description")), ("rationale", item.get("rationale")),
                 ("status", item.get("status"))]
        spots += [(label, flat_text(value)) for label, value in raw.items() if label not in BOOKKEEPING]
    else:
        spots = description_spots(item.get("description"))
        for n, step in enumerate(raw.get("steps") or [], start=1):
            if isinstance(step, dict):
                spots += [(f"step {n}", step.get("content")), (f"step {n}, expected result", step.get("expected"))]
        spots += [(label, flat_text(value)) for label, value in (raw.get("custom") or {}).items()]
    spots.append(("title", item.get("title")))
    return [(label, str(text)) for label, text in spots if str(text or "").strip()]


def match_quote(quote: str, spots: list[tuple[str, str]]) -> dict[str, str] | None:
    """Where in an item a quote is, and its words as the item has them — or None when it isn't there."""
    fragments, opens_mid, ends_mid = quote_fragments(quote)
    if not fragments:
        return None
    for label, text in spots:
        flat, origin = folded(text)
        spans: list[tuple[int, int]] = []
        start = 0
        for fragment in fragments:
            at = find_words(flat, fragment, start)
            if at < 0:
                break
            spans.append((at, at + len(fragment)))
            start = at + len(fragment)
        if len(spans) == len(fragments):
            words_ = " … ".join(one_line(text[origin[a]:origin[b - 1] + 1]) for a, b in spans)
            return {"where": label, "quote": f"{'…' if opens_mid else ''}{words_}{'…' if ends_mid else ''}"}
    return None


def likeness(wanted: str, flat: str) -> float:
    """How alike a quote is to a sentence, or to the run of words in it the quote's length."""
    found, size = flat.split(), max(1, len(wanted.split()))
    windows = [flat] if len(found) <= size + 2 else [" ".join(found[i:i + size]) for i in range(len(found) - size + 1)]
    return max(difflib.SequenceMatcher(None, wanted, window).ratio() for window in windows)


def closest_text(quote: str, spots: list[tuple[str, str]]) -> str:
    """The sentence of an item most like a quote that isn't in it, so the quote can be corrected without a re-read."""
    wanted = " ".join(quote_fragments(quote)[0])
    best, score = "", CLOSEST_RATIO
    for label, text in spots:
        for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
            flat = folded(sentence)[0]
            ratio = likeness(wanted, flat) if flat and wanted else 0.0
            if ratio > score:
                best, score = f'{label}: "{one_line(sentence)[:240]}"', ratio
    return best


def run_items(run: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    requirements: dict[str, dict[str, Any]] = {}
    for item in requirement_list(read_json(run / "requirements.json")):
        requirements.setdefault(key(item.get("id")), item)
    for item in requirement_list(read_json(run / "product.json")):  # a run on part of the product: the rest of it
        requirements.setdefault(key(item.get("id")), item)
    cases: dict[str, dict[str, Any]] = {}
    for item in case_list(read_json(run / "test_cases.json")):
        cases.setdefault(key(item.get("id")), item)
    return requirements, cases


def find_item(rid: str, requirements: dict[str, Any], cases: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    if key(rid) in requirements:
        return "requirement", requirements[key(rid)]
    found = cases.get(key(rid)) or cases.get(as_case_key(rid))
    return ("test case", found) if found else ("", None)


def outside_items(run: Path, requirements: dict[str, Any], cases: dict[str, Any]) -> dict[str, str]:
    """In a run on part of the product, the items from the rest of it, by key, with the feature each is in."""
    links = read_json(run / "links.json") or {}
    if not links.get("outside"):
        return {}
    inside = {key(rid) for entry in links.get("slices") or [] for rid in slice_items(entry)}
    return {k: str(item.get("feature") or NO_FEATURE) for k, item in [*requirements.items(), *cases.items()] if k not in inside}


def check_item(entry: Any, severity: str, requirements: dict[str, Any], cases: dict[str, Any],
               elsewhere: dict[str, str], tickets: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """One side of a finding, looked up in the run with its quote found — or the reason it can't be."""
    if not isinstance(entry, dict) or not str(entry.get("id") or "").strip():
        return None, 'each item is {"id": …, "quote": …}'
    rid = str(entry["id"]).strip()
    quote = str(entry.get("quote") or "").strip()
    if not quote and severity != "low":
        return None, f"{rid}: quote its exact words"
    if len(quote) > MAX_QUOTE_CHARS:
        return None, f"{rid}: quote a phrase or a sentence, at most {MAX_QUOTE_CHARS} characters — not the whole text"
    kind, item = find_item(rid, requirements, cases)
    ticket = tickets.get(key(rid)) if item is None else None
    if ticket is not None:  # its text is in the session's board read, not the run: its quote can't be checked
        return {"id": ticket["id"], "type": BOARD, "title": ticket["title"], "url": ticket["url"], "where": "",
                "quote": one_line(quote), "checked": False}, ""
    if item is None:
        return None, f"{rid} is not in this run: check the ID. A board ticket is in it once saved with `tickets`."
    found = {"id": str(item.get("id")), "type": kind, "title": one_line(item.get("title")),
             "url": str((item.get("raw_fields") or {}).get("url") or ""), "where": "", "quote": "", "checked": True}
    if quote:
        spots = spots_of(item, kind)
        match = match_quote(quote, spots)
        if match is None:
            near = closest_text(quote, spots)
            hint = f" The closest is {near}." if near else ""
            return None, f'{found["id"]}: "{one_line(quote)[:240]}" is not in its text.{hint}'
        found.update(match)
    if key(found["id"]) in elsewhere:
        found["outside"] = elsewhere[key(found["id"])]
    return found, ""


def kind_of(items: list[dict[str, Any]]) -> str:
    types = [t for t in ITEM_TYPES if any(i["type"] == t for i in items)]
    if len({key(i["id"]) for i in items}) == 1:
        return f"{types[0]} contradicts itself"
    return f"{types[0]} ↔ {types[0]}" if len(types) == 1 else " ↔ ".join(types)


def fix_of(value: Any, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The item a finding says to change, at the place of its first quote — or "decide"."""
    text = str((value.get("id") if isinstance(value, dict) else value) or "").strip()
    if key(text) == DECIDE:
        return {"decide": True}
    number = re.fullmatch(r"(?i)(?:tc-?|c)?(\d+)", text)
    wanted = {key(text), key(f"TC-{number.group(1)}") if number else ""} - {""}
    target = next((i for i in items if key(i["id"]) in wanted), None)
    if target is None:
        return None
    return {"id": target["id"], "type": target["type"], "where": target["where"]}


def check_finding(raw: Any, requirements: dict[str, Any], cases: dict[str, Any], elsewhere: dict[str, str],
                  tickets: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(raw, dict):
        return None, ["a finding is a JSON object: items, severity, why, fix"]
    problems: list[str] = []
    severity = str(raw.get("severity") or "").strip().casefold()
    if severity not in SEVERITIES:
        problems.append(f"severity: one of {', '.join(SEVERITIES)}")
    entries = raw.get("items") if isinstance(raw.get("items"), list) else []
    if not 2 <= len(entries) <= MAX_FINDING_ITEMS:
        problems.append(f"items: 2 to {MAX_FINDING_ITEMS} — each side, or two places in an item that contradicts itself")
    items = []
    for entry in entries[:MAX_FINDING_ITEMS]:
        item, problem = check_item(entry, severity, requirements, cases, elsewhere, tickets)
        if item:
            items.append(item)
        else:
            problems.append(problem)
    why = one_line(raw.get("why"))
    if not why or len(why) > MAX_WHY_CHARS:
        problems.append(f"why: one plain sentence on why they conflict, at most {MAX_WHY_CHARS} characters")
    ships = list(dict.fromkeys(i["id"] for i in items if i["type"] == BOARD))
    wanted = raw.get("fix")
    named = [fix_of(value, items) for value in (wanted if isinstance(wanted, list) else [wanted])]
    fix = named[0] if len(named) == 1 else None
    if ships and named and all((found or {}).get("type") == "test case" for found in named):
        # A ticket hasn't shipped: the test cases it will break are updated when it does, never offered as fixes now.
        fix = {"update": list({found["id"]: {"id": found["id"], "where": found["where"]} for found in named}.values()),
               "ships": ships}
    elif isinstance(wanted, list) and not problems:
        problems.append("fix: a list names only test cases among the items, the ones the ticket will break" if ships else
                        "fix: one ID, or \"decide\" — a list of test cases is for a board ticket's finding")
        fix = {}
    if fix is None and not problems:
        ids = ", ".join(dict.fromkeys(i["id"] for i in items))
        problems.append(f'fix: the ID of the item to change ({ids}), or "decide" when a person must choose')
    elif (fix or {}).get("type") == "test case" and not problems and not any(i["type"] == "requirement" for i in items):
        # The fix offer rewrites the test case to agree with a requirement, so one must be among the items.
        problems.append(f'fix: add the requirement words {fix["id"]} breaks to items, or write "decide" when no '
                        'requirement settles it')
    if problems:
        return None, problems
    return {"severity": severity, "kind": kind_of(items), "items": items, "why": why, "fix": fix}, []


def finding_key(finding: dict[str, Any]) -> str:
    """The same sides and quotes are the same finding: saving it again replaces it."""
    return json.dumps(sorted([key(i.get("id")), folded(i.get("quote") or "")[0]] for i in finding.get("items") or []))


def saved_findings(run: Path) -> list[dict[str, Any]]:
    """Every finding saved in the run, in the order first saved; a finding saved again keeps its place."""
    found: list[dict[str, Any]] = []
    place: dict[str, int] = {}
    folder = run / "findings"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = read_json(path)
        for finding in (data.get("findings") or []) if isinstance(data, dict) else []:
            name = finding_key(finding)
            if name in place:
                found[place[name]] = finding
            else:
                place[name] = len(found)
                found.append(finding)
    return found


def on_board(finding: dict[str, Any]) -> bool:
    return any(i.get("type") == BOARD for i in finding.get("items") or [])


def numbered(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The findings in report order — by severity, board tickets last — numbered from 1."""
    ordered = sorted(findings, key=lambda f: (on_board(f), SEVERITIES.index(f["severity"])))
    return [{"n": n, **finding} for n, finding in enumerate(ordered, start=1)]


def fixes_a_test_case(finding: dict[str, Any]) -> bool:
    """A conflict whose fix is a test case, every quote checked: what the fix offer covers."""
    return (finding["severity"] != "low" and (finding.get("fix") or {}).get("type") == "test case"
            and not on_board(finding) and all(i.get("checked") for i in finding["items"]))


def cmd_findings(run_text: str, add_text: str = "", numbers_text: str = "", slice_number: int = 0) -> dict[str, Any]:
    run = run_folder(run_text)
    if slice_number:
        slice_at(run_slices(run), slice_number)
        if not add_text.strip():
            raise RunError("--slice comes with --add: the slice's findings, or [] when it has none.")
    if add_text.strip():
        try:
            batch = json.loads(add_text)
        except ValueError as exc:
            raise RunError(f"--add isn't JSON ({exc}). Pass it on one line, with every ' written as \\u0027.") from None
        if isinstance(batch, dict):
            batch = batch["findings"] if isinstance(batch.get("findings"), list) else [batch]
        if not isinstance(batch, list) or not (batch or slice_number):
            raise RunError("--add takes a finding, or a list of them.")
        if read_json(run / "requirements.json") is None:
            raise RunError("requirements.json is missing — collect the run before saving findings.")
        requirements, cases = run_items(run)
        elsewhere = outside_items(run, requirements, cases)
        tickets = saved_tickets(run)
        saved: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for n, raw in enumerate(batch, start=1):
            finding, problems = check_finding(raw, requirements, cases, elsewhere, tickets)
            if finding:
                saved.append(finding)
            else:
                entries = raw.get("items") if isinstance(raw, dict) and isinstance(raw.get("items"), list) else []
                rejected.append({"finding": n, "ids": [str(e.get("id")) for e in entries if isinstance(e, dict)],
                                 "problems": problems})
        record: dict[str, Any] = {"saved_at": utc_now(), "findings": saved}
        if slice_number:
            # A slice is checked once a save leaves nothing to fix or drop: [] when it holds no conflict.
            record.update({"slice": slice_number, "checked": not rejected})
        if saved or record.get("checked"):
            folder = run / "findings"
            folder.mkdir(mode=0o700, exist_ok=True)
            write_json(folder / f"{time.time_ns():020d}-{os.getpid()}.json", record)
        answer: dict[str, Any] = {"saved": len(saved), "rejected": rejected, "total": len(saved_findings(run))}
        if slice_number:
            answer["checked"] = slice_number in checked_slices(run)[0]
        return answer
    findings = numbered(saved_findings(run))
    if numbers_text.strip():
        wanted = [int(n) for n in re.findall(r"\d+", numbers_text)]
        by_number = {f["n"]: f for f in findings}
        return {"findings": [by_number[n] for n in wanted if n in by_number],
                "not_found": [n for n in wanted if n not in by_number]}
    return {
        "total": len(findings),
        "findings": [{"n": f["n"], "severity": f["severity"], "kind": f["kind"],
                      "ids": list(dict.fromkeys(i["id"] for i in f["items"])), "fix": fix_sentence(f["fix"])}
                     for f in findings],
        "fix_test_cases": [f["n"] for f in findings if fixes_a_test_case(f)],
    }


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def link_text(title: str, url: str) -> str:
    title = one_line(title).replace("[", "\\[").replace("]", "\\]")
    url = str(url or "").strip()
    if url.startswith(("https://", "http://")):
        return f"[{title or 'open'}]({url.replace(' ', '%20').replace(')', '%29')})"
    return title


def fix_sentence(fix: dict[str, Any]) -> str:
    if fix.get("decide"):
        return "a person decides which side is right."
    if fix.get("update"):
        cases = [f"{c['id']} ({c['where']})" if c.get("where") else c["id"] for c in fix["update"]]
        return f"update {and_list(cases)} when {and_list(fix['ships'])} {'ships' if len(fix['ships']) == 1 else 'ship'}."
    return f"change {fix['id']}{', ' + fix['where'] if fix.get('where') else ''}."


def finding_block(finding: dict[str, Any], severity_in_heading: bool = False) -> str:
    kind = finding["kind"]
    heading = f"{finding['severity'].capitalize()}: {kind}" if severity_in_heading else kind[:1].upper() + kind[1:]
    lines = [f"### {finding['n']}. {heading}"]
    named: set[str] = set()
    for item in finding["items"]:
        text = f"**{item['id']}**"
        if key(item["id"]) not in named:
            named.add(key(item["id"]))
            title = link_text(item.get("title") or "", item.get("url") or "")
            text += f" {title}" if title else ""
            if item.get("outside"):
                text += f" _(in {item['outside']}, outside the scope)_"
        if item.get("where"):
            text += f" — {item['where']}"
        if item.get("quote"):
            text += f': "{item["quote"]}"'
            if not item.get("checked"):
                text += " _(not checked: the ticket's text isn't in the run)_"
        lines.append(f"- {text}")
    lines += [f"- **Why:** {finding['why']}", f"- **Fix:** {fix_sentence(finding['fix'])}"]
    return "\n".join(lines) + "\n\n"


def links_line(counts: dict[str, Any]) -> str:
    untested, orphans = int(counts.get("untested") or 0), int(counts.get("orphans") or 0)
    if not untested and not orphans:
        return "**Links:** every requirement has a test case, and every test case has a requirement."
    parts = []
    if untested:
        parts.append(f"{plural(untested, 'requirement')} {'has' if untested == 1 else 'have'} no test case")
    if orphans:
        parts.append(f"{plural(orphans, 'test case')} {'has' if orphans == 1 else 'have'} no requirement")
    return f"**Links:** {' and '.join(parts)}. `/bc:coverage` lists and repairs them."


def and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def ships_blocks(findings: list[dict[str, Any]], more: str) -> list[tuple[str, str]]:
    """For each ticket, the test cases its findings say to update when it ships."""
    tickets: dict[str, dict[str, Any]] = {}
    for finding in findings:
        fix = finding.get("fix") or {}
        for tid in fix.get("ships") or []:
            item = next(i for i in finding["items"] if i["type"] == BOARD and i["id"] == tid)
            entry = tickets.setdefault(key(tid), {"item": item, "cases": [], "findings": []})
            entry["cases"] += [c["id"] for c in fix["update"] if c["id"] not in entry["cases"]]
            entry["findings"].append(str(finding["n"]))
    heading = "## Test cases to update when the tickets ship"
    blocks = []
    for n, entry in enumerate(tickets.values()):
        item, numbers = entry["item"], entry["findings"]
        line = (f"- **{item['id']}** {link_text(item['title'], item['url'])}: {and_list(entry['cases'])} — "
                f"{'finding' if len(numbers) == 1 else 'findings'} {and_list(numbers)}.\n")
        blocks.append(((f"{heading}\n\n" if n == 0 else "") + line, f"{more}{heading} (continued)\n\n"))
    if blocks:
        blocks[-1] = (blocks[-1][0] + "\n", blocks[-1][1])
    return blocks


def report_blocks(links: dict[str, Any], extra: list[str], partial: str, findings: list[dict[str, Any]],
                  unchecked: list[int], total_slices: int, pairs: int | None, tickets: int) -> list[tuple[str, str]]:
    scope = str((links.get("scope") or {}).get("input") or "").strip()
    title = f"# Contradictions — {scope}" if scope and scope.casefold() != "all" else "# Contradictions — the whole project"
    more = f"{title} (continued)\n\n"
    lines = list(links.get("lines") or [])
    lines += [line for line in (one_line(x) for x in extra) if line and line not in lines]
    head = [title, ""]
    if lines:
        head += ["```text", *lines, "```", ""]
    missing = [*(links.get("missing") or []), *([one_line(partial)] if one_line(partial) else [])]
    if missing:
        head += [f"**PARTIAL** — not read: {'; '.join(missing)}.", ""]
    if unchecked:
        one = len(unchecked) == 1
        head += [f"**Not checked:** {'slice' if one else 'slices'} {and_list([str(n) for n in unchecked])} of "
                 f"{total_slices}, so any conflicts in {'it' if one else 'them'} are missing from "
                 "this report.", ""]
    if links.get("subject_lines") and pairs is None:
        head += ["**Not checked:** items in different parts of the product were never compared with each other, so "
                 "conflicts between them are missing from this report.", ""]
    counts = links.get("counts") or {}
    tally = ", ".join(f"{sum(1 for f in findings if f['severity'] == s)} {s}" for s in SEVERITIES)
    also = [*([f"{plural(pairs, 'pair')} of items on the same subject across the product"] if pairs else []),
            *([plural(tickets, "board ticket")] if tickets else [])]
    head += [
        f"**Checked:** {plural(int(counts.get('requirements') or 0), 'requirement')} and "
        f"{plural(int(counts.get('test_cases_in_scope') or 0), 'test case')}"
        f"{''.join(f', {x}' for x in also[:-1])}{f', and {also[-1]}' if also else ''}.  ",
        f"**Found:** {tally}.  " if findings else "**Found:** no contradictions.  ",
        links_line(counts),
    ]
    blocks = [("\n".join(head) + "\n\n", "")]
    sections = [(f"## {s.capitalize()} — {SEVERITY_MEANS[s]}", [f for f in findings if not on_board(f) and f["severity"] == s], False)
                for s in SEVERITIES]
    sections.append(("## Board tickets — work in progress that conflicts with a requirement or a test case",
                     [f for f in findings if on_board(f)], True))
    for heading, group, severity_in_heading in sections:
        for n, finding in enumerate(group):
            block = finding_block(finding, severity_in_heading)
            blocks.append(((f"{heading}\n\n" if n == 0 else "") + block, f"{more}{heading} (continued)\n\n"))
    blocks += ships_blocks(findings, more)
    offered = [str(f["n"]) for f in findings if fixes_a_test_case(f)]
    if offered:
        verb = "names" if len(offered) == 1 else "name"
        blocks.append((f"## Test cases to fix\n\n{plural(len(offered), 'finding')} {verb} a test case to change: "
                       f"{', '.join(offered)}.\n", more))
    return blocks


def cmd_report(run_text: str, lines_text: str = "", partial: str = "") -> dict[str, Any]:
    """The contradiction report, written by code from the saved findings, in parts small enough for one read."""
    run = run_folder(run_text)
    links = read_json(run / "links.json")
    if not isinstance(links, dict):
        raise RunError("links.json is missing — the link step did not run, so there is no run to report on.")
    extra: Any = []
    if lines_text.strip():
        try:
            extra = json.loads(lines_text)
        except ValueError as exc:
            raise RunError(f"--lines isn't JSON ({exc}). Pass a list on one line, with every ' written as \\u0027.") from None
    if not isinstance(extra, list) or not all(isinstance(line, str) for line in extra):
        raise RunError("--lines takes a JSON list of lines.")
    findings = numbered(saved_findings(run))
    checked = checked_slices(run)[0]
    slices = all_slices(run, links)
    unchecked = [n for n in range(1, len(slices) + 1) if n not in checked]
    matched = read_json(run / "pairs.json")
    parts = pack_blocks(report_blocks(links, extra, partial, findings, unchecked, len(slices),
                                      int(matched.get("pairs") or 0) if isinstance(matched, dict) else None,
                                      len(saved_tickets(run))))
    for old in run.glob("report-*.md"):
        old.unlink()
    paths = []
    for n, text in enumerate(parts, start=1):
        write_private(run / f"report-{n}.md", text)
        paths.append(str(run / f"report-{n}.md"))
    return {
        "parts": paths,
        "findings": {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES},
        "fix_test_cases": [f["n"] for f in findings if fixes_a_test_case(f)],
        "unchecked": unchecked,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# progress, note, archive — the onboarding and V&V logs beside the settings
# ---------------------------------------------------------------------------

LOGS = {
    "onboarding": {"file": "onboarding-progress.json", "notes": "onboarding-notes.md", "stamp": "last_updated",
                   "items": ("features", "name")},
    "vnv": {"file": "vnv-progress.json", "notes": None, "stamp": "updated_at", "items": ("stories", "dev_key")},
}


def project_folder(project_text: str) -> Path:
    folder = Path(project_text).expanduser().resolve() / ".buddy-council"
    if not folder.is_dir():
        raise RunError(f"{folder.parent} has no .buddy-council folder — run /bc:setup in this project first.")
    return folder


def parse_patch(text: str) -> dict[str, Any]:
    try:
        patch = json.loads(text)
    except ValueError as exc:
        raise RunError(f"--patch isn't JSON ({exc}). Pass it on one line, with every ' written as \\u0027.") from None
    if not isinstance(patch, dict):
        raise RunError("--patch takes a JSON object.")
    return patch


def merge_log(log: dict[str, Any], patch: dict[str, Any], items: tuple[str, str]) -> dict[str, Any]:
    """Merge a patch into a progress log. The log's own list merges item by item, matched on its key field;
    everything else merges like the settings: objects key by key, anything else replaces."""
    name, field = items
    for key, value in patch.items():
        if key == name and isinstance(value, list) and all(isinstance(v, dict) and field in v for v in value):
            existing = [item for item in log.get(key) or [] if isinstance(item, dict)]
            by_key = {item.get(field): item for item in existing}
            for item in value:
                if item[field] in by_key:
                    merge(by_key[item[field]], item)
                else:
                    copy_ = json.loads(json.dumps(item))
                    existing.append(copy_)
                    by_key[item[field]] = copy_
            log[key] = existing
        elif isinstance(value, dict) and isinstance(log.get(key), dict):
            merge(log[key], value)
        else:
            log[key] = json.loads(json.dumps(value))
    return log


def cmd_progress(project_text: str, which: str, patch_text: str) -> dict[str, Any]:
    spec = LOGS[which]
    folder = project_folder(project_text)
    path = folder / spec["file"]
    if path.exists():
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            raise RunError(f"{path.name} isn't valid JSON, so it was left as it is: repair it, or archive it and "
                           "start again.") from None
        if not isinstance(log, dict):
            raise RunError(f"{path.name} doesn't hold a progress log, so it was left as it is.")
    else:
        log = {"version": 1}
    patch = parse_patch(patch_text)
    merge_log(log, patch, spec["items"])
    if spec["stamp"] not in patch:
        log[spec["stamp"]] = utc_now()
    exclude_from_git(folder.parent)
    write_json(path, log)
    name, _ = spec["items"]
    return {"log": str(path), "saved": sorted(patch), name: len(log.get(name) or [])}


def cmd_note(project_text: str, title: str, text: str) -> dict[str, Any]:
    folder = project_folder(project_text)
    path = folder / LOGS["onboarding"]["notes"]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"## {stamp} — {' '.join(title.split())}\n\n{text.strip()}\n\n---\n\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return {"notes": str(path)}


def cmd_archive(project_text: str, which: str) -> dict[str, Any]:
    spec = LOGS[which]
    folder = project_folder(project_text)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    archived = []
    for name in (spec["file"], spec["notes"]):
        if name and (folder / name).exists():
            stem, dot, ext = name.rpartition(".")
            target = folder / f"{stem}.{stamp}.{ext}"
            os.replace(folder / name, target)
            archived.append(str(target))
    return {"archived": archived}


def cmd_doc(url: str, max_chars: int = MAX_DOC_CHARS, runner: Any = subprocess.run) -> str:
    """One GitHub doc as Markdown: a comment naming it and the sign-in that read it, then the doc verbatim."""
    doc, reader, _ = read_doc(url, runner)
    content = doc["content"]
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[...truncated, see {url} for full document]"
    images = referenced_images(doc["content"], {**doc["locator"], "host": doc["locator"].get("host", "github.com")})
    head = {"url": url, "read_with": reader, "locator": doc["locator"], "raw_size": len(doc["content"]),
            "truncated": len(doc["content"]) > max_chars, "referenced_images": images}
    if doc.get("note"):
        head["note"] = doc["note"]
    return f"<!-- {json.dumps(head, ensure_ascii=False)} -->\n{content}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buddy-Council run folders and progress logs.")
    sub = parser.add_subparsers(dest="action", required=True)
    new = sub.add_parser("new", help="create a run folder and prune old ones")
    new.add_argument("--project", required=True, help="absolute path of the project folder that holds .buddy-council")
    new.add_argument("--command", dest="command_name", required=True, help="the analysis command, e.g. coverage")
    new.add_argument("--scope", default="", help="the scope the run analyzes, as the user gave it")
    for name, text in (("enrich", "fetch the GitHub docs the requirements link to"), ("link", "cross-link and write links.json and the bundles")):
        sub.add_parser(name, help=text).add_argument("--run", required=True, help="the run folder")
    search = sub.add_parser("search", help="rank the run's requirements, test cases, and docs against an ask")
    search.add_argument("--run", required=True)
    search.add_argument("--query", required=True, help="the ask, as the user put it")
    search.add_argument("--concept", action="append", default=[], help="one idea of the ask: 'own words|other wording|...'")
    search.add_argument("--feature", default="", help="only requirements in folders whose path contains these words")
    search.add_argument("--top", type=int, default=SEARCH_TOP, help="requirements written out in full (5-60)")
    show = sub.add_parser("show", help="print requirements, background items, and test cases by ID, as Markdown")
    show.add_argument("--run", required=True)
    show.add_argument("--ids", required=True)
    summary = sub.add_parser("summary", help="what to check and print from a run, small enough to read at once")
    summary.add_argument("--run", required=True)
    summary.add_argument("--feature", default="", help="one feature: its bundle parts, docs, and lists")
    recent = sub.add_parser("recent", help="the newest complete, verified run of the whole scope")
    recent.add_argument("--project", required=True)
    recent.add_argument("--max-age-minutes", type=int, default=RECENT_MINUTES)
    doc = sub.add_parser("doc", help="print one GitHub doc, read with the first sign-in that works")
    doc.add_argument("--url", required=True)
    doc.add_argument("--max-chars", type=int, default=MAX_DOC_CHARS)
    progress = sub.add_parser("progress", help="merge a change into the onboarding or V&V progress log")
    progress.add_argument("--project", required=True)
    progress.add_argument("--log", required=True, choices=sorted(LOGS))
    progress.add_argument("--patch", required=True, help="JSON on one line; write every ' as \\u0027")
    note = sub.add_parser("note", help="append one entry to the onboarding notes")
    note.add_argument("--project", required=True)
    note.add_argument("--title", required=True)
    note.add_argument("--text", required=True)
    archive = sub.add_parser("archive", help="set a progress log aside under a dated name")
    archive.add_argument("--project", required=True)
    archive.add_argument("--log", required=True, choices=sorted(LOGS))
    findings = sub.add_parser("findings", help="check findings against the run and save them, or print the saved ones")
    findings.add_argument("--run", required=True)
    findings.add_argument("--add", default="", help="a finding or a list of them, JSON on one line; write every ' as \\u0027")
    findings.add_argument("--numbers", default="", help="print these findings in full, by their numbers in the report")
    findings.add_argument("--slice", type=int, default=0, help="the slice these findings come from: a save that leaves "
                                                               "nothing rejected, --add '[]' included, marks it checked")
    slices = sub.add_parser("slices", help="the run's slices, each small enough for one reader, and which are checked")
    slices.add_argument("--run", required=True)
    slices.add_argument("--slice", type=int, default=0, help="one slice: its bundle parts and docs, as paths")
    subjects = sub.add_parser("subjects", help="save a slice reader's subject lines: what each item is about, and what it says")
    subjects.add_argument("--run", required=True)
    subjects.add_argument("--slice", type=int, required=True, help="the slice the lines come from")
    subjects.add_argument("--add", required=True, help='{"<ID>": "<subject>: <what it says>", …} as JSON on one line; '
                                                       "write every ' as \\u0027")
    matcher = sub.add_parser("match", help="pair the items on the same subject in different slices, or outside the scope")
    matcher.add_argument("--run", required=True)
    tickets = sub.add_parser("tickets", help="save the board tickets, match each with what is on its subject, cut their slices")
    tickets.add_argument("--run", required=True)
    tickets.add_argument("--add", required=True, help='[{"id", "title", "url", "linked_ids", "line"}, …] as JSON on one line; '
                                                      "write every ' as \\u0027")
    report = sub.add_parser("report", help="write the contradiction report from the saved findings")
    report.add_argument("--run", required=True)
    report.add_argument("--lines", default="", help="more lines for its top, as a JSON list: the board's and the settings'")
    report.add_argument("--partial", default="", help="a source the user chose to go on without")
    args = parser.parse_args(argv)
    try:
        if args.action == "show":
            print(cmd_show(args.run, args.ids), end="")
            return 0
        if args.action == "doc":
            print(cmd_doc(args.url, args.max_chars))
            return 0
        if args.action == "new":
            result = cmd_new(args.project, args.command_name, args.scope)
        elif args.action == "enrich":
            result = cmd_enrich(args.run)
        elif args.action == "search":
            result = cmd_search(args.run, args.query, args.concept, args.feature, args.top)
        elif args.action == "recent":
            result = cmd_recent(args.project, args.max_age_minutes)
        elif args.action == "summary":
            result = cmd_summary(args.run, args.feature)
        elif args.action == "progress":
            result = cmd_progress(args.project, args.log, args.patch)
        elif args.action == "note":
            result = cmd_note(args.project, args.title, args.text)
        elif args.action == "archive":
            result = cmd_archive(args.project, args.log)
        elif args.action == "findings":
            result = cmd_findings(args.run, args.add, args.numbers, args.slice)
        elif args.action == "slices":
            result = cmd_slices(args.run, args.slice)
        elif args.action == "subjects":
            result = cmd_subjects(args.run, args.slice, args.add)
        elif args.action == "match":
            result = cmd_match(args.run)
        elif args.action == "tickets":
            result = cmd_tickets(args.run, args.add)
        elif args.action == "report":
            result = cmd_report(args.run, args.lines, args.partial)
        else:
            result = cmd_link(args.run)
    except (RunError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
