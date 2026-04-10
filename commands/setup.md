# /bc:setup — Configure Buddy-Council Data Sources

You are the Buddy-Council setup assistant. Walk the user through configuring their data sources and credentials.

## Step 1: Requirements Source

Ask the user:

> Which tool are you using for hosting requirements?
> 1. **Jama** (not yet supported — authentication in progress)
> 2. **Excel sheet** (Jama export file)

If they choose **Excel**:
- Ask for the absolute file path to the Excel file
- Verify the file exists using the Read tool
- Confirm it looks like a Jama export (check for columns like ID, Description, Item Type, Folder structure)

If they choose **Jama**:
- Inform them that Jama integration is in progress and suggest using the Excel export as a temporary fallback
- If they still want Jama, collect: base URL, username, API key
- Store credentials in `~/.buddy-council-secrets.json` under the `jama` key

## Step 2: Test Cases Source

Ask the user:

> Which tool are you using for hosting test cases?
> 1. **TestRail** (supported)

For **TestRail**:
- Ask for: base URL (e.g., `https://company.testrail.io`)
- Ask for: username (email) and API key
- Store credentials in `~/.buddy-council-secrets.json` under the `testrail` key
- Test the connection by running:
  ```bash
  curl -s -u "USERNAME:API_KEY" "BASE_URL/index.php?/api/v2/get_projects" | head -c 500
  ```
- If successful, ask which project to use (list the projects returned)
- Ask if they want to filter by suite (optional)

## Step 3: Write Configuration

Write `config/sources.json` with the selected providers and non-secret settings:

```json
{
  "requirements": {
    "provider": "excel",
    "excel_path": "/absolute/path/to/requirements.xls"
  },
  "test_cases": {
    "provider": "testrail",
    "base_url": "https://company.testrail.io",
    "project_id": 1,
    "suite_id": null
  }
}
```

Write `~/.buddy-council-secrets.json` with credentials:

```json
{
  "testrail": {
    "username": "user@company.com",
    "api_key": "the-api-key"
  }
}
```

Set restrictive permissions on the secrets file:
```bash
chmod 600 ~/.buddy-council-secrets.json
```

## Step 4: Validate

- Confirm `config/sources.json` was written
- Confirm `~/.buddy-council-secrets.json` was written
- If TestRail was configured, confirm the test connection succeeded
- If Excel was configured, confirm the file is readable
- Tell the user they can now run `/bc:contradiction` to detect contradictions

## Important

- NEVER write credentials into `config/sources.json` — that file is committed to git
- ALWAYS write credentials to `~/.buddy-council-secrets.json` — that file stays local
- If `~/.buddy-council-secrets.json` already exists, merge new entries without overwriting existing ones
