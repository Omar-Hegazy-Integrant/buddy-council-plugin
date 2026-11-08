# Jama — Setup Guide

How `/bc:setup` connects a project folder to its Jama project. Requirements are the one tool every project needs,
so there is no "none" here. Every Jama call passes `site`: the project's `base_url`.

Jama is read through the plugin's **read-only** `jama` connector: every tool is `jama_get_*`, and its HTTP client
can only issue GET requests. Features are Jama **folders**.

## Links

`bc_setup.py links` recognizes these, and cuts each back to the site's `base_url` (scheme, host, and any context
path before `/perspective.req`):

| Link | Gives |
|---|---|
| `https://company.jamacloud.com/perspective.req#/projects/42` | site, project 42 |
| `https://company.jamacloud.com/perspective.req?docId=7#/items/123?projectId=42` | site, project 42 |
| `https://jama.company.com/contour/perspective.req#/containers/1234?projectId=42` | self-hosted site under `/contour`, project 42 |
| `https://company.jamacloud.com` | site only |

Jama sends credentials over `https://` only.

## Key

An API **client ID and client secret** — Jama → profile menu → **Set API Credentials** → Create. They are OAuth
client credentials: no browser login, revocable on their own, never the account password. An account with
read-only project permissions is the safest home for them. Only an instance without API credentials (some older
self-hosted versions) takes `username` + `password` instead — say plainly that this stores the account password.

Key fields: `client_id`, `client_secret` (or `username`, `password`).

## Connect

Call `jama_get_current_user` with `site`. The verdict goes in the recap: `connected as Jane Doe (API client
key)`. The connector always runs inside the plugin, so when the Jama tools aren't there it didn't start — almost
always because `uv` is missing: point to the README's *Troubleshooting*.

A failure gets a diagnosis, then one retry:

| The error says | Tell the user |
|---|---|
| rejected the API client ID/secret | The key is wrong or revoked — ask for it again. |
| HTTP 403 / Managed Access Control | The key works, but the account may not use the REST API: a Jama admin enables it under Admin → REST API → Managed Access Control. |
| redirected … single sign-on | The address is wrong, or the REST API sits behind SSO. |
| Could not reach | The host is unreachable from here — ask whether it needs the corporate VPN. Behind a TLS-inspecting proxy, `SSL_CERT_FILE` must point at the company CA bundle. |

Still failing: save the link with `"pending": true`. Every analysis command then stops with "Requirements aren't
verified yet", and the next `/bc:setup` connects again.

## Project

Use the link's `project_id` when `jama_get_projects` lists it. A link without one, on an account that sees exactly
one project, uses that project. Otherwise list the projects and ask which — the one question this guide can need.

## Fields

`jama_get_item_types` with `project_id` returns one small answer: the types in the project with their counts, plus
`field_candidates` and `document_key_samples`. On a large project its first call can answer `loading: true`; call
again until it answers. Work these out from it, each shown in the recap:

- **Field mapping**: `status` and `rationale` from `field_candidates`, and `github_url` from its first GitHub-link
  field (keep its `first_url` for `providers/github/setup.md`). A kind with no candidate is left out of the mapping.
  `id`, `title`, and `description` are always the document key, Name, and Description.
- **Item types left out**: `Text`, plus any test-management type such as `Test Case` — test cases come from
  TestRail. Folders, Sets, and Components are never requirements; the server drops them itself.
- **Features**: Jama folders. A project with no folders groups requirements by their Set or Component — a recap
  default, `features: by document — this project has no folders`.

## Scope

Run this after TestRail connects — it anchors the requirements on what the test suite links to. A large Jama
project holds far more than the requirements a team tests; the suite's links point into one **document** (a Jama
Set, such as `Mobile App SRS`), and that whole document becomes the requirements **root**: every requirement in it
is in scope, and one without a linked case shows as untested. The **features** stay the folders the links sit in,
however deep. Everything else in the project is **background**: `/bc:ask` lookups search it, and no report counts it.

1. A run folder carries the linked IDs from TestRail to Jama, so none pass through the conversation:
   `python3 '<plugin root>/scripts/bc_run.py' new --project '<project>' --command setup` → `run_dir`.
2. `testrail_get_requirement_ids` with the TestRail site, project, suite, `requirement_field`, and
   `save_to: '<run_dir>/requirement_ids.json'` → the counts; every linked ID goes into that file.
