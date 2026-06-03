---
description: Fetch requirements from the configured source (Excel or Jama). Reads .buddy-council/sources.json and delegates to the correct provider.
---

# Fetch Requirements — Router Skill

Fetch requirements from the configured source. This skill reads the user's configuration and delegates to the correct provider.

## How It Works

1. Read `.buddy-council/sources.json` from the project root (the current working directory)
2. Check the `requirements.provider` field
3. Delegate to the matching provider:

| Provider | Delegate To |
|----------|-------------|
| `excel` | Follow instructions in `${CLAUDE_PLUGIN_ROOT}/providers/excel/fetch.md` |
| `jama` | Follow instructions in `${CLAUDE_PLUGIN_ROOT}/providers/jama/fetch.md` |

4. **Enrichment step (MANDATORY when enabled)** — if `requirements.enrichment.enabled === true`, you **must** follow `${CLAUDE_PLUGIN_ROOT}/skills/enrich-requirements/SKILL.md` to fetch any GitHub-hosted docs referenced via the provider's transient `_enrichment_urls` field. Do not skip this when it is enabled. After it runs, print a one-line status — `Enrichment: fetched K of N GitHub-linked requirement docs` (or the failure/abort summary the skill returns). The enrich skill attaches `extended_context` per requirement and removes the transient field.
5. **Enrichment visibility** — always make the enrichment outcome observable so the user is never left guessing:
   - `enabled === true` and docs were fetched → the Step 4 status line already reported the counts.
   - `enabled === true` but the provider produced no `_enrichment_urls` (e.g. no `github_url` column mapped, or no GitHub links in the rows) → print: "Note: enrichment is on, but no GitHub doc links were found in the requirements source."
   - `enabled === false` but the provider produced any `_enrichment_urls` → print: "Note: this sheet has GitHub doc links but enrichment is disabled. Run `/bc:setup` to enable." Strip the field before returning.
   In all cases, do NOT block the fetch.
6. Return the results in canonical schema format.

## Input

- `scope`: Optional — requirement ID, feature name, or "all". Passed via $ARGUMENTS.

## Error Handling

- If `.buddy-council/sources.json` does not exist → tell the user to run `/bc:setup` first
- If the configured provider file does not exist → report which provider was expected and that it's not yet implemented
- If credentials are missing from `~/.buddy-council/secrets.json` → tell the user to run `/bc:setup` to configure credentials
- If the enrichment skill returns an `abort` error (user chose to abort during the failure prompt) → return that error to the caller so the command can decide whether to fail outright or proceed without enrichment
