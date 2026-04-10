---
description: Fetch requirements from the configured source (Excel or Jama). Reads config/sources.json and delegates to the correct provider.
---

# Fetch Requirements — Router Skill

Fetch requirements from the configured source. This skill reads the user's configuration and delegates to the correct provider.

## How It Works

1. Read `config/sources.json` from the plugin root (`${CLAUDE_PLUGIN_ROOT}/config/sources.json`)
2. Check the `requirements.provider` field
3. Delegate to the matching provider:

| Provider | Delegate To |
|----------|-------------|
| `excel` | Follow instructions in `${CLAUDE_PLUGIN_ROOT}/providers/excel/fetch.md` |
| `jama` | Follow instructions in `${CLAUDE_PLUGIN_ROOT}/providers/jama/fetch.md` |

4. Return the results in canonical schema format

## Input

- `scope`: Optional — requirement ID, feature name, or "all". Passed via $ARGUMENTS.

## Error Handling

- If `config/sources.json` does not exist → tell the user to run `/bc:setup` first
- If the configured provider file does not exist → report which provider was expected and that it's not yet implemented
- If credentials are missing from `~/.buddy-council-secrets.json` → tell the user to run `/bc:setup` to configure credentials
