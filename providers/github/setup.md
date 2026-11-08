# GitHub — Setup Guide

How `/bc:setup` makes sure the design documents requirements link to can be read. It runs only when Jama's fields
map a GitHub link (`providers/jama/setup.md` → *Fields* keeps the first link found, `first_url`).

## Reading with the sign-in that already works

The plugin reads GitHub documents itself, trying each way to sign in until one reads the document:

1. **`gh`**, when it is installed and signed in to the document's host.
2. **A token in the environment**: `GH_TOKEN` or `GITHUB_TOKEN` for github.com; for a GitHub Enterprise host, only
   the one named in `GH_HOST` — `GH_ENTERPRISE_TOKEN` or `GITHUB_ENTERPRISE_TOKEN` (`GH_TOKEN` on a `*.ghe.com`
   host). A token never goes to a host the user didn't name, however much it looks like GitHub.
3. **The host's saved key**, when an earlier setup saved one.
4. **No sign-in**, which reads public repositories.

Run it on the first link:

```bash
python3 '<plugin root>/scripts/bc_setup.py' github --url '<first_url>'
```

`"ok": true` names the sign-in that worked (`read_with`) and shows a `preview` → `strategy: "cli"`, and the link is
saved as `check_url`, the one a re-check reads. GitHub Enterprise works the same way: the host comes from the link,
and a host whose name doesn't start with `github.` or `ghe.` counts as GitHub once it is `GH_HOST` or has a saved key.

No requirement holds a link yet (`first_url` is null): keep enrichment on with `strategy: "cli"` and no
`check_url` — the recap says `GitHub links in "<field>": none yet, so reading them isn't checked` — and the next
re-check tries again.

## When none of those can read it

1. **The assistant's own GitHub connection.** When this assistant has a `get_file_contents` tool — Copilot CLI's
   built-in GitHub server, or a GitHub server added to Claude Code — read the same document with it. It reads →
   `strategy: "mcp"`: commands then read documents through that connection.
2. **The token an older setup saved**, when the check answered `old_token: true`: run it again with
   `--move-old-token yes`. That makes the token the github.com key — in code, so it never passes through you —
   and reads again; its `lines` go in the recap. Only now: while anything else reads the documents, the old token
   stays where it is, and cleanup says so once.
3. **A read-only token**, asked for only now: a fine-grained personal access token with **Contents: read** on the
   repositories the documents live in (GitHub → Settings → Developer settings). Save it as the host's key — fields
   `token`, provider `github`, `url` the host (`https://github.com`, or the Enterprise host) — then run the check
   again.
4. The user would rather not: enrichment is off, and the recap says links are found but not read.

## Re-check

When enrichment is on with `strategy: "cli"`, run the check on `check_url` — or, in settings saved before it
existed, on the `first_url` from `jama_get_item_types`, then save it as `check_url`. A failure walks the steps above.
With `strategy: "mcp"`, read `check_url` through the assistant's GitHub connection.

## Recap

```
  Design docs:   GitHub links in "Linked to Github" are read with gh — "# Patient Monitoring Architecture…"
```

Or `… read through your assistant's GitHub connection`, or `off — the links are found but not read`.

## Settings

```json settings
{ "requirements": { "enrichment": { "enabled": true, "strategy": "cli", "max_doc_chars": 50000, "check_url": "https://github.com/org/repo/blob/main/docs/sds.md" } } }
```

`strategy` is `"cli"` (the plugin reads them, with the first sign-in that works) or `"mcp"` (the assistant's own
GitHub connection). Off: `{"enabled": false, "max_doc_chars": 50000}`.
