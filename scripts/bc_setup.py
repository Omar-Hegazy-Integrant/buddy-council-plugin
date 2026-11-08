#!/usr/bin/env python3
"""A project folder's settings, .buddy-council/sources.json — the only code that saves them. Standard library only;
Python 3.9+.

The settings belong to a project folder: the nearest folder at or above the current one that holds .buddy-council/
(never the key store's own ~/.buddy-council). They name the links a project uses and what setup worked out from them,
and hold no key: keys belong to sites (bc_keys.py, ADR 0008).

  status  [--from DIR] [--need TOOLS] [--reads TOOLS]
                                          the project folder, each tool's state, what a command prints, asks for,
                                          or stops on, and the checks a re-run of setup makes
  links   --link URL [--link URL]...       what each link names — the tool, the site, the ids in it — and whether
                                          this computer has the site's key, with what to ask for when it hasn't
  save    --project DIR [--patch JSON] [--unset PATH]...
                                          merge a change into the settings, check it against
                                          config/sources.example.json, and save it privately, out of git
  migrate                                 move the keys older setups saved into one file per site, and list what
                                          else they left behind
  cleanup --verified SITES                remove what older setups left, for the sites that answered this run
  forget  --url URL [--from DIR]          delete a site's key (never auto-approved, so it always asks first)
  github  --url URL [--move-old-token yes]
                                          read one GitHub document with the first sign-in that works; with
                                          --move-old-token, the last resort, an older setup's token becomes the key

Structured data travels as one single-quoted argument: --patch takes JSON on one line, with every ' written as
\\u0027, so the command stays one plain line the bash hook can approve. A patch merges into the settings: objects
merge key by key, and anything else — a list, a number, null — replaces what was there. --unset removes a setting by
its dotted path, before the patch is merged, so a tool's block can be replaced whole.

config/sources.example.json lists every setting the plugin reads, and a save is refused when it would hold anything
else. In the example, a "*" key stands for any key, shaped like its value.

Every command prints one JSON object and never prints a key.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bc_keys  # noqa: E402
import bc_run  # noqa: E402

PLUGIN = HERE.parent
EXAMPLE = PLUGIN / "config" / "sources.example.json"
FOLDER = ".buddy-council"
SETTINGS = "sources.json"
FORMAT = 2  # the settings format; 0.28.0 is the first to stamp it
CLOUD_JIRA = (".atlassian.net", ".jira.com")
JIRA_KEY = r"[A-Z][A-Z0-9_]+"

# The tools a project can use. Requirements are required; the others can be "none" (ADR 0009).
CODE_MARKERS = ("package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml",
                "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", ".git")
TOOLS = {
    "requirements": {"block": "requirements", "provider": "jama", "name": "Requirements", "link": "Jama project link",
                     "guide": "providers/jama/setup.md"},
    "test_cases": {"block": "test_cases", "provider": "testrail", "name": "Test cases", "link": "TestRail suite link",
                   "guide": "providers/testrail/setup.md"},
    "board": {"block": "jira", "provider": "jira", "name": "Board", "link": "board link",
              "guide": "providers/jira/setup.md"},
    "vnv_board": {"block": "jira", "provider": "jira", "name": "V&V board", "link": "V&V board link",
                  "guide": "providers/jira/setup.md"},
}
# What to ask for when a site has no key on this computer, and the fields its key file holds.
KEY_WORDS = {
    "testrail": ("the email you sign in to TestRail with, and an API key (TestRail → My Settings → API Keys → Add Key)",
                 ["username", "api_key"]),
    "jama": ("a Jama API client ID and client secret (Jama → your profile menu → Set API Credentials → Create)",
             ["client_id", "client_secret"]),
    "jira-cloud": ("the email you sign in to Atlassian with, and an API token "
                   "(https://id.atlassian.com/manage-profile/security/api-tokens)", ["username", "api_token"]),
    "jira-server": ("a personal access token (Jira → your profile menu → Personal Access Tokens → Create token)",
                    ["personal_token"]),
    "github": ("a read-only GitHub token: a fine-grained personal access token with Contents: read on the "
               "repositories the documents live in", ["token"]),
}
CHOICES = {
    "requirements.provider": ("jama",),
    "requirements.feature_inference.strategy": ("hierarchical_folder",),
    "requirements.enrichment.strategy": ("cli", "mcp"),
    "test_cases.provider": ("testrail", "none"),
    "test_cases.authoring.section_strategy": ("linked", "feature", "fixed"),
    "jira.provider": ("jira", "none"),
    "jira.deployment": ("cloud", "server"),
    "jira.vnv_board.discriminator.kind": ("label", "component", "issue_type", "sprint"),
    "jira.vnv_board.discriminator.confidence": ("sampled", "stated"),
    "automation.status": ("ready", "pending"),
}


UNVERIFIED = {
    "requirements": "Requirements (Jama) aren't verified yet. Run /bc:setup to check them again.",
    "test_cases": "Test cases (TestRail) aren't verified yet. Run /bc:setup to check them again.",
    "board": "The Jira board isn't verified yet. Run /bc:setup to check it again.",
    "vnv_board": "The Jira board isn't verified yet. Run /bc:setup to check it again.",
}


class SetupError(Exception):
    """A problem worded for the person who has to fix it. It never carries a key."""


# ---------------------------------------------------------------------------
# The project folder
# ---------------------------------------------------------------------------


def key_store() -> Path:
    return bc_keys.bc_home().expanduser().resolve()


def find_project(start: Any = None) -> tuple[Path, str]:
    """The project folder for `start`: the nearest folder at or above it holding .buddy-council/, else `start`.

    The key store's own ~/.buddy-council never counts. Returns the folder and where it was found: "here", "above",
    or "new" (no settings anywhere above, so they will be created in `start`).
    """
    begin = Path(start or os.getcwd()).expanduser().resolve()
    store = key_store()
    for folder in (begin, *begin.parents):
        candidate = folder / FOLDER
        if candidate.is_dir() and candidate.resolve() != store:
            return folder, ("here" if folder == begin else "above")
    return begin, "new"


def settings_path(project: Path) -> Path:
    return project / FOLDER / SETTINGS


def is_key_store(project: Path) -> bool:
    return (project / FOLDER).resolve() == key_store()


def load(project: Path) -> dict | None:
    path = settings_path(project)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise SetupError(f"{path} isn't valid JSON ({exc}); fix or remove it, then run /bc:setup again.") from None
    if not isinstance(data, dict):
        raise SetupError(f"{path} doesn't hold settings; fix or remove it, then run /bc:setup again.")
    return data


# ---------------------------------------------------------------------------
# What a project's settings say
# ---------------------------------------------------------------------------


def deployment_of(url: Any) -> str:
    host = (urllib.parse.urlsplit(str(url or "") if "://" in str(url or "") else f"https://{url}").hostname or "")
    return "cloud" if host.lower().endswith(CLOUD_JIRA) else "server"


def _block(settings: dict, name: str) -> dict | None:
    block = settings.get(name)
    return block if isinstance(block, dict) else None


def tool_state(settings: dict, tool: str) -> dict:
    """One tool's state: ready, pending (saved, not verified), none (not used, on purpose), missing (never set up),
    or excel (requirements from before Jama was the only source). Ready and pending ones carry their site."""
    spec = TOOLS[tool]
    block = _block(settings, spec["block"])
    if block is None:
        return {"state": "missing"}
    provider = block.get("provider", spec["provider"] if tool in ("board", "vnv_board") else None)
    if tool == "requirements" and provider == "excel":
        return {"state": "excel"}
    if provider == "none" and tool != "requirements":
        return {"state": "none"}
    if provider != spec["provider"] or not block.get("base_url"):
        return {"state": "missing"}
    if tool == "board" and not isinstance(block.get("board"), dict):
        return {"state": "missing"}
    if tool == "vnv_board":
        vnv = block.get("vnv_board")
        if not isinstance(vnv, dict) or not vnv.get("id"):
            return {"state": "missing"}
    url = str(block["base_url"])
    try:
        site = bc_keys.site_id(bc_keys.base_url(spec["provider"], url))
        checked = bc_keys.check(url, spec["provider"])
    except bc_keys.KeyStoreError:
        site, checked = url, {"ok": False}
    state = {"state": "pending" if block.get("pending") else "ready", "provider": spec["provider"], "url": url,
             "site": site, "key": checked["ok"]}
    if checked.get("user"):
        state["account"] = checked["user"]  # who the key signs in as: Jira Cloud's user_identifier, never a secret
    return state


def readiness(settings: dict, states: dict, need: list[str], reads: list[str]) -> tuple[str | None, list[dict], list[str]]:
    """What a command does before it reads anything: stop, ask for a missing link or key on the spot, or print a
    line in its report for each tool it reads without. `need` is what the command can't run without, `reads` what
    it reads when the project has it; every command needs requirements, and a tool in neither is left alone."""
    requirements = states["requirements"]
    if requirements["state"] == "excel":
        return "Requirements come from Jama only now. Run /bc:setup with a link to your Jama project.", [], []
    if requirements["state"] == "missing":
        return "Requirements aren't set up here. Run /bc:setup with a link to your Jama project.", [], []
    used = ["requirements", *need, *(tool for tool in reads if tool not in need)]
    for tool in used:
        if states[tool]["state"] == "pending" and (tool in ("requirements", "test_cases") or tool in need):
            return UNVERIFIED[tool], [], []
    ask: list[dict] = []
    lines: list[str] = []
    for tool in ("test_cases", "board", "vnv_board"):
        if tool not in used:
            continue
        state = states[tool]["state"]
        spec = TOOLS[tool]
        if tool in need and state in ("none", "missing"):
            ask.append({"tool": tool, "need": "link", "provider": spec["provider"], "words": f"your {spec['link']}",
                        "guide": spec["guide"]})
        elif tool == "vnv_board":
            continue
        elif state == "none":
            them = "them" if tool == "test_cases" else "it"
            lines.append(f"{spec['name']}: not used in this project. Add {them} with /bc:setup <{spec['link']}>.")
        elif state == "missing":
            them = "them" if tool == "test_cases" else "it"
            lines.append(f"{spec['name']}: not set up. Add {them} with /bc:setup <{spec['link']}>.")
        elif state == "pending":
            lines.append(f"{spec['name']}: not verified yet, so it is skipped. Run /bc:setup to check it again.")
    for tool in used:
        info = states[tool]
        if info["state"] == "ready" and not info["key"] and not any(a["tool"] == tool for a in ask):
            if tool == "vnv_board" and "board" in used and states["board"]["state"] == "ready":
                continue  # the same Jira site: asked for once, under the board
            words, fields = key_words(info["provider"], info["url"])
            ask.append({"tool": tool, "need": "key", "provider": info["provider"], "site": info["site"],
                        "url": info["url"], "words": words, "fields": fields, "guide": TOOLS[tool]["guide"]})
    return None, ask, lines


def checks(settings: dict, states: dict) -> list[dict]:
    """What a re-check checks, in order: each tool the project uses through its guide's Re-check, the GitHub link
    when enrichment is on, and the Plugin Tools when the settings have them."""
    found = []
    for tool, guide in (("requirements", "providers/jama/setup.md"), ("test_cases", "providers/testrail/setup.md"),
                        ("board", "providers/jira/setup.md")):
        if states[tool]["state"] in ("ready", "pending"):
            found.append({"check": tool, "site": states[tool]["site"], "state": states[tool]["state"],
                          "guide": f"{guide} → Re-check"})
    enrichment = (_block(settings, "requirements") or {}).get("enrichment")
    if isinstance(enrichment, dict) and enrichment.get("enabled"):
        found.append({"check": "github", "strategy": enrichment.get("strategy") or "cli",
                      "url": enrichment.get("check_url"), "guide": "providers/github/setup.md → Re-check"})
    automation = _block(settings, "automation")
    if automation and automation.get("enabled"):
        found.append({"check": "automation", "guide": "howtos/automation-tools.md → After the save → On a re-check"})
    return found


def code_markers(project: Path) -> list[str]:
    """What says the project folder holds code, for code mapping: the markers present at its top."""
    found = [name for name in CODE_MARKERS if (project / name).exists()]
    with contextlib.suppress(OSError):
        found += sorted(path.name for path in project.glob("*.csproj"))[:1]
    return found


def status(start: Any = None, need: list[str] | None = None, reads: list[str] | None = None) -> dict:
    need = [tool for tool in (need or []) if tool != "requirements"]
    reads = [tool for tool in (reads or []) if tool != "requirements"]
    for option, tools in (("--need", need), ("--reads", reads)):
        unknown = [tool for tool in tools if tool not in TOOLS]
        if unknown:
            raise SetupError(f"{option} takes {', '.join(TOOLS)}, not {', '.join(unknown)}.")
    project, found = find_project(start)
    path = settings_path(project)
    result: dict[str, Any] = {"ok": True, "project": str(project), "found": found, "settings": str(path),
                              "exists": path.is_file(), "keys": len(bc_keys.sites()), "legacy": bool(legacy_items()),
                              "code": code_markers(project)}
    if is_key_store(project):
        return {**result, "stop": "Run this inside your project folder: your home folder's .buddy-council holds the "
                                  "keys, not a project's settings.", "ask": [], "lines": []}
    plugin = PLUGIN.resolve()
    if found == "new" and (project == plugin or plugin in project.parents):
        return {**result, "found": "plugin", "update": False, "tools": {tool: {"state": "missing"} for tool in TOOLS},
                "stop": f"This ran in the plugin's own folder ({plugin}), which is never a project. Run it again with "
                        "--from and the folder you started in.", "ask": [], "lines": []}
    settings = load(project)
    if settings is None:
        return {**result, "update": False, "tools": {tool: {"state": "missing"} for tool in TOOLS},
                "stop": "No Buddy-Council settings in this folder or any folder above it. Run /bc:setup here first.",
                "ask": [], "lines": []}
    states = {tool: tool_state(settings, tool) for tool in TOOLS}
    update = settings.get("version") != FORMAT
    stop, ask, lines = readiness(settings, states, need, reads)
    if update and not stop:
        stop = ("This project was set up with an older Buddy-Council. Run /bc:setup once to finish updating — it asks "
                "nothing if everything still connects.")
    return {**result, "update": update, "tools": states, "checks": checks(settings, states), "stop": stop,
            "ask": [] if stop else ask, "lines": lines}


def key_words(provider: str, url: Any) -> tuple[str, list[str]]:
    if provider == "jira":
        return KEY_WORDS["jira-cloud" if deployment_of(url) == "cloud" else "jira-server"]
    return KEY_WORDS[provider]


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def _first(pattern: str, *texts: str, flags: int = 0) -> str | None:
    for text in texts:
        found = re.search(pattern, text or "", flags)
        if found:
            return found.group(1)
    return None


def recognize(link: Any) -> dict:
    """What a pasted link names: the tool, the site's address, and the ids in it. Never asks the network."""
    text = str(link or "").strip().strip("<>").strip()
    result: dict[str, Any] = {"link": text, "provider": None}
    if not text:
        return {**result, "problem": "no link was given"}
    full = text if "://" in text else "https://" + text
    try:
        parts = urllib.parse.urlsplit(full)
        host = (parts.hostname or "").lower()
    except ValueError:
        return {**result, "problem": f"{text} isn't a link"}
    if not host:
        return {**result, "problem": f"{text} isn't a link"}
    path, query, fragment = parts.path or "", parts.query or "", parts.fragment or ""
    if re.search(r"/perspective\.req", path, re.I) or host.endswith(".jamacloud.com"):
        provider = "jama"
    elif re.search(r"/index\.php", path, re.I) or "testrail" in host:
        provider = "testrail"
    elif (re.search(r"/secure/RapidBoard\.jspa|/jira/(?:software|core|servicedesk)/", path, re.I)
          or re.search(rf"/browse/{JIRA_KEY}-\d+", path) or host.endswith(CLOUD_JIRA)):
        provider = "jira"
    elif bc_keys.github_host(host):
        provider = "github"
    elif "jama" in host:
        provider = "jama"
    elif "jira" in host:
        provider = "jira"
    else:
        return {**result, "problem": f"{host} isn't a Jama, TestRail, Jira, or GitHub address this plugin recognizes"}
    try:
        base = bc_keys.base_url(provider, full)
    except bc_keys.KeyStoreError as exc:
        return {**result, "provider": provider, "problem": str(exc)}
    found: dict[str, Any] = {"provider": provider, "base_url": base, "site": bc_keys.site_id(base)}
    if provider == "jama":
        project = _first(r"[?&#]projectId=(\d+)", "?" + query, "#" + fragment) or _first(r"/projects/(\d+)", fragment, path)
        item = _first(r"/(?:items|containers)/(\d+)", fragment, path) or _first(r"[?&]docId=(\d+)", "?" + query)
        found.update(project_id=int(project) if project else None, item_id=int(item) if item else None)
    elif provider == "testrail":
        target = urllib.parse.unquote(query.split("&", 1)[0])
        suite = _first(r"^/suites/(?:view|overview|edit)/(\d+)", target)
        project = _first(r"^/projects/(?:overview|view|edit)/(\d+)", target)
        case = _first(r"^/cases/(?:view|edit)/(\d+)", target)
        found.update(suite_id=int(suite) if suite else None, project_id=int(project) if project else None,
                     case_id=int(case) if case else None)
    elif provider == "jira":
        board = _first(r"/boards/(\d+)", path) or _first(r"(?:^|&)rapidView=(\d+)", query)
        key = (_first(rf"/projects/({JIRA_KEY})(?:/|$)", path) or _first(rf"(?:^|&)projectKey=({JIRA_KEY})(?:&|$)", query)
               or _first(rf"/browse/({JIRA_KEY})-\d+", path))
        issue = _first(rf"/browse/({JIRA_KEY}-\d+)", path) or _first(rf"(?:^|&)selectedIssue=({JIRA_KEY}-\d+)", query)
        found.update(deployment=deployment_of(base), board_id=int(board) if board else None, project_key=key,
                     issue_key=issue)
    else:
        pieces = [piece for piece in path.split("/") if piece]
        found.update(owner=pieces[0] if len(pieces) > 0 else None, repo=pieces[1] if len(pieces) > 1 else None)
    found = {name: value for name, value in found.items() if value is not None}
    check = bc_keys.check(base, provider)
    words, fields = key_words(provider, base)
    if check["ok"]:
        found["key"] = {"saved": True, "auth": check["auth"], **({"user": check["user"]} if "user" in check else {}),
                        "ask": words, "fields": fields}  # for replacing a key the site refuses
    else:
        found["key"] = {"saved": False, "ask": words, "fields": fields}
        if not check.get("problem", "").startswith("no "):
            found["key"]["problem"] = check["problem"]
    return {**result, **found}


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def remove(settings: dict, dotted: str) -> None:
    *parents, last = [piece for piece in dotted.split(".") if piece]
    node: Any = settings
    for piece in parents:
        node = node.get(piece) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node.pop(last, None)


