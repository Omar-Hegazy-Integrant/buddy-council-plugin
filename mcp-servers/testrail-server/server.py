"""TestRail MCP Server — read access to TestRail's REST API, plus guarded case creation and updates.

Every `testrail_get_*` tool is read-only. The write tools — `testrail_add_case`,
`testrail_add_cases`, `testrail_add_section`, `testrail_update_case`, and
`testrail_update_cases` — mutate the team's TestRail instance and are
deliberately excluded from the plugin's auto-approve hook and from
`settings.json`, so they always reach a permission prompt. No write tool may
ever be named `testrail_get_*`: the hook auto-approves that glob.

The one local side effect of a read tool: with `save_to`,
`testrail_get_canonical_cases` writes its result to a JSON file directly inside an
existing `<project>/.buddy-council/runs/<run>/` folder, so a run's data reaches the
analysis as a file instead of passing through a model. Nothing else is written.
"""

import asyncio
import contextlib
import html
import os
import re
import sys
import json
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration — env vars take precedence, then the buddy-council secrets file
# ---------------------------------------------------------------------------


def _secrets_candidates() -> list[str]:
    """Secrets-file paths to try, in priority order."""
    candidates = []
    env_path = os.environ.get("BC_SECRETS_FILE")
    if env_path:
        candidates.append(os.path.expanduser(env_path))
    candidates.append(os.path.expanduser("~/.buddy-council/secrets.json"))  # unified location
    candidates.append(os.path.expanduser("~/.buddy-council-secrets.json"))  # legacy flat file
    return candidates


def _load_secrets(section: str) -> dict[str, Any]:
    """Load one section from the first readable buddy-council secrets file.

    Checks BC_SECRETS_FILE, then ~/.buddy-council/secrets.json, then the legacy
    ~/.buddy-council-secrets.json. Returns {} if none is readable, so the env-var
    path and the missing-values check below still apply (fail safe).
    """
    for path in _secrets_candidates():
        if not os.path.exists(path):
            continue
        try:
            if (os.stat(path).st_mode & 0o077) != 0:
                print(
                    f"WARNING: {path} is group/world-accessible; run `chmod 600` on it.",
                    file=sys.stderr,
                )
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, PermissionError, OSError):
            continue
        section_data = data.get(section, {}) if isinstance(data, dict) else {}
        return section_data if isinstance(section_data, dict) else {}
    return {}


_secrets = _load_secrets("testrail")

BASE_URL = (os.environ.get("TESTRAIL_BASE_URL") or _secrets.get("base_url", "")).rstrip("/")
USERNAME = os.environ.get("TESTRAIL_USERNAME") or _secrets.get("username", "")
API_KEY = os.environ.get("TESTRAIL_API_KEY") or _secrets.get("api_key", "")

