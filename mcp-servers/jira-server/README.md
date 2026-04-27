# Jira Cloud MCP Server

MCP server wrapping the Jira Cloud REST API for creating and reading issues.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_BASE_URL` | Yes | Jira instance URL (e.g., `https://yourorg.atlassian.net`) |
| `JIRA_EMAIL` | Yes | Jira account email |
| `JIRA_API_TOKEN` | Yes | Jira API token (from Account Settings > Security > API tokens) |

## Getting a Jira API Token

1. Log in to [Atlassian Account](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Give it a label (e.g., "Buddy-Council MCP")
4. Copy the token immediately (you won't be able to see it again)

## Setup

```bash
cd mcp-servers/jira-server
uv pip install -e .
```

Or with pip:
```bash
pip install -e .
```

## Running Standalone

```bash
# With uv
uv run mcp run server.py

# With python directly
python server.py
```

## Testing with MCP Inspector

```bash
mcp dev server.py
```

## Available Tools

| Tool | Description | Read/Write |
|------|-------------|------------|
| `jira_get_projects` | List all projects | Read |
| `jira_get_issue_types` | Get available issue types for a project | Read |
| `jira_get_issue` | Fetch a single issue by key | Read |
| `jira_create_issue` | Create a new issue (supports dry-run mode) | Write |

## Dry-Run Mode

The `jira_create_issue` tool supports a `dry_run` parameter. When `dry_run=True`:
- No API call is made to Jira
- Returns a mock issue key in the format `DRY-RUN-XXXXXXXX`
- Useful for testing workflows without creating real tickets

Example:
```python
await jira_create_issue(
    project_key="PROJ",
    summary="Add login button",
    description="Button for user authentication",
    dry_run=True  # No real ticket created
)
```

Returns:
```json
{
  "key": "DRY-RUN-A3F2B1C4",
  "id": "dry-run-a3f2b1c4",
  "self": "https://yourorg.atlassian.net/browse/DRY-RUN-A3F2B1C4",
  "dry_run": true
}
```

## Jira Markdown Format

The `description` field in `jira_create_issue` supports Jira's Atlassian Document Format (ADF). Currently, plain text is converted to ADF automatically. For more complex formatting:

**Supported in plain text:**
- Paragraphs (separated by blank lines)
- Inline formatting: `*bold*`, `_italic_`, `{{monospace}}`
- Lists (bullets and numbered)
- Code blocks
- Headings (use `## Heading` syntax in your text)

**Example with Acceptance Criteria:**
```markdown
This ticket adds a login button to the checkout page.

## Acceptance Criteria
- Button displays "Sign In" text
- Clicking button redirects to /login
- Button is disabled when user is already logged in

## Technical Notes
- Use existing AuthButton component
- Add analytics event on click
```

## Configuration in .mcp.json

Add this to your `.mcp.json` in the plugin root:

```json
{
  "mcpServers": {
    "jira": {
      "command": "uv",
      "args": ["run", "--directory", "${CLAUDE_PLUGIN_ROOT}/mcp-servers/jira-server", "mcp", "run", "server.py"],
      "env": {
        "JIRA_BASE_URL": "https://yourorg.atlassian.net",
        "JIRA_EMAIL": "your.email@company.com",
        "JIRA_API_TOKEN": "your-api-token-here"
      }
    }
  }
}
```

**Note:** `.mcp.json` should be in your `.gitignore` to keep credentials secure.

## Permissions

The API token must have permission to:
- Browse projects
- View issues
- Create issues

If you encounter 403 Forbidden errors, verify your token has these permissions in your Jira project settings.
