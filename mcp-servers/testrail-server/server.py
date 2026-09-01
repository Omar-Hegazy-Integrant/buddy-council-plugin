"""TestRail MCP Server — read access to TestRail's REST API, plus guarded case creation.

Every `testrail_get_*` tool is read-only. The write tools — `testrail_add_case`,
`testrail_add_cases`, and `testrail_add_section` — mutate the team's TestRail
instance and are deliberately excluded from the plugin's auto-approve hook and
from `settings.json`, so they always reach a permission prompt.
"""

import asyncio
import os
import sys
import json
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

    Retries on 429 (TestRail Cloud's rate limit), honouring Retry-After. Raises
    TestRailError carrying the server's own message on any other 4xx/5xx, because
    "HTTP 400" alone is useless when the real cause is a required custom field.
    """
    url = f"{_API_BASE}{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url = f"{url}&{query}"

    for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
        response = await _client.request(method, url, json=payload)
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
    """POST JSON to a TestRail endpoint and return parsed JSON."""
    return await _request("POST", endpoint, payload=payload)


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
) -> tuple[int, str, list[str]]:
    """Resolve the target folder to a section id.

    Returns (section_id, human-readable path, notes about folders created).

    A numeric section_id is *validated* against the suite rather than trusted:
    a wrong id writes cases into someone else's folder, which is the failure
    mode worth one extra GET to prevent.
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
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("testrail")


@mcp.tool()
async def testrail_get_projects() -> str:
    """List all projects in the TestRail instance.

    Returns a JSON array of projects with id, name, announcement, and is_completed fields.
    Used during setup to let the user pick which project to analyze.
    """
    data = await _get("get_projects")
    projects = data.get("projects", data) if isinstance(data, dict) else data
    return json.dumps(projects, indent=2)


@mcp.tool()
async def testrail_get_suites(project_id: int) -> str:
    """List all test suites for a given project.

    Args:
        project_id: The TestRail project ID.

    Returns a JSON array of suites with id, name, and description fields.
    """
    data = await _get(f"get_suites/{project_id}")
    return json.dumps(data, indent=2)


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
async def testrail_get_case(case_id: int) -> str:
    """Fetch a single test case by its ID.

    Args:
        case_id: The TestRail case ID (numeric, without TC- prefix).

    Returns the full test case object with all fields including custom fields.
    """
    data = await _get(f"get_case/{case_id}")
    return json.dumps(data, indent=2)


@mcp.tool()
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


@mcp.tool()
async def testrail_get_case_types() -> str:
    """List the available case types (Functional, Regression, Smoke, ...).

    Returns a JSON array of {id, name, is_default}. Use the id as type_id when
    creating a case — TestRail takes ids, not names.
    """
    data = await _get("get_case_types")
    return json.dumps(data, indent=2)


@mcp.tool()
async def testrail_get_priorities() -> str:
    """List the available case priorities (Low, Medium, High, Critical, ...).

    Returns a JSON array of {id, name, short_name, priority, is_default}. Use the
    id as priority_id when creating a case.
    """
    data = await _get("get_priorities")
    return json.dumps(data, indent=2)


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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
            nothing. Use it to preview a batch before committing.

    Returns JSON:
        {"dry_run": bool,
         "created": [{"index", "id", "title", "section_id", "section_path", "url"}],
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
    # one section lookup and one duplicate-check, not forty.
    resolved: dict[tuple[int | None, str | None], tuple[int, str]] = {}
    seen_titles: dict[int, set[str]] = {}

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
                    create_missing=create_missing_sections and not dry_run,
                )
                resolved[key] = (resolved_id, resolved_path)
                sections_created.extend(notes)
                if skip_if_title_exists and resolved_id not in seen_titles:
                    seen_titles[resolved_id] = await _existing_titles(
                        project_id, suite_id, resolved_id
                    )
            dest_id, dest_path = resolved[key]
            payload = _build_case_payload(spec)
        except TestRailError as exc:
            failed.append({"index": index, "title": spec.get("title"), "error": str(exc)})
            continue

        normalized = payload["title"].strip().casefold()
        if skip_if_title_exists and normalized in seen_titles.get(dest_id, set()):
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
                }
            )
            seen_titles.setdefault(dest_id, set()).add(normalized)
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
            }
        )
        seen_titles.setdefault(dest_id, set()).add(normalized)

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


if __name__ == "__main__":
    mcp.run()
