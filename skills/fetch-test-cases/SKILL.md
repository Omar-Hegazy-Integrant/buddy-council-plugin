---
description: Fetch test cases from the configured source. Reads config/sources.json and delegates to the correct provider, which uses MCP tools for data access.
---

# Fetch Test Cases — Router Skill

Fetch test cases from the configured source. This skill reads the user's configuration and delegates to the correct provider.

## How It Works

1. Read `config/sources.json` from the plugin root (`${CLAUDE_PLUGIN_ROOT}/config/sources.json`)
2. Check the `test_cases.provider` field
3. Delegate to the matching provider:

| Provider | Delegate To |
|----------|-------------|
| `testrail` | Follow instructions in `${CLAUDE_PLUGIN_ROOT}/providers/testrail/fetch.md` |

4. **IMPORTANT**: The provider skill will instruct you to use specific MCP tools. Use those MCP tools directly — do NOT use curl or Bash to call APIs.
5. Return the results in canonical schema format

## Input

- `scope`: Optional — test case ID, section/feature name, or "all". Passed via $ARGUMENTS.

## Error Handling

- If `config/sources.json` does not exist → tell the user to run `/bc:setup` first
- If the configured provider file does not exist → report which provider was expected and that it's not yet implemented
- If MCP tools specified by the provider are not available → tell the user to check `.mcp.json` and restart Claude Code
