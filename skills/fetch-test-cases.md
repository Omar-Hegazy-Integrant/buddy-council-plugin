# Fetch Test Cases — Router Skill

Fetch test cases from the configured source. This skill reads the user's configuration and delegates to the correct provider.

## How It Works

1. Read `config/sources.json` from the plugin directory
2. Check the `test_cases.provider` field
3. Delegate to the matching provider:

| Provider | Delegate To |
|----------|-------------|
| `testrail` | Follow instructions in `providers/testrail/fetch.md` |

4. Return the results in canonical schema format

## Input

- `scope`: Optional — test case ID, section/feature name, or "all"

## Error Handling

- If `config/sources.json` does not exist → tell the user to run `/bc:setup` first
- If the configured provider file does not exist → report which provider was expected and that it's not yet implemented
- If credentials are missing from `~/.buddy-council-secrets.json` → tell the user to run `/bc:setup` to configure credentials
