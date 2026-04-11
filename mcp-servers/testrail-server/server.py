"""TestRail MCP Server — read-only access to TestRail's REST API."""

import os
import sys
import json
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("TESTRAIL_BASE_URL", "").rstrip("/")
USERNAME = os.environ.get("TESTRAIL_USERNAME", "")
API_KEY = os.environ.get("TESTRAIL_API_KEY", "")

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
        f"ERROR: Missing required environment variables: {', '.join(missing)}. "
        "Set them in .mcp.json env block or export them before starting.",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_API_BASE = f"{BASE_URL}/index.php?/api/v2/"

_client = httpx.AsyncClient(
    auth=(USERNAME, API_KEY),
    headers={"Content-Type": "application/json"},
    timeout=30.0,
)


async def _get(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    """Make a GET request to the TestRail API and return parsed JSON.

    TestRail uses index.php?/api/v2/<endpoint>&key=val for query params,
    so we build the URL manually rather than using httpx base_url + params.
    """
    url = f"{_API_BASE}{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url = f"{url}&{query}"
    response = await _client.get(url)
    response.raise_for_status()
    return response.json()


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

    Returns a JSON array of sections with id, name, parent_id, and depth fields.
    Used to resolve section_id values to human-readable feature names.
    """
    params: dict[str, Any] = {}
    if suite_id is not None:
        params["suite_id"] = suite_id
    data = await _get(f"get_sections/{project_id}", params=params)
    sections = data.get("sections", data) if isinstance(data, dict) else data
    return json.dumps(sections, indent=2)


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
async def testrail_get_case(case_id: int) -> str:
    """Fetch a single test case by its ID.

    Args:
        case_id: The TestRail case ID (numeric, without TC- prefix).

    Returns the full test case object with all fields including custom fields.
    """
    data = await _get(f"get_case/{case_id}")
    return json.dumps(data, indent=2)


if __name__ == "__main__":
    mcp.run()
