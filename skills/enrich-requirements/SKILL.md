---
description: Provider-agnostic enrichment orchestrator. For each requirement with _enrichment_urls, fetches the referenced GitHub markdown via the configured strategy (gh CLI or github-mcp-server), resolves image refs, attaches extended_context, and removes the transient field. Handles failures with a once-per-run user prompt.
---

# Enrich Requirements — Skill

After requirements have been fetched from their provider (Excel, Jama) but before normalization, fetch any externally-referenced documentation (GitHub-hosted markdown today) and merge it into each requirement as `extended_context`. Every downstream consumer (`/bc:contradiction`, `/bc:coverage`, `/bc:onboarding`, `/bc:validate`) gets richer reasoning input without changes.

## When to Use

Invoked by `skills/fetch-requirements/SKILL.md` after the provider returns, when `requirements.enrichment.enabled === true` in `config/sources.json`. If enrichment is disabled, this skill is NOT called — the transient `_enrichment_urls` field passes through and is dropped by `normalize-artifacts`.

## Input

- `requirements`: array of requirement objects from a provider. Some may carry a transient `_enrichment_urls: [{ source, url }]` field.

## Configuration

Read `config/sources.json` `requirements.enrichment`:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `enabled` | boolean | `false` | Master switch — if false, this skill is not invoked |
| `strategy` | string | `"cli"` | `"cli"` → use `providers/github/fetch-cli.md`; `"mcp"` → use `providers/github/fetch-mcp.md` |
| `max_doc_chars` | integer | `50000` | Hard safety ceiling on raw doc size before user is prompted |

## Procedure

### Step 1: Collect URLs and dedupe

Walk every requirement. Build a `Map<url, {source, requirementIds: [string]}>` from each requirement's `_enrichment_urls`. Same URL referenced from multiple requirements → one entry, all referring requirement IDs collected.

If the map is empty, return the requirements unchanged (with `_enrichment_urls` stripped if any were empty arrays).

### Step 2: Per-run failure state

Maintain three in-memory variables for the duration of this skill invocation:

- `failure_choice: null | "continue" | "skip_all" | "abort"` — initialized to `null`
- `oversize_choice: null | "truncate_all" | "skip_all_oversize" | "ask_each"` — initialized to `"ask_each"`
- `processed: Map<url, result>` — dedupe + cache for THIS run only

These do NOT persist across runs. If the user aborts, the skill returns an error to the calling router.

### Step 3: Fetch each unique URL

For each `(url, source)` in the dedupe map:

1. **Strip query params** from the URL (`?plain=1` etc.). Preserve the fragment (`#L42-L60`) — that's used by the locator.
2. **Verify the URL pattern**. For `source === "github"`, require `^https://github\.com/` (or `^https://raw\.githubusercontent\.com/`). Non-matching URLs: skip silently, log a one-line warning, do NOT prompt.
3. **Delegate to the strategy provider**:
   - `enrichment.strategy === "cli"` → follow `${CLAUDE_PLUGIN_ROOT}/providers/github/fetch-cli.md`
   - `enrichment.strategy === "mcp"` → follow `${CLAUDE_PLUGIN_ROOT}/providers/github/fetch-mcp.md`
4. **Handle the provider response**:
   - **Success** with `content` → continue to Step 4
   - **Error** → invoke the failure prompt (Step 5)

### Step 4: Process successful content

For each successful fetch:

#### 4a. Safety ceiling

If `raw_size > max_doc_chars` (default 50000):

- If `oversize_choice === "truncate_all"` → truncate to `max_doc_chars` chars at a character boundary, set `truncated: true`, append a tail line `\n\n[...truncated, see <URL> for full document]`.
- If `oversize_choice === "skip_all_oversize"` → skip this URL, log a warning, do NOT attach.
- If `oversize_choice === "ask_each"` → prompt the user:

  ```
  Doc at <URL> is <raw_size> chars (limit: <max_doc_chars>). Choose:
    (t) truncate to <max_doc_chars> chars
    (s) skip this doc
    (T) truncate this AND all future oversized docs this run
    (S) skip this AND all future oversized docs this run
    (a) abort enrichment for this run
  ```
  
  Save `T/S` choices to `oversize_choice`. Apply the chosen action.

#### 4b. Resolve image refs

Walk the markdown content with a regex `!\[([^\]]*)\]\(([^)]+)\)` (covering inline markdown image syntax). For each match `(alt, target)`:

