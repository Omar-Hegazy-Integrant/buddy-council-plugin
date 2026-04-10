# Fetch Requirements — Router Skill

Fetch requirements from the configured source. This skill reads the user's configuration and delegates to the correct provider.

## How It Works

1. Read `config/sources.json` from the plugin directory
2. Check the `requirements.provider` field
3. Delegate to the matching provider:

| Provider | Delegate To |
|----------|-------------|
| `excel` | Follow instructions in `providers/excel/fetch.md` |
| `jama` | Follow instructions in `providers/jama/fetch.md` |

4. Return the results in canonical schema format

## Input

- `scope`: Optional — requirement ID, feature name, or "all"

## Error Handling

- If `config/sources.json` does not exist → tell the user to run `/bc:setup` first
- If the configured provider file does not exist → report which provider was expected and that it's not yet implemented
- If credentials are missing from `~/.buddy-council-secrets.json` → tell the user to run `/bc:setup` to configure credentials