if not all([BASE_URL, USERNAME, API_KEY]):
    missing = [
        name
        for name, val in [
            ("TESTRAIL_BASE_URL", BASE_URL),
            ("TESTRAIL_USERNAME", USERNAME),
            ("TESTRAIL_API_KEY", API_KEY),
        ]
        if not val
    ]
    print(
        f"ERROR: Missing required TestRail config: {', '.join(missing)}. "
        "Provide credentials in ~/.buddy-council/secrets.json (or BC_SECRETS_FILE) "
        "and non-secret values like TESTRAIL_BASE_URL in the .mcp.json env block. "
        "Run /bc:setup to configure.",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_API_BASE = f"{BASE_URL}/index.php?/api/v2/"

_PAGE_LIMIT = 250  # TestRail's maximum page size on get_* endpoints
_RATE_LIMIT_STATUS = 429
_MAX_RATE_LIMIT_RETRIES = 3
_DEFAULT_RETRY_AFTER_SECONDS = 5.0
_MAX_NETWORK_RETRIES = 2  # reads only: a POST that timed out may still have been applied
_MAX_ERROR_CHARS = 500
_SECTION_PATH_SEPARATOR = "/"

_client = httpx.AsyncClient(
    auth=(USERNAME, API_KEY),
    headers={"Content-Type": "application/json"},
    timeout=30.0,
)


class TestRailError(RuntimeError):
    """A TestRail API call failed. The message carries TestRail's own error text."""


def _error_message(response: httpx.Response) -> str:
    """Extract TestRail's own error text, falling back to the raw body."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])
    text = (response.text or "").strip()
    return text[:_MAX_ERROR_CHARS] if text else response.reason_phrase


def _retry_after_seconds(response: httpx.Response) -> float:
    """Seconds to wait after a 429, from the Retry-After header when present."""
    try:
        return max(float(response.headers.get("Retry-After", "")), 0.0)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS


async def _request(
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Call the TestRail API and return parsed JSON.

    TestRail uses index.php?/api/v2/<endpoint>&key=val for query params, so the
    URL is built manually rather than via httpx base_url + params.

    Retries on 429 (TestRail Cloud's rate limit), honouring Retry-After, and retries
    a GET that hit a network error or timeout — never a POST, which may already have
    been applied. Raises TestRailError carrying the server's own message on any other
    4xx/5xx, because "HTTP 400" alone is useless when the real cause is a required
    custom field.
    """
    url = f"{_API_BASE}{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url = f"{url}&{query}"

    network_retries = 0
    attempt = -1
    while attempt < _MAX_RATE_LIMIT_RETRIES:
        attempt += 1
        try:
            response = await _client.request(method, url, json=payload)
        except httpx.TransportError as exc:
            if method == "GET" and network_retries < _MAX_NETWORK_RETRIES:
                network_retries += 1
                attempt -= 1  # a network retry does not use up a rate-limit retry
                await asyncio.sleep(float(network_retries))
                continue
            raise TestRailError(f"{method} {endpoint} → could not reach TestRail ({type(exc).__name__}: {exc})") from None
        if response.status_code == _RATE_LIMIT_STATUS and attempt < _MAX_RATE_LIMIT_RETRIES:
            await asyncio.sleep(_retry_after_seconds(response))
            continue
        if response.status_code >= 400:
            raise TestRailError(
                f"{method} {endpoint} → HTTP {response.status_code}: {_error_message(response)}"
            )
        if not response.content:
            return {}
        return response.json()

    raise TestRailError(
        f"{method} {endpoint} → still rate-limited after {_MAX_RATE_LIMIT_RETRIES} retries"
    )


async def _get(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    """GET a TestRail endpoint and return parsed JSON."""
    return await _request("GET", endpoint, params=params)


async def _post(endpoint: str, payload: dict[str, Any]) -> Any:
    """POST JSON to a TestRail endpoint and return parsed JSON. Every write clears the link index."""
    result = await _request("POST", endpoint, payload=payload)
    _INDEX.clear()
    return result


async def _get_paged(endpoint: str, params: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    """Read every page of a paginated TestRail collection.

    Older TestRail versions return a bare array with no pagination envelope;
    newer ones return {offset, limit, size, _links, <collection>}. Handle both.
    """
    page = {**params, "limit": _PAGE_LIMIT, "offset": 0}
    items: list[dict[str, Any]] = []
    while True:
        data = await _get(endpoint, params=page)
        if not isinstance(data, dict):
            return list(data or [])
        chunk = data.get(collection, [])
        items.extend(chunk)
        if len(chunk) < _PAGE_LIMIT:
            return items
        page = {**page, "offset": page["offset"] + _PAGE_LIMIT}


# ---------------------------------------------------------------------------
# Section (folder) resolution
# ---------------------------------------------------------------------------


def _matches(name: Any, target: str) -> bool:
    """Compare section names the way a human would — trimmed, case-insensitive."""
    return str(name or "").strip().casefold() == target.strip().casefold()


def _find_child(sections: list[dict[str, Any]], parent_id: int | None, name: str) -> dict[str, Any] | None:
    """Find a direct child section by name under parent_id (None = top level)."""
    for section in sections:
        if section.get("parent_id") == parent_id and _matches(section.get("name"), name):
            return section
    return None


def _section_path(sections: list[dict[str, Any]], section: dict[str, Any]) -> str:
    """Render a section's full folder path, for reporting back to the caller."""
    by_id = {s.get("id"): s for s in sections}
    parts: list[str] = []
    cursor: dict[str, Any] | None = section
    while cursor is not None:
        parts.insert(0, str(cursor.get("name", "")))
        cursor = by_id.get(cursor.get("parent_id"))
    return _SECTION_PATH_SEPARATOR.join(parts)


def _split_path(section_path: str) -> list[str]:
    """Split "Login/Negative cases" into its non-empty segments."""
    return [part.strip() for part in section_path.split(_SECTION_PATH_SEPARATOR) if part.strip()]


async def _resolve_section(
    project_id: int,
    suite_id: int | None,
    section_id: int | None,
    section_path: str | None,
    create_missing: bool,
    dry_run: bool = False,
) -> tuple[int | None, str, list[str]]:
    """Resolve the target folder to a section id.

    Returns (section_id, human-readable path, notes about folders created).

    A numeric section_id is *validated* against the suite rather than trusted:
    a wrong id writes cases into someone else's folder, which is the failure
    mode worth one extra GET to prevent.

    In a dry run, folders that would be created are reported but not POSTed,
    and the returned section_id is None: the folder does not exist yet.
    """
    if section_id is None and not section_path:
        raise TestRailError("Provide either section_id or section_path — a case must land in a folder.")

    sections = await _get_paged(f"get_sections/{project_id}", {"suite_id": suite_id}, "sections")

    if section_id is not None:
        match = next((s for s in sections if s.get("id") == section_id), None)
        if match is None:
            known = ", ".join(sorted(_section_path(sections, s) for s in sections)) or "(none)"
            raise TestRailError(
                f"Section {section_id} is not in project {project_id}"
                f"{f' suite {suite_id}' if suite_id else ''}. Sections here: {known}"
            )
        return section_id, _section_path(sections, match), []

    segments = _split_path(section_path or "")
    notes: list[str] = []
    parent_id: int | None = None
    current: dict[str, Any] | None = None
    existing_depth = 0

    for depth, name in enumerate(segments):
        found = _find_child(sections, parent_id, name)
        if found is None:
            if not create_missing:
                walked = _SECTION_PATH_SEPARATOR.join(segments[:depth]) or "(root)"
                siblings = ", ".join(
                    str(s.get("name")) for s in sections if s.get("parent_id") == parent_id
                ) or "(none)"
                raise TestRailError(
                    f'Folder "{name}" does not exist under {walked}. '
                    f"Existing folders there: {siblings}. "
                    "Pass create_missing_sections=true to create it."
                )
            if dry_run:
                # Nothing below a missing folder can exist either, so every remaining
                # segment would be created too.
                prefix = [_section_path(sections, current)] if current is not None else []
                would_be = _SECTION_PATH_SEPARATOR.join([*prefix, *segments[existing_depth:]])
                for end in range(depth + 1, len(segments) + 1):
                    notes.append(
                        f'would create folder "{_SECTION_PATH_SEPARATOR.join(segments[:end])}"'
                    )
                return None, would_be, notes
            payload: dict[str, Any] = {"name": name}
            if suite_id is not None:
                payload["suite_id"] = suite_id
            if parent_id is not None:
                payload["parent_id"] = parent_id
            found = await _post(f"add_section/{project_id}", payload)
            sections = [*sections, found]
            notes.append(f'created folder "{_SECTION_PATH_SEPARATOR.join(segments[: depth + 1])}"')
        parent_id = found.get("id")
        current = found
        existing_depth = depth + 1

    if current is None or current.get("id") is None:
        raise TestRailError(f'Could not resolve section path "{section_path}".')
    return int(current["id"]), _section_path(sections, current), notes


async def _existing_titles(project_id: int, suite_id: int | None, section_id: int) -> set[str]:
    """Titles already present in a section, normalized for duplicate detection."""
    params: dict[str, Any] = {"section_id": section_id}
    if suite_id is not None:
        params["suite_id"] = suite_id
    cases = await _get_paged(f"get_cases/{project_id}", params, "cases")
    return {str(c.get("title", "")).strip().casefold() for c in cases}


# ---------------------------------------------------------------------------
# Case payload
# ---------------------------------------------------------------------------

_FIELD_ALIASES = {
    "preconditions": "custom_preconds",
    "steps": "custom_steps",
    "expected": "custom_expected",
    "steps_separated": "custom_steps_separated",
}


def _build_case_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Turn a friendly case spec into TestRail's add_case body.

    Maps the readable aliases (preconditions/steps/expected/steps_separated) onto
    their custom_* names, passes the built-in fields through, and prefixes any
    extra custom_fields keys with custom_ when the caller left it off.
    """
    title = str(spec.get("title", "")).strip()
    if not title:
        raise TestRailError("Every case needs a non-empty title.")

    payload: dict[str, Any] = {"title": title}

    for key in ("template_id", "type_id", "priority_id", "milestone_id", "estimate", "refs"):
        if spec.get(key) is not None:
            payload[key] = spec[key]

    for alias, field in _FIELD_ALIASES.items():
        if spec.get(alias) is not None:
            payload[field] = spec[alias]

    extra = spec.get("custom_fields") or {}
    if not isinstance(extra, dict):
        raise TestRailError("custom_fields must be an object mapping field names to values.")
    for key, value in extra.items():
        payload[key if str(key).startswith("custom_") else f"custom_{key}"] = value

    return payload


# ---------------------------------------------------------------------------
# Case updates — diffing, link protection, read-back verification
# ---------------------------------------------------------------------------

_UPDATE_CONTROL_KEYS = {"case_id", "expected_updated_on", "allow_unlink"}
_UPDATE_FIELD_KEYS = {
    "title",
    "type_id",
    "priority_id",
    "milestone_id",
    "estimate",
    "refs",
    "custom_fields",
    *_FIELD_ALIASES,
}
# Moving a case or switching its template changes where it lives or which fields it has at all —
# a decision for a person in TestRail, not something to slip into a content edit.
_UPDATE_REFUSED_KEYS = {"template_id", "section_id", "section_path", "suite_id", "project_id"}
_STEPS_FIELD = "custom_steps_separated"
_TOKEN_SPLIT = re.compile(r"[,;\n]")


def _parse_case_id(value: Any) -> int:
    """Accept 1234, "1234", "C1234", or "TC-1234" — the forms users and reports use."""
    match = re.fullmatch(r"(?:TC-?|C)?(\d+)", str(value if value is not None else "").strip(), re.IGNORECASE)
    if not match:
        raise TestRailError(f"'{value}' is not a test case id — expected 1234, C1234, or TC-1234.")
    return int(match.group(1))


def _custom_key(name: Any) -> str:
    key = str(name).strip()
    return key if key.startswith("custom_") else f"custom_{key}"


def _build_update_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Turn a friendly update entry into update_case's body — only the fields it names.

    Stricter than the create path on purpose: an unknown key fails the entry rather
    than being ignored, because a typo in an update silently changes nothing.
    """
    refused = sorted(key for key in spec if key in _UPDATE_REFUSED_KEYS)
    if refused:
        raise TestRailError(
            f"Refusing to change {', '.join(refused)}: moving a case or switching its template "
            "is done in TestRail itself."
        )
    unknown = sorted(key for key in spec if key not in _UPDATE_CONTROL_KEYS | _UPDATE_FIELD_KEYS)
    if unknown:
        raise TestRailError(f"Unknown update keys {unknown}. Accepted: {sorted(_UPDATE_FIELD_KEYS)}.")

    payload: dict[str, Any] = {}
    if spec.get("title") is not None:
        title = str(spec["title"]).strip()
        if not title:
            raise TestRailError("A case title cannot be blank.")
        payload["title"] = title
    for key in ("type_id", "priority_id", "milestone_id", "estimate", "refs"):
        if spec.get(key) is not None:
            payload[key] = spec[key]
    for alias, field in _FIELD_ALIASES.items():
        if spec.get(alias) is not None:
            payload[field] = spec[alias]
    extra = spec.get("custom_fields") or {}
    if not isinstance(extra, dict):
        raise TestRailError("custom_fields must be an object mapping field names to values.")
    for key, value in extra.items():
        payload[_custom_key(key)] = value
    if not payload:
        raise TestRailError("Nothing to change — the entry names no fields.")
    return payload


def _norm_text(value: Any) -> str:
    return "" if value is None else str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _canon(value: Any) -> Any:
    """A comparable form: line endings and outer whitespace ignored, "2" equal to 2, None equal to ""."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    if isinstance(value, str):
        return _norm_text(value)
    if isinstance(value, list):
        return [_canon(item) for item in value]
    if isinstance(value, dict):
        return {key: _canon(item) for key, item in value.items()}
    return str(value)


def _tokens(value: Any) -> list[str]:
    """IDs in a reference-style field — "A, B;C", a multi-select list, or rich text ("<p>A,&nbsp;B</p>") — in order.

    Some instances store the requirement field as rich text, so markup and entities are dropped before
    splitting; otherwise "<p>A" would never match "A".
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else _TOKEN_SPLIT.split(_clean(value))
    return [token for token in (_clean(item) for item in items) if token]


def _step_keys(steps: Any) -> tuple[str, ...]:
    """Compare only the step keys the caller sent; TestRail adds its own (shared_step_id, …)."""
    keys = {"content", "expected"}
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, dict):
            keys.update(step)
    return tuple(sorted(keys))


def _steps_view(value: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {key: _canon(step.get(key)) for key in keys}
        for step in (value if isinstance(value, list) else [])
        if isinstance(step, dict)
    ]


def _same(field: str, current: Any, new: Any, token_fields: set[str]) -> bool:
    if field in token_fields:
        return [t.casefold() for t in _tokens(current)] == [t.casefold() for t in _tokens(new)]
    if field == _STEPS_FIELD:
        keys = _step_keys(new)
        return _steps_view(current, keys) == _steps_view(new, keys)
    return _canon(current) == _canon(new)


def _removed_links(current: dict[str, Any], body: dict[str, Any], token_fields: set[str]) -> dict[str, list[str]]:
    """IDs this update would take out of refs or the requirement field."""
    removed: dict[str, list[str]] = {}
    for field in token_fields & set(body):
        kept = {token.casefold() for token in _tokens(body[field])}
        gone = [token for token in _tokens(current.get(field)) if token.casefold() not in kept]
        if gone:
            removed[field] = gone
    return removed


def _update_warnings(current: dict[str, Any], body: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if _STEPS_FIELD in body:
        before = len(current.get(_STEPS_FIELD) or [])
        after = len(body[_STEPS_FIELD] or [])
        if after < before:
            warnings.append(
                f"{_STEPS_FIELD} goes from {before} steps to {after}: steps are replaced as a whole list, "
                "so every step not sent is deleted."
            )
    return warnings


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Requirement-link index — which cases link to which requirements, and where they sit
# ---------------------------------------------------------------------------

_INDEX_TTL_SECONDS = 600.0
_DEFAULT_ID_SAMPLE = 500
_MAX_ID_SAMPLE = 2000
_CASES_CHAR_BUDGET = 60_000  # per testrail_get_linked_cases response, so it fits MCP output caps


@dataclass
class _LinkIndex:
    loaded_at: float
    cases: list[dict[str, Any]]
    ids_by_case: dict[int, list[str]]
    section_paths: dict[int, str]


_INDEX: dict[tuple[int, int | None, str], _LinkIndex] = {}


async def _link_index(project_id: int, suite_id: int | None, field: str, refresh: bool = False) -> _LinkIndex:
    """Every case in the suite with the requirement IDs its requirement field holds, cached.

    TestRail cannot filter on a custom field, so the suite is read once and matched locally.
    Any case or folder write through this server clears the cache.
    """
    key = (project_id, suite_id, field)
    cached = _INDEX.get(key)
    if cached and not refresh and time.monotonic() - cached.loaded_at < _INDEX_TTL_SECONDS:
        return cached
    cases = await _get_paged(f"get_cases/{project_id}", {"suite_id": suite_id}, "cases")
    sections = await _get_paged(f"get_sections/{project_id}", {"suite_id": suite_id}, "sections")
    index = _LinkIndex(
        loaded_at=time.monotonic(),
        cases=cases,
        ids_by_case={case["id"]: _tokens(case.get(field)) for case in cases if case.get("id") is not None},
        section_paths={s["id"]: _section_path(sections, s) for s in sections if s.get("id") is not None},
    )
    _INDEX[key] = index
    return index


def _natural_key(token: str) -> tuple:
    """Sort REQ-2 before REQ-10."""
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part.casefold()) for part in re.split(r"(\d+)", token) if part)


def _id_prefix(token: str) -> str:
    return re.sub(r"[-_ ]?\d+$", "", token) or token


def _spread(values: list[str], size: int) -> list[str]:
    """``size`` values evenly spaced through a sorted list, first and last included."""
    if len(values) <= size:
        return list(values)
    if size == 1:
        return [values[0]]
    return list(dict.fromkeys(values[round(i * (len(values) - 1) / (size - 1))] for i in range(size)))


# ---------------------------------------------------------------------------
# Canonical cases — the plugin's slim test_case schema, rendered from the cached suite
# ---------------------------------------------------------------------------

_CASE_ID_PREFIX = "TC-"
_MAX_CUSTOM_TEXT = 500  # longer custom text is cut here, with a note of how much more TestRail holds
_BODY_FIELDS = {
    "custom_preconds", "custom_steps", "custom_expected", "custom_steps_separated",
    "custom_mission", "custom_goals", "custom_description", "custom_desc",
}
_TEXT_FIELD_TYPES = {1, 3, 4}  # String, Text, URL
_SKIPPED_FIELD_TYPES = {7, 8, 9, 10, 11}  # User, Date, Milestone, Steps, Step Results
_DROPDOWN, _CHECKBOX, _INTEGER, _MULTI_SELECT = 6, 5, 2, 12
_TAG = re.compile(r"<[^>]+>")
_BREAK_TAG = re.compile(r"<\s*(br|/p|/li|/div|/h[1-6]|/tr)\s*/?>", re.IGNORECASE)
_BLANK_RUN = re.compile(r"\n{3,}")
_META_CACHE: dict[Any, tuple[float, Any]] = {}  # field definitions, priorities, case types — configuration, not cases


def _clean(value: Any) -> str:
    """A text field as plain text: tags dropped (line breaks kept), entities decoded, whitespace tidied."""
    text = _norm_text(value)
    if "<" in text and ">" in text:
        text = _TAG.sub("", _BREAK_TAG.sub("\n", text))
    text = html.unescape(text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_RUN.sub("\n\n", text).strip()


async def _cached_meta(key: Any, load: Any) -> Any:
    cached = _META_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _INDEX_TTL_SECONDS:
        return cached[1]
    value = await load()
    _META_CACHE[key] = (time.monotonic(), value)
    return value


def _options_for(field: dict[str, Any], project_id: int) -> dict[int, str]:
    """A dropdown or multi-select's option names, from the config that applies to this project."""
    configs = [c for c in field.get("configs") or [] if isinstance(c, dict)]
    chosen = next((c for c in configs if project_id in ((c.get("context") or {}).get("project_ids") or [])), None)
    chosen = chosen or next((c for c in configs if (c.get("context") or {}).get("is_global")), None)
    chosen = chosen or (configs[0] if configs else {})
    options: dict[int, str] = {}
    for line in str((chosen.get("options") or {}).get("items") or "").splitlines():
        number, _, label = line.partition(",")
        if number.strip().isdigit():
            options[int(number.strip())] = label.strip()
    return options


async def _case_meta(project_id: int) -> tuple[dict[str, dict[str, Any]], dict[int, str], dict[int, str], list[str]]:
    """Custom field definitions (label, type, option names), priority names, and case type names.

    Any of them that cannot be read leaves raw values in place and a warning, never a failed fetch.
    """
    warnings: list[str] = []

    async def fields() -> dict[str, dict[str, Any]]:
        listing = await _get("get_case_fields")
        return {
            f["system_name"]: {"label": f.get("label") or f["system_name"], "type_id": f.get("type_id"), "options": _options_for(f, project_id)}
            for f in listing or []
            if isinstance(f, dict) and f.get("system_name")
        }

    async def named(endpoint: str) -> dict[int, str]:
        listing = await _get(endpoint)
        return {int(e["id"]): str(e.get("name") or e["id"]) for e in listing or [] if isinstance(e, dict) and e.get("id") is not None}

    results: list[Any] = []
    for key, load, what in (
        (("fields", project_id), fields, "case field definitions"),
        ("get_priorities", lambda: named("get_priorities"), "priorities"),
        ("get_case_types", lambda: named("get_case_types"), "case types"),
    ):
        try:
            results.append(await _cached_meta(key, load))
        except TestRailError as exc:
            warnings.append(f"Could not read {what} ({exc}); their raw ids are shown instead.")
            results.append({})
    return results[0], results[1], results[2], warnings


def _custom_value(value: Any, meta: dict[str, Any] | None) -> Any:
    """A custom field as a reader would want it, or None when it carries nothing worth showing."""
    type_id = (meta or {}).get("type_id")
    options = (meta or {}).get("options") or {}
    if type_id in _SKIPPED_FIELD_TYPES or value is None or value == "" or value == []:
        return None
    if type_id == _DROPDOWN or (type_id is None and isinstance(value, int) and not isinstance(value, bool) and options):
        return options.get(value, str(value)) if isinstance(value, int) else None
    if type_id == _MULTI_SELECT or isinstance(value, list):
        names = [options.get(v, str(v)) for v in value if isinstance(v, (int, str))] if isinstance(value, list) else []
        return names or None
    if type_id == _CHECKBOX or isinstance(value, bool):
        return True if value is True else None  # an unticked box is indistinguishable from an unset one
    if type_id == _INTEGER and isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and (type_id in _TEXT_FIELD_TYPES or type_id is None):
        text = _clean(value)
        if len(text) > _MAX_CUSTOM_TEXT:
            text = f"{text[:_MAX_CUSTOM_TEXT]}… [{len(text) - _MAX_CUSTOM_TEXT} more characters in TestRail]"
        return text or None
    return None


def _case_steps(case: dict[str, Any]) -> list[dict[str, str]]:
    """Steps from a steps template, or the text template's steps/expected as a single step."""
    steps = case.get("custom_steps_separated")
    if isinstance(steps, list):
        rendered = [
            {"content": _clean(step.get("content")), "expected": _clean(step.get("expected"))}
            for step in steps
            if isinstance(step, dict)
        ]
        return [step for step in rendered if step["content"] or step["expected"]]
    content, expected = _clean(case.get("custom_steps")), _clean(case.get("custom_expected"))
    return [{"content": content, "expected": expected}] if content or expected else []


def _canonical_case(
    case: dict[str, Any],
    field: str,
    section_paths: dict[int, str],
    include_steps: bool,
    fields: dict[str, dict[str, Any]] | None = None,
    priorities: dict[int, str] | None = None,
    case_types: dict[int, str] | None = None,
) -> dict[str, Any]:
    """One TestRail case in the canonical test_case schema the analysis skills read."""
    fields, priorities, case_types = fields or {}, priorities or {}, case_types or {}
    case_id = case.get("id")
    section_id = case.get("section_id")
    path = section_paths.get(section_id, "")
    description = []
    for key, label in (("custom_preconds", "Preconditions"), ("custom_mission", "Mission"), ("custom_goals", "Goals")):
        text = _clean(case.get(key))
        if text:
            description.append(f"{label}: {text}")
    for key in ("custom_description", "custom_desc"):
        text = _clean(case.get(key))
        if text:
            description.insert(0, text)
    raw: dict[str, Any] = {
        "testrail_id": case_id,
        "section_id": section_id,
        "section_path": path,
        "suite_id": case.get("suite_id"),
        "refs": _norm_text(case.get("refs")),
        "priority": priorities.get(case.get("priority_id"), case.get("priority_id")),
        "type": case_types.get(case.get("type_id"), case.get("type_id")),
        "template_id": case.get("template_id"),
        "updated_on": case.get("updated_on"),
        "url": f"{BASE_URL}/index.php?/cases/view/{case_id}" if case_id else None,
    }
    if include_steps:
        raw["steps"] = _case_steps(case)
    custom: dict[str, Any] = {}
    for key, value in case.items():
        if not key.startswith("custom_") or key == field or key in _BODY_FIELDS:
            continue
        shown = _custom_value(value, fields.get(key))
        if shown is None:
            continue
        label = str((fields.get(key) or {}).get("label") or key)
        custom[f"{label} ({key})" if label in custom else label] = shown
    if custom:
        raw["custom"] = custom
    return {
        "type": "test_case",
        "id": f"{_CASE_ID_PREFIX}{case_id}",
        "title": _clean(case.get("title")),
        "description": "\n\n".join(description),
        "feature": path.rsplit(_SECTION_PATH_SEPARATOR, 1)[-1] if path else "",
        "status": "",
        "linked_ids": _tokens(case.get(field)),
        "raw_fields": raw,
    }


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _in_section(path: str, wanted: str) -> bool:
    """A section path equal to or under ``wanted`` — or, for a bare name, with a folder whose name contains it.

    A bare name matches as whole words, case-insensitively, so "Alarms" finds "Alarms", "Alarms - iOS", and
    "iOS Alarms" but not "Alarmsound".
    """
    folded, target = path.casefold(), wanted.strip().strip(_SECTION_PATH_SEPARATOR).casefold()
    if not target:
        return False
    if _SECTION_PATH_SEPARATOR in target:
        return folded == target or folded.startswith(target + _SECTION_PATH_SEPARATOR)
    words = _words(target)
    return bool(words) and any(f" {words} " in f" {_words(segment)} " for segment in folded.split(_SECTION_PATH_SEPARATOR))


# ---------------------------------------------------------------------------
# Run files — with save_to, a read writes its whole result into the caller's run folder
# ---------------------------------------------------------------------------

_RUN_FILE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\.json")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAVE_TO_SHAPE = "<project>/.buddy-council/runs/<run>/<name>.json"


def _run_file_path(save_to: str) -> Path:
    """Validate save_to: a .json file directly inside an existing .buddy-council/runs/<run>/ folder.

    The plugin's collect step creates the run folder; this server never creates a directory, and nothing
    outside such a folder can be written.
    """
    text = str(save_to or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise TestRailError(f"save_to must be an absolute path: {_SAVE_TO_SHAPE}.")
    run_dir = path.parent
    if (
        ".." in path.parts
        or not _RUN_FILE_NAME.fullmatch(path.name)
        or not _RUN_ID.fullmatch(run_dir.name)
        or run_dir.parent.name != "runs"
        or run_dir.parent.parent.name != ".buddy-council"
    ):
        raise TestRailError(f"Refusing save_to {text!r}: it must be {_SAVE_TO_SHAPE}.")
    if not run_dir.is_dir():
        raise TestRailError(f"Refusing save_to {text!r}: the run folder does not exist — the collect step creates it first.")
    if path.is_symlink():
        raise TestRailError(f"Refusing save_to {text!r}: it is a symbolic link.")
    real = run_dir.resolve()
    if real.parent.name != "runs" or real.parent.parent.name != ".buddy-council":
        raise TestRailError(f"Refusing save_to {text!r}: the run folder resolves outside a .buddy-council/runs/ folder.")
    return real / path.name


def _write_run_file(save_to: str, payload: dict[str, Any]) -> tuple[str, int]:
    """Write payload as JSON to save_to, atomically and readable only by the user. Returns (path, bytes)."""
    path = _run_file_path(save_to)
    data = json.dumps(payload, ensure_ascii=False, indent=1, default=str).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return str(path), len(data)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

# Every tool returns one JSON string, sent once as text. structured_output=False stops FastMCP from adding a
# structured copy of it ({"result": "<the same JSON, escaped>"}): Copilot CLI hands the model both, which
# doubles every response and pushes results past its ~20 KB inline limit at half their real size.
mcp = FastMCP("testrail")


@mcp.tool(structured_output=False)
async def testrail_get_projects() -> str:
    """List all projects in the TestRail instance.

    Returns a JSON array of projects with id, name, announcement, and is_completed fields.
    Used during setup to let the user pick which project to analyze.
    """
    data = await _get("get_projects")
    projects = data.get("projects", data) if isinstance(data, dict) else data
    return json.dumps(projects, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_suites(project_id: int) -> str:
    """List all test suites for a given project.

    Args:
        project_id: The TestRail project ID.

    Returns a JSON array of suites with id, name, and description fields.
    """
    data = await _get(f"get_suites/{project_id}")
    return json.dumps(data, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_sections(
    project_id: int, suite_id: int | None = None
) -> str:
    """List all sections (folders) for a given project/suite.

    Args:
        project_id: The TestRail project ID.
        suite_id: Optional suite ID to filter by.

    Returns a JSON array of sections with id, name, parent_id, depth, and a
    derived `path` field ("Login/Negative cases") that testrail_add_cases accepts
    as section_path.
    """
    sections = await _get_paged(f"get_sections/{project_id}", {"suite_id": suite_id}, "sections")
    enriched = [{**s, "path": _section_path(sections, s)} for s in sections]
    return json.dumps(enriched, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_cases(
    project_id: int,
    suite_id: int | None = None,
    section_id: int | None = None,
    limit: int = 250,
    offset: int = 0,
) -> str:
    """Fetch test cases for a project with pagination.

    Args:
        project_id: The TestRail project ID.
        suite_id: Optional suite ID to filter by.
        section_id: Optional section ID to filter by.
        limit: Max results per page (TestRail max is 250).
        offset: Number of results to skip for pagination.

    Returns a JSON object with:
    - cases: array of test case objects
    - size: number of cases returned in this page
    - offset: current offset
    - limit: current limit

    To paginate: if size == limit, call again with offset += limit.
    """
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if suite_id is not None:
        params["suite_id"] = suite_id
    if section_id is not None:
        params["section_id"] = section_id
    data = await _get(f"get_cases/{project_id}", params=params)
    return json.dumps(data, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_cases_by_refs(
    project_id: int,
    refs: str,
    suite_id: int | None = None,
) -> str:
    """Fetch test cases that reference specific requirement IDs.

    Uses TestRail's built-in filter parameter to search by the refs/reference field.
    This is much faster than fetching all cases when you only need cases linked to
    specific requirements.

    Args:
        project_id: The TestRail project ID.
        refs: Comma-separated requirement IDs to search for (e.g., "CWA-REQ-85,CWA-REQ-86").
        suite_id: Optional suite ID to filter by.

    Returns a JSON object with cases array (same format as testrail_get_cases).
    Note: TestRail's refs filter searches the built-in "References" field.
    For custom fields like custom_jama_req_id, use testrail_get_cases with section_id filtering instead.
    """
    params: dict[str, Any] = {"refs": refs}
    if suite_id is not None:
        params["suite_id"] = suite_id
    all_cases = await _get_paged(f"get_cases/{project_id}", params, "cases")
    return json.dumps({"cases": all_cases, "size": len(all_cases)}, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_case(case_id: int) -> str:
    """Fetch a single test case by its ID.

    Args:
        case_id: The TestRail case ID (numeric, without TC- prefix).

    Returns the full test case object with all fields including custom fields.
    """
    data = await _get(f"get_case/{case_id}")
    return json.dumps(data, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_case_fields() -> str:
    """List every case field defined on this TestRail instance, including custom ones.

    Call this before creating cases against an unfamiliar instance: it is the only
    way to learn which custom_* fields exist, which are required, and what their
    types and valid option values are. A required custom field that you omit is
    the usual cause of a 400 from testrail_add_case.

    Returns a JSON array of field objects with system_name, label, type_id, and
    per-project configs carrying is_required and options.
    """
    data = await _get("get_case_fields")
    return json.dumps(data, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_case_types() -> str:
    """List the available case types (Functional, Regression, Smoke, ...).

    Returns a JSON array of {id, name, is_default}. Use the id as type_id when
    creating a case — TestRail takes ids, not names.
    """
    data = await _get("get_case_types")
    return json.dumps(data, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_priorities() -> str:
    """List the available case priorities (Low, Medium, High, Critical, ...).

    Returns a JSON array of {id, name, short_name, priority, is_default}. Use the
    id as priority_id when creating a case.
    """
    data = await _get("get_priorities")
    return json.dumps(data, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_templates(project_id: int) -> str:
    """List the case templates available in a project.

    Args:
        project_id: The TestRail project ID.

    Returns a JSON array of {id, name, is_default}. The template decides which
    fields a case shows — "Test Case (Steps)" enables custom_steps_separated,
    while "Test Case (Text)" uses custom_steps and custom_expected instead.
    """
    data = await _get(f"get_templates/{project_id}")
    return json.dumps(data, indent=2)


@mcp.tool(structured_output=False)
async def testrail_get_requirement_ids(
    project_id: int,
    requirement_field: str,
    suite_id: int | None = None,
    sample_size: int = _DEFAULT_ID_SAMPLE,
    refresh: bool = False,
) -> str:
    """Summarize which requirement IDs a suite's cases link to through the requirement field.

    Reads every case once (cached for 10 minutes) and returns counts, the ID
    prefixes in use, and an evenly spread sample of distinct IDs — compact enough to
    hand to jama_get_scope_proposal. Read-only.

    Args:
        project_id: The TestRail project ID.
        requirement_field: The custom field holding requirement IDs
            (test_cases.authoring.requirement_field); custom_ prefix optional.
        suite_id: The suite to read. Required on multi-suite projects.
        sample_size: How many distinct IDs to return, spread across the sorted list
            (default 500, max 2000).
        refresh: Ignore the cache and re-read the suite.

    Returns JSON {"requirement_field", "field_found", "cases_scanned",
    "cases_with_ids", "distinct_ids", "prefixes": {prefix: count}, "sample": [...],
    "warnings"}.
    """
    field = _custom_key(requirement_field)
    index = await _link_index(project_id, suite_id, field, refresh)
    distinct: dict[str, str] = {}
    with_ids = 0
    for tokens in index.ids_by_case.values():
        if tokens:
            with_ids += 1
        for token in tokens:
            distinct.setdefault(token.casefold(), token)
    ordered = sorted(distinct.values(), key=_natural_key)
    size = max(1, min(int(sample_size or _DEFAULT_ID_SAMPLE), _MAX_ID_SAMPLE))
    field_found = any(field in case for case in index.cases)
    warnings: list[str] = []
    if index.cases and not field_found:
        warnings.append(f"No case carries a field named {field} — check test_cases.authoring.requirement_field.")
    return json.dumps(
        {
            "requirement_field": field,
            "field_found": field_found,
            "cases_scanned": len(index.cases),
            "cases_with_ids": with_ids,
            "distinct_ids": len(ordered),
            "prefixes": dict(Counter(_id_prefix(token) for token in ordered).most_common()),
            "sample": _spread(ordered, size),
            "warnings": warnings,
        },
        indent=2,
    )


@mcp.tool(structured_output=False)
async def testrail_get_linked_cases(
    project_id: int,
    requirement_field: str,
    requirement_ids: list[str],
    suite_id: int | None = None,
    include_cases: bool = True,
    unlinked: bool = False,
    offset: int = 0,
    refresh: bool = False,
) -> str:
    """Find the cases linked to given requirement IDs through the requirement field, and where they sit.

    TestRail cannot filter on a custom field, so the suite is read once (cached for
    10 minutes; any write through this server clears it) and matched locally. Use it
    to fetch a feature's cases by their requirement links — whatever their folders
    are called — and to file a new case beside the cases already testing the same
    requirements. With unlinked=true it lists the orphans instead: every case whose
    requirement field holds no ID. Read-only.

    Large results page: while `next_offset` is set, call again with the same
    arguments and offset=next_offset, and concatenate `cases`.

    Args:
        project_id: The TestRail project ID.
        requirement_field: The custom field holding requirement IDs; custom_ prefix optional.
        requirement_ids: The requirement IDs to look for, e.g. a feature's requirements.
            Ignored with unlinked=true.
        suite_id: The suite to read. Required on multi-suite projects.
        include_cases: Return the matching cases themselves; false returns only the summary.
        unlinked: Return the cases with no requirement ID at all instead.
        offset: Position to resume `cases` from — the previous page's `next_offset`.
        refresh: Ignore the cache and re-read the suite.

    Returns JSON {"requirement_field", "cases_scanned", "matched_cases",
    "sections": [{"id", "path", "cases"}] (most cases first),
    "by_requirement": {id: [case ids]}, "unlinked_ids": [...], "cases": [...],
    "offset", "next_offset"}.
    """
    field = _custom_key(requirement_field)
    wanted: dict[str, str] = {}
    for raw in [] if unlinked else requirement_ids or []:
        token = str(raw).strip()
        if token:
            wanted.setdefault(token.casefold(), token)
    index = await _link_index(project_id, suite_id, field, refresh)
    by_requirement: dict[str, list[int]] = {original: [] for original in wanted.values()}
    matched: list[dict[str, Any]] = []
    for case in index.cases:
        tokens = index.ids_by_case.get(case.get("id"), [])
        if unlinked:
            if not tokens:
                matched.append(case)
            continue
        hits = [wanted[t.casefold()] for t in tokens if t.casefold() in wanted]
        if not hits:
            continue
        matched.append(case)
        for requirement in dict.fromkeys(hits):
            by_requirement[requirement].append(case["id"])

    page: list[dict[str, Any]] = []
    start = min(max(offset, 0), len(matched))
    if include_cases:
        used = 0
        for case in matched[start:]:
            size = len(json.dumps(case))
            if page and used + size > _CASES_CHAR_BUDGET:
                break
            page.append(case)
            used += size
    end = start + len(page)
    sections = Counter(case.get("section_id") for case in matched)
    return json.dumps(
        {
            "requirement_field": field,
            "cases_scanned": len(index.cases),
            "matched_cases": len(matched),
            "sections": [
                {"id": section_id, "path": index.section_paths.get(section_id, ""), "cases": count}
                for section_id, count in sections.most_common()
            ],
            "by_requirement": {requirement: ids for requirement, ids in by_requirement.items() if ids},
            "unlinked_ids": [requirement for requirement, ids in by_requirement.items() if not ids],
            "cases": page,
            "offset": start,
            "next_offset": end if include_cases and end < len(matched) else None,
        },
        indent=2,
    )


@mcp.tool(structured_output=False)
async def testrail_get_canonical_cases(
    project_id: int,
    requirement_field: str,
    suite_id: int | None = None,
    requirement_ids: list[str] | None = None,
    section: str = "",
    unlinked: bool = False,
    include_steps: bool = True,
    offset: int = 0,
    refresh: bool = False,
    save_to: str = "",
) -> str:
    """Fetch test cases in the plugin's canonical schema — slim, with their requirement links. Read-only.

    Each case is {type: "test_case", id: "TC-1234", title, description, feature,
    status, linked_ids, raw_fields}: linked_ids are the IDs in the requirement
    field, description holds the preconditions, and raw_fields carries the steps,
    refs, section path, and browse url — a fraction of the size of the raw cases
    from testrail_get_cases. The suite is read once and cached for 10 minutes; any
    write through this server clears the cache.

    With no filter, every case in the suite. Filters:
    - requirement_ids: the cases linked to any of these IDs through the requirement field.
    - section: the cases filed under this folder. A path ("Alarms/Battery") includes
      its subfolders; a bare name matches a folder of that name at any depth. Given
      together with requirement_ids, a case matching either is returned.
    - unlinked: only the cases whose requirement field holds no ID (within `section`
      when it is given) — the orphans.

    Large results page: while `next_offset` is set, call again with the same
    arguments and offset=next_offset, and concatenate `test_cases`.

    Args:
        project_id: The TestRail project ID.
        requirement_field: The custom field holding requirement IDs
            (test_cases.authoring.requirement_field); custom_ prefix optional.
        suite_id: The suite to read. Required on multi-suite projects.
        requirement_ids: Only cases linked to these requirement IDs.
        section: Only cases under this folder path or folder name.
        unlinked: Only cases with no requirement ID.
        include_steps: Include each case's steps and expected results (default true).
        offset: Position to resume `test_cases` from — the previous page's `next_offset`.
        refresh: Ignore the cache and re-read the suite.
        save_to: Write every matching case to this file instead of returning them —
            an absolute path inside the run folder the plugin's collect step created:
            <project>/.buddy-council/runs/<run>/test_cases.json. The response then
            carries only `saved_to`, the counts, and the summary.

    Returns JSON {"requirement_field", "field_found", "suite_id", "cases_scanned",
    "matched", "offset", "next_offset", "test_cases", "unlinked_ids", "summary",
    "warnings"} — with save_to, "saved_to" and "bytes" in place of test_cases and paging.
    """
    if save_to:
        _run_file_path(save_to)  # a bad path fails before TestRail is read
        offset = 0
    field = _custom_key(requirement_field)
    wanted: dict[str, str] = {}
    for raw in requirement_ids or []:
        token = str(raw).strip()
        if token:
            wanted.setdefault(token.casefold(), token)
    folder = (section or "").strip()
    index = await _link_index(project_id, suite_id, field, refresh)
    linked: set[str] = set()
    matched: list[dict[str, Any]] = []
    for case in index.cases:
        tokens = index.ids_by_case.get(case.get("id"), [])
        in_folder = bool(folder) and _in_section(index.section_paths.get(case.get("section_id"), ""), folder)
        if unlinked:
            if tokens or (folder and not in_folder):
                continue
        elif wanted or folder:
            hits = {t.casefold() for t in tokens} & wanted.keys()
            linked |= hits
            if not hits and not in_folder:
                continue
        matched.append(case)
    fields, priorities, case_types, warnings = await _case_meta(project_id)
    rendered = [
        _canonical_case(case, field, index.section_paths, include_steps, fields, priorities, case_types) for case in matched
    ]

    field_found = any(field in case for case in index.cases)
    if index.cases and not field_found:
        warnings.append(f"No case carries a field named {field} — check test_cases.authoring.requirement_field.")
    if folder and not any(_in_section(path, folder) for path in index.section_paths.values()):
        warnings.append(f"No folder matches section {folder!r}.")
    unlinked_ids = [] if unlinked else [original for key, original in wanted.items() if key not in linked]
    selection = []
    if wanted and not unlinked:
        selection.append(f"linked to {len(wanted) - len(unlinked_ids)} of {len(wanted)} requested requirement IDs")
    if folder:
        selection.append(f"under folder {folder!r}")
    if unlinked:
        selection.append("with no requirement ID")
    what = f" ({'; '.join(selection)})" if selection else ""
    summary = f"{len(rendered)} of {len(index.cases)} cases in suite {suite_id}{what}"
    if not include_steps:
        summary += "; steps left out"

    if save_to:
        saved_to, size = _write_run_file(
            save_to,
            {
                "kind": "test_cases",
                "source": "testrail",
                "written_at": _utc_now(),
                "project_id": project_id,
                "suite_id": suite_id,
                "requirement_field": field,
                "filter": {"requirement_ids": list(wanted.values()), "section": folder, "unlinked": unlinked},
                "summary": summary,
                "warnings": warnings,
                "cases_scanned": len(index.cases),
                "count": len(rendered),
                "test_cases": rendered,
            },
        )
        return json.dumps(
            {
                "requirement_field": field,
                "field_found": field_found,
                "suite_id": suite_id,
                "cases_scanned": len(index.cases),
                "matched": len(rendered),
                "saved_to": saved_to,
                "bytes": size,
                "unlinked_ids": unlinked_ids,
                "summary": f"{summary}; written to {saved_to}",
                "warnings": warnings,
            },
            indent=2,
        )

    start = min(max(offset, 0), len(rendered))
    page: list[dict[str, Any]] = []
    used = 0
    for case in rendered[start:]:
        size = len(json.dumps(case, ensure_ascii=False))
        if page and used + size > _CASES_CHAR_BUDGET:
            break
        page.append(case)
        used += size
    end = start + len(page)
    next_offset = end if end < len(rendered) else None
    if start or next_offset is not None:
        resume = f"call again with offset={next_offset}" if next_offset is not None else "last page"
        summary += f"; page {start + 1}-{end} of {len(rendered)}, {resume}"
    return json.dumps(
        {
            "requirement_field": field,
            "field_found": field_found,
            "suite_id": suite_id,
            "cases_scanned": len(index.cases),
            "matched": len(rendered),
            "offset": start,
            "next_offset": next_offset,
            "test_cases": page,
            "unlinked_ids": unlinked_ids,
            "summary": summary,
            "warnings": warnings,
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool(structured_output=False)
async def testrail_add_section(
    project_id: int,
    name: str,
    suite_id: int | None = None,
    parent_id: int | None = None,
    description: str | None = None,
) -> str:
    """Create a section (folder) in a test suite. WRITES to TestRail.

    Args:
        project_id: The TestRail project ID.
        name: The folder name.
        suite_id: The suite to create it in. Required on multi-suite projects.
        parent_id: Optional parent section, to nest this folder under it.
        description: Optional folder description.

    Returns the created section object. Prefer passing section_path to
    testrail_add_cases with create_missing_sections — it creates the whole
    chain of folders in one call and reports what it made.
    """
    payload: dict[str, Any] = {"name": name}
    if suite_id is not None:
        payload["suite_id"] = suite_id
    if parent_id is not None:
        payload["parent_id"] = parent_id
    if description is not None:
        payload["description"] = description
    data = await _post(f"add_section/{project_id}", payload)
    return json.dumps(data, indent=2)


@mcp.tool(structured_output=False)
async def testrail_add_case(
    project_id: int,
    title: str,
    suite_id: int | None = None,
    section_id: int | None = None,
    section_path: str | None = None,
    create_missing_sections: bool = True,
    template_id: int | None = None,
    type_id: int | None = None,
    priority_id: int | None = None,
    estimate: str | None = None,
    milestone_id: int | None = None,
    refs: str | None = None,
    preconditions: str | None = None,
    steps: str | None = None,
    expected: str | None = None,
    steps_separated: list[dict[str, Any]] | None = None,
    custom_fields: dict[str, Any] | None = None,
    skip_if_title_exists: bool = True,
) -> str:
    """Create ONE test case in a suite folder. WRITES to TestRail.

    Args:
        project_id: The TestRail project ID.
        title: The case title. Required.
        suite_id: The suite to write into. Required on multi-suite projects.
        section_id: Target folder by numeric id. Validated against the suite.
        section_path: Target folder by path instead, e.g. "Login/Negative cases".
            Give exactly one of section_id or section_path.
        create_missing_sections: Create folders in section_path that don't exist yet.
        template_id: Case template id (see testrail_get_templates).
        type_id: Case type id (see testrail_get_case_types).
        priority_id: Priority id (see testrail_get_priorities).
        estimate: Time estimate, e.g. "3m", "1h 30m".
        milestone_id: Milestone to attach the case to.
        refs: Comma-separated requirement references, e.g. "CWA-REQ-85,CWA-REQ-86".
        preconditions: Maps to custom_preconds.
        steps: Plain-text steps; maps to custom_steps.
        expected: Plain-text expected result; maps to custom_expected.
        steps_separated: Structured steps, [{"content": "...", "expected": "..."}].
            Requires a steps-style template.
        custom_fields: Any other custom fields, {"my_field": value}. A missing
            custom_ prefix is added for you.
        skip_if_title_exists: When true (default), do nothing if a case with this
            title already sits in the target folder, so re-runs don't duplicate.

    Returns JSON: {"created": [...], "skipped": [...], "failed": [...], "section": {...}}
    — the same shape as testrail_add_cases, with at most one entry.
    """
    spec: dict[str, Any] = {
        "title": title,
        "template_id": template_id,
        "type_id": type_id,
        "priority_id": priority_id,
        "estimate": estimate,
        "milestone_id": milestone_id,
        "refs": refs,
        "preconditions": preconditions,
        "steps": steps,
        "expected": expected,
        "steps_separated": steps_separated,
        "custom_fields": custom_fields,
    }
    return await testrail_add_cases(
        project_id=project_id,
        cases=[spec],
        suite_id=suite_id,
        section_id=section_id,
        section_path=section_path,
        create_missing_sections=create_missing_sections,
        skip_if_title_exists=skip_if_title_exists,
        dry_run=False,
    )


@mcp.tool(structured_output=False)
async def testrail_add_cases(
    project_id: int,
    cases: list[dict[str, Any]],
    suite_id: int | None = None,
    section_id: int | None = None,
    section_path: str | None = None,
    create_missing_sections: bool = True,
    skip_if_title_exists: bool = True,
    dry_run: bool = False,
) -> str:
    """Create one or more test cases in suite folders. WRITES to TestRail.

    TestRail has no bulk-create endpoint, so this loops add_case. The loop is
    resilient: a case that fails is recorded and the rest still run, and a 429
    from TestRail Cloud is retried with its Retry-After delay.

    Args:
        project_id: The TestRail project ID.
        cases: The cases to create. Each entry accepts the same keys as
            testrail_add_case: title (required), template_id, type_id,
            priority_id, estimate, milestone_id, refs, preconditions, steps,
            expected, steps_separated, custom_fields — plus its own optional
            section_id or section_path to override the batch target, which is
            how one call can write into several folders.
        suite_id: The suite to write into. Required on multi-suite projects.
        section_id: Default target folder by numeric id, for cases that don't
            specify their own.
        section_path: Default target folder by path, e.g. "Login/Negative cases".
        create_missing_sections: Create folders in a section_path that don't
            exist yet. Folders created are listed in the response.
        skip_if_title_exists: When true (default), a case whose title already
            exists in its target folder is skipped rather than duplicated, so
            re-running a batch is safe. Comparison is trimmed and case-insensitive.
        dry_run: When true, resolve folders and duplicate-check but create
            nothing. Use it to preview a batch before committing. A folder that
            would be created is reported in sections_created as
            'would create folder "…"', and its cases come back with
            section_id null.

    Returns JSON:
        {"dry_run": bool,
         "created": [{"index", "id", "title", "section_id", "section_path", "url", "updated_on"}],
         "skipped": [{"index", "title", "reason", "section_path"}],
         "failed":  [{"index", "title", "error"}],
         "sections_created": ["Login/Negative cases"]}

    Always surface `failed` to the user — a silently dropped case is a test that
    never gets written.
    """
    if not cases:
        return json.dumps({"error": "No cases supplied — `cases` is empty."}, indent=2)

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    sections_created: list[str] = []

    # Resolved once per distinct target, so a 40-case batch into one folder costs
    # one section lookup and one duplicate-check, not forty. A dry-run folder that
    # does not exist yet has no id, so its titles are tracked under its path.
    resolved: dict[tuple[int | None, str | None], tuple[int | None, str]] = {}
    seen_titles: dict[int | str, set[str]] = {}

    for index, spec in enumerate(cases):
        if not isinstance(spec, dict):
            failed.append({"index": index, "title": None, "error": "Case entry is not an object."})
            continue

        target_id = spec.get("section_id", section_id)
        target_path = spec.get("section_path", section_path)
        key = (target_id, target_path)

        try:
            if key not in resolved:
                resolved_id, resolved_path, notes = await _resolve_section(
                    project_id=project_id,
                    suite_id=suite_id,
                    section_id=target_id,
                    section_path=target_path,
                    create_missing=create_missing_sections,
                    dry_run=dry_run,
                )
                resolved[key] = (resolved_id, resolved_path)
                sections_created.extend(n for n in notes if n not in sections_created)
                titles_key = resolved_id if resolved_id is not None else f"new:{resolved_path.casefold()}"
                if skip_if_title_exists and titles_key not in seen_titles:
                    seen_titles[titles_key] = (
                        await _existing_titles(project_id, suite_id, resolved_id)
                        if resolved_id is not None
                        else set()
                    )
            dest_id, dest_path = resolved[key]
            titles_key = dest_id if dest_id is not None else f"new:{dest_path.casefold()}"
            payload = _build_case_payload(spec)
        except TestRailError as exc:
            failed.append({"index": index, "title": spec.get("title"), "error": str(exc)})
            continue

        normalized = payload["title"].strip().casefold()
        if skip_if_title_exists and normalized in seen_titles.get(titles_key, set()):
            skipped.append(
                {
                    "index": index,
                    "title": payload["title"],
                    "reason": "a case with this title already exists in the folder",
                    "section_path": dest_path,
                }
            )
            continue

        if dry_run:
            created.append(
                {
                    "index": index,
                    "id": None,
                    "title": payload["title"],
                    "section_id": dest_id,
                    "section_path": dest_path,
                    "url": None,
                    "updated_on": None,
                }
            )
            seen_titles.setdefault(titles_key, set()).add(normalized)
            continue

        try:
            result = await _post(f"add_case/{dest_id}", payload)
        except TestRailError as exc:
            failed.append({"index": index, "title": payload["title"], "error": str(exc)})
            continue

        case_id = result.get("id")
        created.append(
            {
                "index": index,
                "id": case_id,
                "title": result.get("title", payload["title"]),
                "section_id": dest_id,
                "section_path": dest_path,
                "url": f"{BASE_URL}/index.php?/cases/view/{case_id}" if case_id else None,
                "updated_on": result.get("updated_on"),
            }
        )
        seen_titles.setdefault(titles_key, set()).add(normalized)

    return json.dumps(
        {
            "dry_run": dry_run,
            "created": created,
            "skipped": skipped,
            "failed": failed,
            "sections_created": sections_created,
        },
        indent=2,
    )


@mcp.tool(structured_output=False)
async def testrail_update_case(
    case_id: int | str,
    title: str | None = None,
    type_id: int | None = None,
    priority_id: int | None = None,
    estimate: str | None = None,
    milestone_id: int | None = None,
    refs: str | None = None,
    preconditions: str | None = None,
    steps: str | None = None,
    expected: str | None = None,
    steps_separated: list[dict[str, Any]] | None = None,
    custom_fields: dict[str, Any] | None = None,
    requirement_field: str | None = None,
    expected_updated_on: int | None = None,
    allow_unlink: bool = False,
    dry_run: bool = False,
) -> str:
    """Update ONE existing test case, changing only the fields you pass. WRITES to TestRail.

    The single-case wrapper around testrail_update_cases — same guards, same
    response shape. Omit a field to leave it alone; pass "" to clear it.

    Args:
        case_id: 1234, "C1234", or "TC-1234".
        title, type_id, priority_id, estimate, milestone_id, refs: as in testrail_add_case.
        preconditions, steps, expected: map to custom_preconds, custom_steps, custom_expected.
        steps_separated: REPLACES the whole step list — send every step, not just the changed one.
        custom_fields: any other custom fields, {"my_field": value}; custom_ prefix optional.
        requirement_field: the custom field holding requirement IDs (test_cases.authoring.requirement_field).
            Pass it whenever you have it: IDs in it are then protected like refs.
        expected_updated_on: the case's updated_on from when you read it. If TestRail's value
            differs, someone edited the case since, and the update is refused.
        allow_unlink: set only when the user explicitly asked to remove a requirement link.
        dry_run: diff against the live case and report what would change, writing nothing.
    """
    spec: dict[str, Any] = {
        "title": title,
        "type_id": type_id,
        "priority_id": priority_id,
        "estimate": estimate,
        "milestone_id": milestone_id,
        "refs": refs,
        "preconditions": preconditions,
        "steps": steps,
        "expected": expected,
        "steps_separated": steps_separated,
        "custom_fields": custom_fields,
    }
    entry: dict[str, Any] = {key: value for key, value in spec.items() if value is not None}
    entry["case_id"] = case_id
    if expected_updated_on is not None:
        entry["expected_updated_on"] = expected_updated_on
    if allow_unlink:
        entry["allow_unlink"] = True
    return await testrail_update_cases(cases=[entry], requirement_field=requirement_field, dry_run=dry_run)


@mcp.tool(structured_output=False)
async def testrail_update_cases(
    cases: list[dict[str, Any]],
    requirement_field: str | None = None,
    dry_run: bool = False,
    stop_on_dropped_field: bool = True,
) -> str:
    """Update existing test cases, each with its own changes. WRITES to TestRail.

    TestRail's bulk update_cases endpoint can only set the SAME values on every
    case, so this loops update_case. Each case is read first and only the fields
    that actually differ are sent; a case with nothing to change is left alone,
    so re-running a batch adds no history noise. A 429 is retried with Retry-After.

    Guards, per case:
      - Requirement links: an update that would remove an ID from `refs`, or from
        `requirement_field` when given, is BLOCKED unless that entry sets
        allow_unlink. Losing a link turns the case into an orphan and leaves its
        requirement reading untested.
      - Conflicts: with expected_updated_on, a case edited in TestRail since it was
        read is BLOCKED rather than overwritten.
      - Read-back: every sent field is compared with the case TestRail returns.
        TestRail accepts unknown custom fields and silently drops them, so a field
        that did not stick is listed in dropped_fields — and, with
        stop_on_dropped_field (default), the rest of the batch is not attempted,
        because the cause (a wrong field name, a field not on the template) is
        usually the same for every case.
      - Template and folder changes are refused: do those in TestRail itself.

    Args:
        cases: one entry per case — `case_id` (1234, "C1234", or "TC-1234") plus the
            fields to change, using the same keys as testrail_add_case (title,
            type_id, priority_id, estimate, milestone_id, refs, preconditions,
            steps, expected, steps_separated, custom_fields), and optionally
            `expected_updated_on` and `allow_unlink`. Omit a field to leave it
            alone; pass "" to clear it. steps_separated replaces the whole list.
        requirement_field: the custom field holding requirement IDs
            (test_cases.authoring.requirement_field), protected like refs.
        dry_run: read and diff every case, write nothing. Each planned entry
            carries the case's current updated_on — pass it back as
            expected_updated_on when applying the plan.
        stop_on_dropped_field: stop after the first case whose write did not stick.

    Returns JSON:
        {"dry_run": bool,
         "updated":   [{"index", "case_id", "title", "url", "changes": {field: {"before", "after"}},
                        "warnings", "updated_on", "dropped_fields"}],
         "unchanged": [{"index", "case_id", "title", "url", "updated_on"}],
         "blocked":   [{"index", "case_id", "title", "url", "reason", ...}],
         "failed":    [{"index", "case_id", "error"}],
         "not_attempted": [{"index", "case_id"}],
         "stopped": null | "why the batch stopped"}

    `changes[*].before` is the value each field had — what to send back to undo.
    Always surface blocked, failed, dropped_fields, and not_attempted to the user.
    """
    if not cases:
        return json.dumps({"error": "No cases supplied — `cases` is empty."}, indent=2)

    token_fields = {"refs"}
    if requirement_field and str(requirement_field).strip():
        token_fields.add(_custom_key(requirement_field))

    updated: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_attempted: list[dict[str, Any]] = []
    stopped: str | None = None
    seen: set[int] = set()

    for index, spec in enumerate(cases):
        raw_id = spec.get("case_id") if isinstance(spec, dict) else None
        if stopped:
            not_attempted.append({"index": index, "case_id": raw_id})
            continue
        if not isinstance(spec, dict):
            failed.append({"index": index, "case_id": None, "error": "Case entry is not an object."})
            continue
        try:
            case_id = _parse_case_id(raw_id)
            payload = _build_update_payload(spec)
        except TestRailError as exc:
            failed.append({"index": index, "case_id": raw_id, "error": str(exc)})
            continue
        if case_id in seen:
            failed.append(
                {
                    "index": index,
                    "case_id": case_id,
                    "error": f"Case {case_id} appears twice in this batch; merge its changes into one entry.",
                }
            )
            continue
        seen.add(case_id)

        try:
            current = await _get(f"get_case/{case_id}")
        except TestRailError as exc:
            failed.append({"index": index, "case_id": case_id, "error": str(exc)})
            continue

        base = {
            "index": index,
            "case_id": case_id,
            "title": current.get("title"),
            "url": f"{BASE_URL}/index.php?/cases/view/{case_id}",
        }

        expected_on = spec.get("expected_updated_on")
        if expected_on is not None and _as_int(current.get("updated_on")) != _as_int(expected_on):
            blocked.append(
                {
                    **base,
                    "reason": "changed in TestRail since it was read — re-read it and re-plan",
                    "expected_updated_on": expected_on,
                    "updated_on": current.get("updated_on"),
                }
            )
            continue

        body = {field: value for field, value in payload.items() if not _same(field, current.get(field), value, token_fields)}
        if not body:
            unchanged.append({**base, "updated_on": current.get("updated_on")})
            continue

        removed = _removed_links(current, body, token_fields)
        if removed and not spec.get("allow_unlink"):
            blocked.append(
                {
                    **base,
                    "reason": "would remove requirement links — pass allow_unlink only if the user asked for that",
                    "removed": removed,
                }
            )
            continue

        changes = {field: {"before": current.get(field), "after": value} for field, value in body.items()}
        entry = {**base, "changes": changes, "warnings": _update_warnings(current, body)}
        if dry_run:
            updated.append({**entry, "updated_on": current.get("updated_on"), "dropped_fields": []})
            continue

        try:
            result = await _post(f"update_case/{case_id}", body)
            if not isinstance(result, dict) or result.get("id") is None:
                result = await _get(f"get_case/{case_id}")
        except TestRailError as exc:
            failed.append({"index": index, "case_id": case_id, "title": base["title"], "error": str(exc)})
            continue

        dropped = [field for field, value in body.items() if not _same(field, result.get(field), value, token_fields)]
        updated.append({**entry, "updated_on": result.get("updated_on"), "dropped_fields": dropped})
        if dropped and stop_on_dropped_field:
            stopped = (
                f"Case {case_id} accepted the update but did not store {', '.join(dropped)} — usually a wrong "
                "field name or a field not on the case's template. Check testrail_get_case_fields; the "
                "remaining cases were not attempted."
            )

    return json.dumps(
        {
            "dry_run": dry_run,
            "updated": updated,
            "unchanged": unchanged,
            "blocked": blocked,
            "failed": failed,
            "not_attempted": not_attempted,
            "stopped": stopped,
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