def normalize(settings: dict) -> list[str]:
    """Bring settings from older setups to this format. Returns what changed, in words for the recap."""
    changes = []
    if "plugin_root" in settings:
        settings.pop("plugin_root")
        changes.append("dropped plugin_root: commands find the plugin themselves")
    for name in ("test_cases", "jira"):
        block = _block(settings, name)
        if block and block.get("provider") == "none" and set(block) != {"provider"}:
            settings[name] = {"provider": "none"}
    jira = _block(settings, "jira")
    if jira and jira.get("provider", "jira") == "jira":
        if "cloud_id" in jira:
            jira.pop("cloud_id")
            changes.append("dropped jira.cloud_id: every Jira call names its site")
        if jira.get("base_url") and not jira.get("deployment"):
            jira["deployment"] = deployment_of(jira["base_url"])
            changes.append(f"jira.deployment: {jira['deployment']}, from the site's address")
    if settings.get("version") != FORMAT:
        settings["version"] = FORMAT
        changes.append(f"settings format {FORMAT}")
    return changes


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return "null"


def _check(value: Any, example: Any, where: str, problems: list[str]) -> None:
    if value is None or example is None:
        return
    if _kind(value) != _kind(example):
        problems.append(f"{where} should be {_kind(example)}, not {_kind(value)}")
        return
    if isinstance(example, dict):
        for name, item in value.items():
            here = f"{where}.{name}" if where else name
            if name in example:
                _check(item, example[name], here, problems)
            elif "*" in example:
                _check(item, example["*"], here, problems)
            else:
                problems.append(f"{here} isn't a setting the plugin reads (config/sources.example.json)")
    elif isinstance(example, list) and example:
        for number, item in enumerate(value):
            _check(item, example[0], f"{where}[{number}]", problems)
    choices = CHOICES.get(re.sub(r"\[\d+\]", "", where))
    if choices and value not in choices:
        problems.append(f"{where} is {value!r}; it takes {', '.join(repr(c) for c in choices)}")


