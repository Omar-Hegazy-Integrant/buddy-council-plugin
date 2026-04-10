# Jama Requirements Fetch — Provider Skill (Future)

Fetch requirements directly from the Jama REST API.

## Status

**Not yet implemented.** Jama authentication is currently blocked. Use the Excel provider (`providers/excel/fetch.md`) as a temporary fallback.

## When Ready

This provider will need:
- Jama base URL
- Authentication (OAuth2 or Basic Auth with API key)
- API endpoints:
  - `GET /rest/latest/items?project=PROJECT_ID` — fetch items
  - `GET /rest/latest/items/{id}` — fetch single item
  - `GET /rest/latest/items/{id}/children` — fetch children (for folder hierarchy)
  - `GET /rest/latest/relationships/{id}` — fetch links between items

## Output

Same canonical schema as the Excel provider — the router skill (`skills/fetch-requirements.md`) will delegate transparently.

## Migration Path

1. Implement Jama API client in this file
2. Add `jama` credentials to `~/.buddy-council-secrets.json`
3. User runs `/bc:setup` and selects "Jama" instead of "Excel"
4. `config/sources.json` changes `provider` from `"excel"` to `"jama"`
5. No changes needed in agents, skills, or contradiction logic
