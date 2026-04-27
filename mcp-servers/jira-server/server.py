"""Jira Cloud MCP Server — create and read Jira issues via REST API."""

import os
import sys
import json
from typing import Any
import uuid

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
EMAIL = os.environ.get("JIRA_EMAIL", "")
API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")

if not all([BASE_URL, EMAIL, API_TOKEN]):
    missing = [
        name
        for name, val in [
            ("JIRA_BASE_URL", BASE_URL),
            ("JIRA_EMAIL", EMAIL),
            ("JIRA_API_TOKEN", API_TOKEN),
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

_API_BASE = f"{BASE_URL}/rest/api/3"

_client = httpx.AsyncClient(
    auth=(EMAIL, API_TOKEN),
    headers={"Content-Type": "application/json", "Accept": "application/json"},
    timeout=30.0,
)


async def _get(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    """Make a GET request to the Jira API and return parsed JSON."""
    url = f"{_API_BASE}/{endpoint}"
    response = await _client.get(url, params=params)
    response.raise_for_status()
    return response.json()


async def _post(endpoint: str, data: dict[str, Any]) -> Any:
    """Make a POST request to the Jira API and return parsed JSON."""
    url = f"{_API_BASE}/{endpoint}"
    response = await _client.post(url, json=data)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("jira")


@mcp.tool()
async def jira_get_projects() -> str:
    """List all projects in the Jira instance.

    Returns a JSON array of projects with id, key, name, and projectTypeKey fields.
    Used during setup to let the user pick which project to create tickets in.
    """
    data = await _get("project")
    return json.dumps(data, indent=2)


@mcp.tool()
async def jira_get_issue_types(project_key: str) -> str:
    """Get available issue types for a project.

    Args:
        project_key: The Jira project key (e.g., "PROJ").

    Returns a JSON array of issue types with id, name, description, and subtask fields.
    Common types: Story, Task, Bug, Epic.
    """
    data = await _get(f"project/{project_key}/statuses")
    # Extract unique issue types from the statuses response
    issue_types = []
    seen_ids = set()
    for status_group in data:
        if "id" in status_group and status_group["id"] not in seen_ids:
            seen_ids.add(status_group["id"])
            issue_types.append({
                "id": status_group["id"],
                "name": status_group.get("name", ""),
                "description": status_group.get("description", ""),
                "subtask": status_group.get("subtask", False),
            })
    return json.dumps(issue_types, indent=2)


@mcp.tool()
async def jira_get_issue(issue_key: str) -> str:
    """Get a single Jira issue by key.

    Args:
        issue_key: The Jira issue key (e.g., "PROJ-123").

    Returns a JSON object with issue fields including summary, description, status, etc.
    """
    data = await _get(f"issue/{issue_key}")
    return json.dumps(data, indent=2)


@mcp.tool()
async def jira_create_issue(
    project_key: str,
    summary: str,
    description: str,
    issue_type: str = "Story",
    dry_run: bool = False,
) -> str:
    """Create a new Jira issue.

    Args:
        project_key: The Jira project key (e.g., "PROJ").
        summary: The issue title/summary (one line).
        description: The issue description (supports Jira markdown).
        issue_type: The issue type (default: "Story"). Common values: Story, Task, Bug, Epic.
        dry_run: If True, return a mock issue key without creating a real ticket.

    Returns a JSON object with:
    - key: The created issue key (e.g., "PROJ-123" or "DRY-RUN-xxxxx" for dry runs)
    - id: The issue ID
    - self: URL to the issue

    Example description with acceptance criteria:
    ```
    This ticket adds a login button to the checkout page.

    ## Acceptance Criteria
    - Button displays "Sign In" text
    - Clicking button redirects to /login
    - Button is disabled when user is already logged in
    ```
    """
    if dry_run:
        # Generate a mock issue key for dry-run mode
        mock_id = str(uuid.uuid4())[:8].upper()
        mock_response = {
            "key": f"DRY-RUN-{mock_id}",
            "id": f"dry-run-{mock_id}",
            "self": f"{BASE_URL}/browse/DRY-RUN-{mock_id}",
            "dry_run": True,
        }
        return json.dumps(mock_response, indent=2)

    # Real issue creation
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": description,
                            }
                        ]
                    }
                ]
            },
            "issuetype": {"name": issue_type},
        }
    }

    data = await _post("issue", payload)
    return json.dumps(data, indent=2)
