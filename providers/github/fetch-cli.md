# GitHub Fetch (CLI Strategy) — Provider Skill

Fetch a single GitHub-hosted file via the `gh` command-line tool. This is the default strategy when both `gh` and `github-mcp-server` are available.

## When to Use

Invoked by `skills/enrich-requirements/SKILL.md` for each unique URL when `requirements.enrichment.strategy === "cli"`.

## Prerequisites

- `gh` CLI installed and on `$PATH` (`gh --version`)
- User authenticated (`gh auth status` returns OK)
- The `Bash(gh api:*)` permission is already in the project allowlist

If `gh auth status` fails, return a structured auth error (do NOT prompt for credentials here — the orchestrator handles the user prompt).

## Input

A single URL string matching `^https://github\.com/...`. The orchestrator has already stripped query params (`?plain=1` etc.) before calling this skill.

## URL Parsing

Accept these URL shapes:

| Shape | Example | Extracted |
|-------|---------|-----------|
| `blob` file | `https://github.com/owner/repo/blob/main/path/to/file.md` | `{owner, repo, path: "path/to/file.md", ref: "main"}` |
| `blob` with SHA | `https://github.com/owner/repo/blob/abc123def/path.md` | `{owner, repo, path, ref: "abc123def"}` (preserve verbatim) |
| `blob` with anchor | `https://github.com/owner/repo/blob/main/path.md#L42-L60` | `{owner, repo, path, ref, anchor: "L42-L60"}` (do NOT slice content) |
| `tree` directory | `https://github.com/owner/repo/tree/main/docs/architecture` | `{owner, repo, path: "docs/architecture", ref, kind: "tree"}` |
| `raw` file | `https://raw.githubusercontent.com/owner/repo/main/path.md` | Normalize to `blob` form |

Reject (return structured error):
- Hostnames other than `github.com` or `raw.githubusercontent.com`
- URLs without `/blob/` or `/tree/` segment
- Gist URLs (`gist.github.com`)

## Fetch Procedure

### Step 1: Pre-probe repo access

```bash
gh api "repos/{owner}/{repo}" 2>&1
```

Outcomes:
- **200** with repo metadata → repo is accessible, proceed to Step 2
- **404** → return `error: "repo_inaccessible"` (could be private without auth, or doesn't exist)
- **401/403** → return `error: "auth_failed"`
- Network failure → return `error: "network"` with the message

This pre-probe distinguishes "the file moved" from "I can't see this repo at all."

### Step 2: Fetch content (blob)

```bash
gh api "repos/{owner}/{repo}/contents/{path}?ref={ref}" 2>&1
```

The response is JSON with a base64-encoded `content` field for files under ~1 MB:

```json
{
  "name": "file.md",
  "path": "docs/file.md",
  "sha": "...",
  "size": 4231,
  "content": "IyBQYXRpZW50IE1vbml0b3Jpbmcg...",
  "encoding": "base64"
}
```

Decode `content`:

```bash
echo "$content_b64" | base64 --decode
```

For files over ~1 MB, the API returns `"content": ""` and you must fetch via:

```bash
gh api "repos/{owner}/{repo}/git/blobs/{sha}" --jq '.content' | base64 --decode
```

Outcomes:
- **200** → return decoded content
- **404** → return `error: "file_not_found"` (the repo exists but this file doesn't, e.g. renamed/deleted)
- **403** with `X-RateLimit-Remaining: 0` → return `error: "rate_limit"` with `reset_at` from `X-RateLimit-Reset`
- Any other error → return `error: "unknown"` with the gh stderr

### Step 3: Tree (directory) handling

If the URL is a `tree` URL, list the directory contents:

```bash
gh api "repos/{owner}/{repo}/contents/{path}?ref={ref}"
```

For a directory, the response is a JSON array of entries. Apply this rule:

1. If any entry has `"name": "README.md"` (case-insensitive), recurse into it as if it were the requested file.
2. Otherwise, collect up to **5** entries where `"type": "file"` and the name ends in `.md`. Fetch each and concatenate with `\n\n---\n\n` separators. Set `truncated_files: <count_omitted>` if there were more than 5.
3. Otherwise, return `error: "directory_no_markdown"`.

## Output

```json
{
  "source": "github",
  "url": "<original URL>",
  "locator": {
    "owner": "owner",
    "repo": "repo",
    "path": "docs/file.md",
    "ref": "main",
    "anchor": null
  },
  "content": "<decoded markdown verbatim>",
  "fetched_at": "<ISO 8601 UTC>",
  "raw_size": 4231,
  "http_status": 200
}
```

For errors:

```json
{
  "source": "github",
  "url": "<original URL>",
  "error": "auth_failed | repo_inaccessible | file_not_found | rate_limit | directory_no_markdown | network | unknown",
  "message": "<human-readable detail>",
  "reset_at": "<ISO 8601, only on rate_limit>"
}
```

## Guidelines

- **Never store credentials.** `gh` handles auth via its own keychain; this skill must not read or write any secret files.
- **Preserve markdown verbatim.** Do NOT strip HTML, do NOT collapse whitespace, do NOT modify links. The enrich skill needs the raw content to resolve image refs.
- **Preserve `ref` literally.** A SHA in the URL is intentional — don't substitute `main`.
- **Don't follow redirects to other hosts.** `gh api` won't, but if a future version of `gh` adds that behavior, refuse to leave `github.com`.
- **Do not write to disk.** All content stays in memory and is returned to the orchestrator.
