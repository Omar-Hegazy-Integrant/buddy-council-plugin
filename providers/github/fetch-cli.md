# GitHub Fetch (CLI Strategy) — Provider Guide

Fetch one GitHub-hosted document with the plugin's own reader, which signs in with whatever already works.

## When to Use

Invoked by `howtos/enrich-requirements.md` for each unique URL when `requirements.enrichment.strategy === "cli"`.
Run folders don't need it: `bc_run.py enrich` reads every linked document the same way.

## Fetch

```bash
python3 '<plugin root>/scripts/bc_run.py' doc --url '<url>'
```

It tries each sign-in until one reads the document — `gh`; then `GH_TOKEN` or `GITHUB_TOKEN` for github.com
(for a GitHub Enterprise host, only the one named in `GH_HOST`: `GH_ENTERPRISE_TOKEN` or `GITHUB_ENTERPRISE_TOKEN`);
then the host's saved key; then no sign-in, which reads public repositories. A token goes only to its own host, and
a redirect is never followed. The bash hook approves the command, so it asks nothing.

On success it prints one HTML comment holding `url`, `read_with`, `locator` (`owner`, `repo`, `path`, `ref`,
`anchor`, and `host` for GitHub Enterprise), `raw_size`, `truncated`, `referenced_images`, and any `note`, then the
document verbatim — cut at `--max-chars` (50,000 by default) with a pointer to the rest. A folder (`tree`) link
reads its `README.md`, else up to five Markdown files joined by `---`. On failure it prints
`{"ok": false, "error": "<kind>: <detail>"}`.

## URL shapes

| Shape | Example |
|-------|---------|
| `blob` file | `https://github.com/owner/repo/blob/main/path/to/file.md` |
| `blob` with SHA or anchor | `https://github.com/owner/repo/blob/abc123def/path.md#L42-L60` — the ref is kept verbatim, the anchor recorded, the content not sliced |
| `tree` folder | `https://github.com/owner/repo/tree/main/docs/architecture` |
| `raw` file | `https://raw.githubusercontent.com/owner/repo/main/path.md` |
| GitHub Enterprise | `https://github.company.com/team/repo/blob/main/docs/sds.md` |

Anything else — a gist, another forge, a link without `/blob/` or `/tree/` — is `unsupported_url`.

## Output

Return to the orchestrator, per URL:

```json
{
  "source": "github",
  "url": "<original URL>",
  "locator": { "owner": "owner", "repo": "repo", "path": "docs/file.md", "ref": "main", "anchor": null },
  "content": "<the document, verbatim>",
  "fetched_at": "<ISO 8601 UTC>",
  "raw_size": 4231,
  "referenced_images": [{ "alt": "...", "url": "https://raw.githubusercontent.com/..." }]
}
```

For errors, `{"source": "github", "url": "<original URL>", "error": "<kind>", "message": "<detail>"}`, where the kind
is one of `auth_failed`, `repo_inaccessible`, `file_not_found`, `rate_limit`, `directory_no_markdown`, `network`,
`unsupported_url`, or `unknown`. `repo_inaccessible` means no sign-in that worked could see the repository: it is
private to every one of them, or gone.

## Guidelines

- **Preserve markdown verbatim.** The enrichment how-to resolves image refs from the raw content.
- **Never handle a token yourself.** The reader finds them; nothing here reads or writes a secret.