3. `jama_get_scope_proposal` with `site`, `project_id`, `document_keys_file: '<run_dir>/requirement_ids.json'`, and
   the item types left out → `roots`, `feature_depth`, `proposed_item_types`, `features`, `linked_folders`,
   `candidates`, `outliers`, `anchors`.
4. Save `roots` as `[{"id", "path"}]`, `feature_inference.depth` = `feature_depth`, and `item_type_filter` =
   `proposed_item_types`. On a re-check, saved `exclude` entries still inside the new root are kept.

When the user changes the scope — at the recap or in `/bc:setup <sentence>` — preview it with the same tool
(`root_ids`, `exclude_ids`, `feature_depth`; ids come from `candidates`, `linked_folders`, and `features`), then
recap again:

| They say | Preview with |
|---|---|
| leave out `<folder>` | its id added to `exclude_ids` |
| only `<folder>`, use `<folder>` | `root_ids` = its id, `feature_depth` 1 |
| split the features one level deeper | the same roots, `feature_depth` + 1 |
| the whole project | no roots |
| keep the previous scope | the saved `roots`, `exclude`, and `depth` |

Defaults, each a recap line rather than a question:

- **No test cases** (TestRail is none or not verified): the whole project, every folder a feature —
  `In scope: the whole project — say "only <document>" to narrow it`.
- **No root proposed** (the links spread across the project): `In scope: the whole project — your test cases link
  to requirements all over it`.
- **`anchors.resolved` is 0**: no root, and the recap leads with `None of the 1,480 requirement IDs your test cases
  link to exist in this Jama project — check the project before saving.`

## Re-check

`jama_get_current_user` with `site`, then `jama_get_projects` with `site` still lists the project. A root that no
longer exists shows up as a warning in the first fetch; recheck the scope only when the user asks.

## From another command

Every project uses Jama, so a command never adds it — but one whose Jama project is gone (deleted, or no longer
visible to the key's account) asks for the Jama project link, then *Key* when `bc_setup.py links` says the site has
none, then *Connect*, *Project*, *Fields*, and *Scope*, saves with `--unset requirements`, and carries on. A
requirements root that moved or was deleted needs no question: *Scope* anchors on the test suite again.

## Recap

Plain words — none of Jama's internal ids (item, folder, and root ids mean nothing to a person) and none of "root",
"depth", "anchor", or "item type filter". Names people use are fine: a project's key, a document's name, a
requirement's ID:

```
  Requirements:  Jama project CWA "Clinical Window App" on company.jamacloud.com — connected as Jane Doe
  In scope:      the whole "Mobile App SRS (SRS-1)" document — the one your test cases link to
                 312 requirements in 9 features (Alarms, Patient Monitoring, Settings, …)
  Background:    the rest of the project — 3,190 items — is searched to enrich answers, never counted
  Fields:        status ← Status, rationale ← Rationale, GitHub doc ← Linked to Github
  Left out:      Text and Test Case items
```

Add a line only for what applies:

- Features without a linked case: `not tested yet: Non-Functional (25) — say "leave out Non-Functional" if it is
  an archive`. Only the user knows whether an untested folder is a gap or an archive, so list them, keep them.
- Same-type requirements outside the document: `12 more of the same kind sit in Archive / Old SRS — background`.
- Linked IDs outside the document (`outliers`): `41 of the IDs your tests link to sit outside this document
  (e.g. CWA-REQ-412 in Product / Legacy) — not counted`.
- Linked IDs Jama doesn't have (`anchors.not_found`): `2 IDs your test cases link to are not in Jama (TBD, N/A)`.
- A re-check that changed the scope: `was: Mobile App SRS / REQUIREMENTS — say "keep the previous scope"`.

## Settings

```json settings
{
  "requirements": {
    "provider": "jama",
    "base_url": "https://company.jamacloud.com",
    "project_id": 42,
    "project_key": "CWA",
    "project_name": "Clinical Window App",
    "roots": [{ "id": 1001, "path": "Product / Mobile App SRS" }],
    "feature_inference": { "strategy": "hierarchical_folder", "folder_item_type": "Folder", "depth": 3 },
    "item_type_filter": ["Mobile SRS"],
    "item_type_exclude": ["Text", "Test Case"],
    "field_mapping": { "status": "Status", "rationale": "Rationale", "github_url": "Linked to Github" },
    "pending": false
  }
}
```

Without a root, leave out `roots`, `exclude`, `depth`, and `item_type_filter`: every folder is then a feature.
`enrichment` comes from `providers/github/setup.md`; with no GitHub field mapped it is
`{"enabled": false, "max_doc_chars": 50000}`.