def validate(settings: dict, only: set | None = None) -> list[str]:
    """Every problem with these settings: a setting the example lacks, a value of the wrong kind or choice, or two
    settings that contradict each other."""
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    problems: list[str] = []
    for name, value in settings.items():
        if only is not None and name not in only:
            continue
        if name in example:
            _check(value, example[name], name, problems)
        else:
            problems.append(f"{name} isn't a setting the plugin reads (config/sources.example.json)")
    requirements = _block(settings, "requirements")
    if (only is None or "requirements" in only) and requirements is not None:
        if requirements.get("provider") == "excel":
            problems.append("requirements come from Jama only: save a Jama project in place of the Excel file")
        elif not requirements.get("base_url"):
            problems.append("requirements need the Jama site's base_url")
    jira = _block(settings, "jira") or {}
    board, vnv = jira.get("board"), jira.get("vnv_board")
    if isinstance(board, dict) and isinstance(vnv, dict) and board.get("id") and board.get("id") == vnv.get("id"):
        problems.append("jira.vnv_board is the dev board: clones would land on the board they came from")
    return problems


def save(project: Any, patch: dict | None = None, unset: list[str] | None = None, stamp: bool = True) -> dict:
    """Merge a change into a project's settings and save them, or refuse with every problem named.

    `stamp` brings the settings to this format (normalize); a script recording only its own block passes False,
    and then only that block is checked.
    """
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise SetupError(f"{project} isn't a folder.")
    if is_key_store(root):
        raise SetupError("Your home folder's .buddy-council holds the keys; save a project's settings in its own folder.")
    if patch is not None and not isinstance(patch, dict):
        raise SetupError("--patch takes a JSON object.")
    before = load(root) or {}
    after = copy.deepcopy(before)
    for dotted in unset or []:
        remove(after, dotted)
    bc_run.merge(after, patch or {})
    changes = normalize(after) if stamp else []
    only = None if stamp else {name for name in (patch or {})} | {d.split(".")[0] for d in unset or []}
    problems = validate(after, only)
    if problems:
        raise SetupError("Not saved: " + "; ".join(problems) + ".")
    path = settings_path(root)
    path.parent.mkdir(exist_ok=True)
    bc_run.exclude_from_git(root)
    fd, tmp = tempfile.mkstemp(prefix=".sources-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(after, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return {"ok": True, "settings": str(path), "changed": sorted(_changed(before, after)), "updated": changes}


def _changed(before: Any, after: Any, where: str = "") -> set[str]:
    if isinstance(before, dict) or isinstance(after, dict):
        before = before if isinstance(before, dict) else {}
        after = after if isinstance(after, dict) else {}
        found: set[str] = set()
        for name in set(before) | set(after):
            found |= _changed(before.get(name), after.get(name), f"{where}.{name}" if where else name)
        return found
    return set() if before == after else {where}


# ---------------------------------------------------------------------------
# What older setups left behind
# ---------------------------------------------------------------------------


def _home() -> Path:
    return Path.home()


def _under(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def _tilde(path: Path) -> str:
    return bc_keys._tilde(path, _home())


def _old_env() -> Path:
    return _home() / ".buddy-council" / "atlassian.env"


def _entry_words(entry: dict) -> list[str]:
    args = entry.get("args") if isinstance(entry.get("args"), list) else []
    return [word for word in (entry.get("command"), entry.get("cwd"), *args) if isinstance(word, str)]


def _env(entry: dict) -> dict:
    env = entry.get("env")
    return env if isinstance(env, dict) else {}


def _site_of(provider: str, url: Any) -> str | None:
    try:
        return bc_keys.site_id(bc_keys.base_url(provider, url)) if url else None
    except bc_keys.KeyStoreError:
        return None


def _old_entry(name: str, entry: Any, in_bc_folder: bool) -> dict | None:
    """Whether a connector entry is one an older bc setup wrote, and for which provider and site.

    Only bc's own: an entry that runs a server folder of this plugin's, mcp-atlassian started from bc's
    atlassian.env, the GitHub server bc registered with the token bc saved, or appium-mcp from bc's own tools folder.
    """
    if not isinstance(entry, dict):
        return None
    words = _entry_words(entry)
    if name in ("testrail", "jama", "jira") and bc_keys.is_bc_entry(name, entry, in_bc_folder, _home()):
        if name == "jira":
            return {"provider": "jira", "site": _site_of("jira", bc_keys.legacy_jira_url(_home()))}
        saved = next((section["base_url"] for section in bc_keys.legacy_sections(_home())
                      if section["provider"] == name and section["base_url"]), None)
        return {"provider": name, "site": _site_of(name, _env(entry).get(f"{name.upper()}_BASE_URL") or saved)}
    if name == "atlassian":
        for number, word in enumerate(words[:-1]):
            if word == "--env-file" and re.search(r"(?:^|/)\.buddy-council/atlassian\.env$", words[number + 1].strip()):
                return {"provider": "jira", "site": _site_of("jira", bc_keys.legacy_jira_url(_home()))}
    if name == "github" and (in_bc_folder or bc_keys.legacy_entry_uses_github_token(entry, _home())):
        return {"provider": "github", "site": "github.com"}
    if name == "appium" and any("/.buddy-council/tools/" in word for word in words):
        return {"provider": "appium", "site": None}
    return None


def legacy_items() -> list[dict]:
    """Everything an older setup left on this computer, in this home folder: bc's connector entries in Copilot's
    settings and in bc's plugin folders, the sections of the old key file, and the old Jira settings file."""
    home = _home()
    items: list[dict] = []
    copilot = home / ".copilot" / "mcp-config.json"
    configs = [(copilot, False)] + [(folder / ".mcp.json", True) for folder in bc_keys.bc_plugin_folders(home)]
    for path, in_bc_folder in configs:
        if not path.is_file() or not _under(path, home):
            continue
        for name, entry in bc_keys._servers(bc_keys._read_json(path)).items():
            found = _old_entry(name, entry, in_bc_folder)
            if found:
                items.append({"kind": "entry", "path": str(path), "name": name, **found})
    for section in bc_keys.legacy_sections(home):
        name = section["provider"]
        site = "github.com" if name == "github" else _site_of(name, section["base_url"])
        items.append({"kind": "section", "path": section["path"], "name": name, "provider": name,
                      "site": site or _migrated_site(name)})
    if _old_env().is_file():
        site = _site_of("jira", bc_keys.legacy_jira_url(home))
        items.append({"kind": "file", "path": str(_old_env()), "name": "atlassian.env", "provider": "jira", "site": site})
    for item in items:
        if item["provider"] in ("testrail", "jama") and not item["site"]:
            item["site"] = _migrated_site(item["provider"])
    return items


def _migrated_site(provider: str) -> str | None:
    """The site the key migration copied a provider's old key to, from its record."""
    record = bc_keys._read_json(bc_keys.keys_dir() / bc_keys.MARKER)
    if not isinstance(record, dict):
        return None
    for name in ("migrated", "kept"):
        for entry in record.get(name) or []:
            if isinstance(entry, dict) and entry.get("provider") == provider and entry.get("site"):
                return entry["site"]
    return None


def migrate() -> dict:
    """Move the keys older setups saved into one file per site — in code, so no key passes through the assistant —
    and say which sites to check before cleanup removes the rest."""
    report = bc_keys.migrate_legacy()
    record = bc_keys._read_json(bc_keys.keys_dir() / bc_keys.MARKER) or {}
    moved = report["migrated"] if not report["already"] else record.get("migrated") or []
    items = legacy_items()
    verify = sorted({item["site"] for item in items if item["site"] and item["provider"] in ("testrail", "jama", "jira")})
    lines = []
    if moved and not report["already"]:
        names = sorted({bc_keys.NAMES[entry["provider"]] for entry in moved})
        lines.append(f"Keys: moved the {_words(names)} {'key' if len(moved) == 1 else 'keys'} older setups saved into "
                     "one file per site, in ~/.buddy-council/keys")
    skipped = [entry for entry in report["skipped"] if entry.get("provider")]
    for entry in skipped:
        lines.append(f"Keys: the old {bc_keys.NAMES.get(entry['provider'], entry['provider'])} key stays where it is — "
                     f"{entry['reason']}")
    return {"ok": True, "moved": [{k: e[k] for k in ("provider", "site")} for e in moved if isinstance(e, dict)],
            "skipped": skipped, "leftovers": len(items), "verify": verify, "keys": len(bc_keys.sites()), "lines": lines}


def _words(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _write_json_like(path: Path, data: Any) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _removable(item: dict, verified: set) -> str | None:
    """Why an old item can't go yet, or None when it can."""
    provider = item["provider"]
    if provider == "appium":
        return None if (bc_keys.bc_home() / "tools" / "appium-mcp.json").is_file() else \
            "the Plugin Tools haven't been checked under this version yet"
    if provider == "github":
        # No token stays in connector settings, so bc's old entry goes at once. The token stays in the old key file
        # until it becomes the github.com key, the last resort, or the user deletes it.
        if item["kind"] == "entry" or bc_keys.check("https://github.com", "github")["ok"]:
            return None
        return "it becomes the github.com key only if nothing else can read your design documents — delete it there " \
               "if you don't need it"
    if not item.get("site"):
        return "the site it was set up for is unknown"
    if item["site"] not in verified:
        return f"{item['site']} wasn't checked this run"
    if not any(entry["site"] == item["site"] for entry in bc_keys.sites(provider)):
        return f"no key is saved for {item['site']} yet"
    return None


KNOWN_FILES = {"keys", "tools", "automation", "secrets.json", "atlassian.env"}


def cleanup(verified: list[str]) -> dict:
    """Remove what older setups left, for the sites that answered this run with their per-site keys. Files bc didn't
    create are left alone and named once."""
    sites = {bc_keys.site_id(site) for site in verified if site.strip()}
    removed: list[str] = []
    kept: list[dict] = []
    by_file: dict[str, list[dict]] = {}
    for item in legacy_items():
        why = _removable(item, sites)
        if why:
            kept.append({"what": _describe(item), "why": why})
        else:
            by_file.setdefault(item["path"], []).append(item)
    for name, items in by_file.items():
        path = Path(name)
        if items[0]["kind"] == "file":
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            removed.append(_tilde(path))
            continue
        data = bc_keys._read_json(path)
        if not isinstance(data, dict):
            continue
        holder = data.get("mcpServers") if items[0]["kind"] == "entry" else data
        for item in items:
            holder.pop(item["name"], None)
        if items[0]["kind"] == "section":
            names = _words(sorted(bc_keys.NAMES[item["provider"]] for item in items))
            what = f"the old {names} {'key' if len(items) == 1 else 'keys'} in {_tilde(path)}"
        else:
            names = _words(sorted(item["name"] for item in items))
            what = f"bc's old {names} {'entry' if len(items) == 1 else 'entries'} in {_tilde(path)}"
        if not holder and set(data) <= {"mcpServers"} and (items[0]["kind"] == "section" or path.name == ".mcp.json"):
            path.unlink()
            what += f", and the file, which held nothing else"
        else:
            _write_json_like(path, data)
        removed.append(what)
    kept = _once([f"Kept {item['what']}: {item['why']}" for item in kept], "kept")
    left = _once([f"~/.buddy-council/{name}" for name in _unknown_files()], "left_alone")
    lines = [f"Removed {item}." for item in removed] + kept
    if left:
        lines.append(f"Left alone, because bc didn't create {'it' if len(left) == 1 else 'them'}: {', '.join(left)}.")
    return {"ok": True, "removed": removed, "kept": kept, "left_alone": left, "lines": lines}


def _describe(item: dict) -> str:
    if item["kind"] == "entry":
        return f"the old {item['name']} entry in {_tilde(Path(item['path']))}"
    if item["kind"] == "section" and item["provider"] == "github":
        return f"the GitHub token an older setup saved in {_tilde(Path(item['path']))}"
    if item["kind"] == "section":
        return f"the old {bc_keys.NAMES[item['provider']]} key in {_tilde(Path(item['path']))}"
    return _tilde(Path(item["path"]))


def _unknown_files() -> list[str]:
    """The files in ~/.buddy-council that bc didn't create, such as a backup someone made by hand."""
    folder = bc_keys.bc_home()
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir() if not path.name.startswith(".") and path.name not in KNOWN_FILES)


def _once(lines: list[str], kind: str) -> list[str]:
    """The lines not reported by an earlier cleanup: what stays behind is named once, not on every run."""
    record_path = bc_keys.keys_dir() / ".cleanup"
    record = bc_keys._read_json(record_path)
    record = record if isinstance(record, dict) else {}
    told = set(record.get(kind) or [])
    new = [line for line in lines if line not in told]
    if new:
        record[kind] = sorted(told | set(new))
        bc_keys._private_dir(record_path.parent)
        bc_keys._write(record_path, record)
    return new


# ---------------------------------------------------------------------------
# Forget, and GitHub
# ---------------------------------------------------------------------------


def forget(url: Any, start: Any = None) -> dict:
    forgot = bc_keys.forget(url)
    project, _ = find_project(start)
    used = []
    with contextlib.suppress(SetupError):
        settings = load(project) or {}
        for tool in ("requirements", "test_cases", "board"):
            info = tool_state(settings, tool)
            if info.get("site") and any(entry["site"] == info["site"] for entry in forgot):
                used.append(tool)
    if not forgot:
        return {"ok": True, "forgot": [], "used_here": [], "lines": [f"No key is saved for {bc_keys.site_id(url)}."]}
    lines = [f"Forgot the {bc_keys.NAMES[entry['provider']]} key for {entry['site']}." for entry in forgot]
    if used:
        names = _words([TOOLS[tool]["name"].lower() for tool in used])
        lines.append(f"This project reads {names} there: the next command that does asks for a new key.")
    return {"ok": True, "forgot": forgot, "used_here": used, "lines": lines}


def github(url: str, move_old_token: bool = False) -> dict:
    """Read one GitHub document with the first sign-in that works. With move_old_token — the last resort, once
    nothing else could read it — the token an older setup saved for github.com becomes its key first."""
    lines = []
    if move_old_token:
        moved = bc_keys.move_legacy_github_token(_home())
        if moved["moved"]:
            lines.append("GitHub: the token an older setup saved is now the github.com key — nothing else could read "
                         "your design documents")
    try:
        doc, reader, tried = bc_run.read_doc(url)
    except bc_run.RunError as exc:
        kind, _, message = str(exc).partition(": ")
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        old = host in ("github.com", "www.github.com", "raw.githubusercontent.com") and bc_keys.has_legacy_github_token(_home())
        return {"ok": False, "error": kind, "problem": message or kind, "tried": getattr(exc, "tried", []),
                "old_token": old, "lines": lines}
    content = doc["content"]
    return {"ok": True, "read_with": reader, "tried": tried, "chars": len(content),
            "preview": " ".join(content[:200].split()), "lines": lines}


# ---------------------------------------------------------------------------
# Command line — prints JSON, never a key
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A project folder's Buddy-Council settings. Never prints a key.")
    sub = parser.add_subparsers(dest="action", required=True)
    showing = sub.add_parser("status", help="the project folder, each tool's state, and what a command does first")
    showing.add_argument("--from", dest="start", help="where to start looking (default: the current folder)")
    showing.add_argument("--need", default="", help="the tools this command can't run without, comma-separated")
    showing.add_argument("--reads", default="", help="the tools this command reads when the project has them")
    links = sub.add_parser("links", help="what each link names, and whether its site has a key here")
    links.add_argument("--link", action="append", required=True)
    saving = sub.add_parser("save", help="merge a change into the settings, check it, and save it")
    saving.add_argument("--project", required=True)
    saving.add_argument("--patch", default="{}", help="JSON on one line; write every ' as \\u0027")
    saving.add_argument("--unset", action="append", default=[], help="a setting's dotted path, removed first")
    sub.add_parser("migrate", help="move older setups' keys into per-site files and list what else they left")
    cleaning = sub.add_parser("cleanup", help="remove what older setups left, for the sites that answered")
    cleaning.add_argument("--verified", default="", help="the sites that answered this run, comma-separated")
    forgetting = sub.add_parser("forget", help="delete a site's key")
    forgetting.add_argument("--url", required=True)
    forgetting.add_argument("--from", dest="start")
    reading = sub.add_parser("github", help="read one GitHub document with the first sign-in that works")
    reading.add_argument("--url", required=True)
    reading.add_argument("--move-old-token", choices=["yes"], help="first make an older setup's GitHub token the key")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = status(args.start, [t.strip() for t in args.need.split(",") if t.strip()],
                            [t.strip() for t in args.reads.split(",") if t.strip()])
        elif args.action == "links":
            result = {"ok": True, "links": [recognize(link) for link in args.link]}
        elif args.action == "save":
            try:
                patch = json.loads(args.patch)
            except ValueError as exc:
                raise SetupError(f"--patch isn't JSON ({exc}). Pass it on one line, with every ' written as \\u0027.") from None
            result = save(args.project, patch, args.unset)
        elif args.action == "migrate":
            result = migrate()
        elif args.action == "cleanup":
            result = cleanup(args.verified.split(","))
        elif args.action == "forget":
            result = forget(args.url, args.start)
        else:
            result = github(args.url, args.move_old_token == "yes")
    except (SetupError, bc_keys.KeyStoreError, OSError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
