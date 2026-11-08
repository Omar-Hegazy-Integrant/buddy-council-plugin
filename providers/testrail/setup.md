# TestRail — Setup Guide

How `/bc:setup` connects a project folder to its TestRail suite, and works out how new cases get written. Every
TestRail call passes `site`: the project's `test_cases.base_url`.

## Links

`bc_setup.py links` recognizes these, and cuts each back to the site's `base_url` (everything before
`/index.php`):

| Link | Gives |
|---|---|
| `https://company.testrail.io/index.php?/suites/view/9277&group_by=cases:section_id` | site, suite 9277 |
| `https://company.testrail.io/index.php?/projects/overview/45` | site, project 45 |
| `https://company.testrail.io/index.php?/cases/view/1234` | site, case 1234 |
| `https://company.testrail.io` | site only |

"None" is an answer too: the project uses no test cases (ADR 0009).

## Key

The email the user signs in to TestRail with, and an **API key** — TestRail → My Settings → API Keys → Add Key.

Key fields: `username`, `api_key`.

## Connect

The first call of *Suite* below is the connection check — `testrail_get_suites` for a suite, case, or project link,
`testrail_get_projects` only for a link to the site alone — and its verdict goes in the recap: `connected`. A failure
gets a diagnosis, then one retry:

| Result | Tell the user |
|---|---|
| 401 | The email or API key is wrong — ask for the key again. |
| 403 | The key works, but the account can't use the API: an admin enables it under Administration → Site Settings → API. |
| redirected … not followed | The address is wrong, or it sends sign-ins elsewhere — confirm the link. |
| Could not reach / timeout | The host is unreachable from here — ask whether it needs the corporate VPN. |

Still failing: save the link with `"pending": true`. Analysis then stops with "Test cases aren't verified yet",
and the next `/bc:setup` connects again.

## Suite

Work the project and suite out from the link:

- **A suite**: `testrail_get_suites` with `suite_id` — its `project_id` is the project.
- **A case**: `testrail_get_case` gives its `suite_id`; then as for a suite.
- **A project**: `testrail_get_suites` with `project_id`. One suite, or a single-suite project: use it. Several:
  ask which — the one question this guide can need.
- **A site only**: list the projects and ask which, then as for a project.

## Authoring

What every path that creates cases needs — `/bc:vnv-sprint-prep`, the `/bc:coverage` gap-fill, `/bc:ask` scenario
mapping: TestRail takes **ids, not names**, and they differ per site, so resolve them once, here, with one call:
`testrail_get_authoring_defaults` with `site`, the project, and the suite. It answers:

- `template` — the one whose name contains "Steps", because scenarios are written as Given/When/Then and only a
  steps template has `custom_steps_separated`; `steps: false` means none exists, and the recap says steps go in as
  plain text.
- `case_type` — "Functional", else the site's default; `priority` — the middle one.
- `requirement_field` — the field whose mistake is silent: TestRail accepts an unknown `custom_*` key and drops its
  value without an error, so a wrong name makes every created case an orphan. It is the candidate holding
  requirement-shaped IDs on the most sampled cases, or `null` when none does — save `null` then, never a guess.
- `placement` — where new cases go, a default and never a question: every path that creates cases shows each case's
  folder and waits for a yes, so placement is decided again where it can be judged.

| When | Placement | Settings | Recap words |
|---|---|---|---|
| the field is filled on most sampled cases | beside existing cases | `"linked"`, root `null` | `new cases are saved next to existing cases for the same requirement or feature` |
| otherwise | their own folder | `"feature"`, root `"V&V"` | `new cases are saved in a V&V folder, one subfolder per feature` |
| the user names a folder per feature among the existing ones | | `"feature"`, root `null` | `new cases are saved in a folder named after their feature` |
| the user names one folder | | `"fixed"`, root = it | `new cases are all saved in <root>` |

A re-check keeps a saved `section_strategy` and `section_root`: the user may have chosen them.

## Re-check

`testrail_get_projects` with `site`, then `testrail_get_suites` with `site` and the saved `suite_id` — a suite deleted on
TestRail's side asks for a new suite link. With the authoring ids saved, nothing more.

## From another command

A command that needs test cases in a project that uses none — `/bc:coverage`, say — or whose suite was deleted on
TestRail's side asks for the TestRail suite link, then *Key* when `bc_setup.py links` says the site has none, then
*Connect*, *Suite*, and *Authoring*, saves (with `--unset test_cases` when it replaces a suite), and carries on.
Jama's scope stays as it is until the next `/bc:setup`.

## Recap

```
  Test cases:    TestRail suite "Master" (12) in project 7 on company.testrail.io — connected
  New cases:     template "Test Case (Steps)", type "Functional", priority "Medium"
                 requirement link → custom_jama_req_id (+ built-in References)
                 new cases are saved next to existing cases for the same requirement or feature
```

`requirement link → none found` when the field is `null`, with `cases the plugin creates won't count as coverage
until one is set`. Test cases set to none: `Test cases: none — /bc:coverage and /bc:contradiction ask for a suite
link when you run them, and /bc:vnv-sprint-prep writes no cases`.

## Settings

```json settings
{
  "test_cases": {
    "provider": "testrail",
    "base_url": "https://company.testrail.io",
    "project_id": 7,
    "suite_id": 12,
    "authoring": {
      "template_id": 2,
      "type_id": 7,
      "priority_id": 4,
      "requirement_field": "custom_jama_req_id",
      "section_strategy": "linked",
      "section_root": null,
      "create_missing_sections": true
    },
    "pending": false
  }
}
```

Leave `authoring` out when the tools weren't available: the next `/bc:setup` fills it in, and the paths that create
cases skip themselves with a visible line until then. Not used:

```json settings
{ "test_cases": { "provider": "none" } }
```