- If `target` is an **absolute URL** (`^https?://`) → keep as-is, append `{alt, url: target}` to `referenced_images`.
- If `target` is a **relative path** (`./foo.png`, `../images/foo.png`, `foo.png`):
  1. Compute the doc's directory: from `locator.path`, take the directory portion (e.g., `docs/sds/`).
  2. Resolve `target` against that directory using POSIX path resolution. Clamp any `..` that would escape the repo root to the repo root itself.
  3. Build a raw URL: `https://raw.githubusercontent.com/{locator.owner}/{locator.repo}/{locator.ref}/{resolved-path}`.
  4. Append `{alt, url: <raw URL>}` to `referenced_images`.

Do NOT fetch the image bytes. The URL is for the user to click.

Also scan for HTML image tags `<img src="..." alt="...">` and resolve `src` the same way. Preserve the HTML tags verbatim in the markdown content (don't strip them).

#### 4c. Assemble the extended_context entry

```json
{
  "source": "github",
  "url": "<original URL with fragment, before query-param stripping>",
  "locator": {
    "owner": "...",
    "repo": "...",
    "path": "...",
    "ref": "...",
    "anchor": "<from URL fragment, e.g. 'L42-L60', or null>"
  },
  "content": "<markdown verbatim — possibly truncated>",
  "fetched_at": "<ISO 8601 UTC>",
  "referenced_images": [{"alt": "...", "url": "..."}],
  "truncated": false
}
```

Store in `processed[url]`.

### Step 5: Failure prompt (per-run choice memory)

When a fetch returns an error:

1. If `failure_choice === "skip_all"` → silently mark this URL as skipped, continue with the next URL.
2. If `failure_choice === "abort"` → return an error to the router immediately.
3. Otherwise (first failure or `continue`): prompt the user:

   ```
   Failed to fetch <URL>
     Reason: <error message>
   
   Choose:
     (c) continue without this one
     (s) skip all remaining GitHub fetches this run
     (a) abort enrichment for this run
   ```

   - `c` → mark skipped, continue. Do NOT update `failure_choice` (keep prompting per-failure).
   - `s` → set `failure_choice = "skip_all"`, continue.
   - `a` → set `failure_choice = "abort"`, return error to router.

Rate-limit errors also show the `reset_at` time so the user can decide whether to wait.

### Step 6: Attach to requirements

For each requirement with `_enrichment_urls`:

```
for url in requirement._enrichment_urls:
    result = processed.get(url.url)
    if result and not result.skipped:
        requirement.extended_context = requirement.extended_context or []
        requirement.extended_context.append(result)
delete requirement._enrichment_urls
```

### Step 7: Return

Return the modified requirements array. The `_enrichment_urls` transient field is gone. Any requirement that referenced a URL successfully fetched has a populated `extended_context`; others are unchanged.

## Output Schema

Each requirement that had at least one successful enrichment gains:

```json
"extended_context": [
  {
    "source": "github",
    "url": "https://github.com/org/repo/blob/main/docs/file.md#L42-L60",
    "locator": {
      "owner": "org",
      "repo": "repo",
      "path": "docs/file.md",
      "ref": "main",
      "anchor": "L42-L60"
    },
    "content": "<markdown>",
    "fetched_at": "ISO 8601 UTC",
    "referenced_images": [
      { "alt": "Patient flow", "url": "https://raw.githubusercontent.com/org/repo/main/docs/images/flow.png" }
    ],
    "truncated": false
  }
]
```

Requirements without any successful fetch have no `extended_context` field at all (do NOT set it to `[]`).

## Error Handling

| Situation | Behavior |
|-----------|----------|
| `enrichment.enabled` is false at runtime | Should not be invoked — the router gates this. If invoked anyway, return requirements unchanged. |
| `enrichment.strategy` is unknown | Return error: `"unknown enrichment strategy: <value>"`. |
| All URLs fail and user picks `abort` | Return error to router; the router decides whether to fail the whole command or warn and continue. |
| MCP tools unavailable but strategy is `mcp` | The MCP provider returns `error: "network"`; treat like any other fetch failure (per-run prompt). |
| Provider returns malformed response | Log raw response, return `error: "unknown"`, prompt the user. |

## Guidelines

- **Per-run, in-memory only.** Failure choices and the processed cache live for this skill invocation. Do NOT write to disk, do NOT persist across runs.
- **Dedupe always.** Same URL referenced 10 times in the sheet → 1 fetch, attached to all 10.
- **Preserve provenance.** Every `extended_context` entry includes the original URL (with fragment), the parsed locator, and the fetched_at timestamp. Downstream skills should be able to cite back to the source.
- **Never modify `_enrichment_urls` partially.** Process all URLs first, then attach + remove in one pass.
- **Quiet for skips, loud for failures.** Non-URL cells and unmatched-pattern URLs skip with a one-line log. Real fetch failures prompt.
- **No caching across runs.** Re-fetch every invocation. This is locked policy per the approved plan.
