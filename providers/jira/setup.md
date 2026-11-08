# Jira — Setup Guide

How `/bc:setup` connects a project folder to its Jira dev board, and how `/bc:vnv-sprint-prep` adds its V&V board.
Every Jira call passes `site`: the project's `jira.base_url`. The plugin's `atlassian` connector hands each call to
a copy of `mcp-atlassian` bound to that site, which speaks to both **Atlassian Cloud and Jira Server/Data Center**.

## Links

`bc_setup.py links` recognizes these, and gives the site's `base_url`, `deployment`, `board_id`, and
`project_key`:

| Layout | Example |
|---|---|
| Team- or company-managed | `.../jira/software/projects/PROJ/boards/42`, `.../jira/software/c/projects/PROJ/boards/42` |
| With a tab or issue | `.../boards/42/backlog`, `.../boards/42/timeline?selectedIssue=PROJ-7` |
| Classic RapidBoard | `.../secure/RapidBoard.jspa?rapidView=42&projectKey=PROJ`, or without `projectKey` |
| Under a context path | `https://tools.company.com/jira/secure/RapidBoard.jspa?rapidView=42` |
| An issue | `.../browse/PROJ-123` — the project, no board |

**Cloud or Server/Data Center comes from the host**: `*.atlassian.net` and `*.jira.com` are Cloud, anything else is
Server/Data Center. The recap names it, so a vanity domain can be corrected (`it's Cloud`).

"None" is an answer too: the project uses no Jira (ADR 0009).

## Key

| Deployment | Key | Fields |
|---|---|---|
| Cloud | the Atlassian account email and an API token — <https://id.atlassian.com/manage-profile/security/api-tokens> | `username`, `api_token` |
| Server/Data Center | a personal access token — profile menu → **Personal Access Tokens** → Create token | `personal_token` |

Server/Data Center takes no username: the token names its owner, and a username makes mcp-atlassian try basic
auth and fail with a confusing 401.

## Connect

Call `jira_get_user_profile` with `site` and `user_identifier` — on Cloud the account email the key signs in as,
which this computer already knows (`bc_setup.py links` → `key.user`, or `status` → `tools.board.account`), and on
Server/Data Center `currentUser()`. Never ask for it. A site's first call downloads mcp-atlassian, which can take
half a minute; when the answer says it is still starting, call again. Verdict for the recap: `connected as Jane Doe (Jira Cloud)`.

| Result | Tell the user |
|---|---|
| 401 | The token is wrong or expired, or (Cloud) paired with the wrong email — ask for the key again. |
| 403 | The key works but the account can't use the API — a Jira permission or an IP allowlist. |
| 404 | The address is wrong or points at a proxy. |
| TLS error | A self-signed certificate: the user can add `"ssl_verify": false` to the site's key file (`bc_keys.py path` names it), which turns certificate checks off for that site. Never add it without asking. |
| Connection refused / timeout | The host is unreachable from here — ask whether it needs the corporate VPN; a self-hosted Jira usually does. |

After one retry, save the board with `"pending": true`: analysis skips it with a visible line, `/bc:validate` and
`/bc:vnv-sprint-prep` stop, and the next `/bc:setup` connects again. The key stays saved.

## Board

- **The link names a board**: `jira_get_agile_boards` with `project_key` lists it — its name and type go in the
  recap (`board 1656 "CWA Scrum Board" (scrum)`) — and `jira_get_board_issues` with `board_id`,
  `jql: "statusCategory != Done ORDER BY updated DESC"`, and a small `limit` counts the open issues. A classic link
  with no project key gets it from the board's newest issue: `jira_get_board_issues` with
  `jql: "ORDER BY created DESC"`, `limit: 1`, and the key's prefix (`CWA-1234` → `CWA`).
- **The link names a project or an issue**: `jira_get_agile_boards` with its `project_key`. One board: use it.
  Several: ask which — the one question this guide can need.
- **A board id the site doesn't list** is asked for again; a board id is never saved unverified by a list.
- **Issue type** for new tickets: `jira_get_project_issue_types` → `Story`, else `Task`.

The board is read by id (`howtos/fetch-board-issues.md`), so analysis sees the board's own issues, including
boards whose filter spans projects.

## Re-check

`jira_get_user_profile` with `site` and `user_identifier` as in *Connect* (`tools.board.account` on Cloud), then
`jira_get_agile_boards` with `site` still lists the board. A board deleted on Jira's side asks for a new board link.

## From another command

A command that needs the board in a project that uses no Jira — `/bc:validate` creating a ticket — or whose board
was deleted on Jira's side asks for the board link, then *Key* when `bc_setup.py links` says the site has none, then
*Connect* and *Board*, saves, and carries on. A missing or deleted V&V board is *The V&V board*, below.

## The V&V board

Used by `/bc:vnv-sprint-prep`, which asks for this link the first time it runs, or when the user passes another.
Setup never asks for it.

1. **The link**: `bc_setup.py links`, then *Board* as above. Its board id must differ from the dev board's: the
   same id would clone stories onto the board they came from. Refuse it and ask again. A shared *project* is fine —
   one project with a dev board and a V&V board is a common layout, and everything is addressed by board id.
2. **What puts an issue on it** — the discriminator, needed only when both boards are in one project (else
   `null`). Sample each board with `jira_get_board_issues`, `jql: "ORDER BY updated DESC"`, `limit: 50`,
   `fields: "summary,labels,components,issuetype,status"`, and look — label first, then component, then issue
   type — for a value on at least 80% of V&V issues and at most 5% of dev issues: `{"kind": "label", "value":
   "vnv", "confidence": "sampled", "sampled_at": "<now>"}`. When the samples settle nothing, ask nothing: save
   `null`, and the plan shows it as the default with its consequence — clones land in the shared project and may
   surface on the dev board — and how to change it: `say "stamp label vnv"` (or a component, issue type, or
   sprint), which is saved as `"stated"`.
3. **The platform field**: `jira_search_fields` with `keyword` = `OS` → its custom field id. No such field:
   save `field_name` alone; parity then falls back to title prefixes, marked lower-confidence.
4. **The reviewer** who approves scenarios is whoever runs the command — `jira_get_user_profile`, as in
   *Connect* — unless the user names someone — `reviewer is Jane` → `jira_search_assignable_users` → `{"account_id", "display_name"}`,
   saved. On Server/Data Center the identifier is a username; save what the server returns as `account_id`.
5. **Labels and approval phrases**: the defaults below, renamed only by hand.

The discriminator and the reviewer show in `/bc:vnv-sprint-prep`'s plan, where the user can change either. Save
`vnv_workflow.reviewer` only when the user names someone; without it, the reviewer is whoever runs the command.

## Recap

```
  Board:         PROJ board 42 "PROJ Scrum" (scrum) on jira.company.com (Jira Server/Data Center) — 37 open issues
                 connected as Jane Doe; new tickets are Stories
```

Jira set to none: `Board: none — analysis reads no board issues, /bc:validate asks for a board link before it
creates a ticket, and /bc:vnv-sprint-prep can't run`.

## Settings

```json settings
{
  "jira": {
    "provider": "jira",
    "base_url": "https://jira.company.com",
    "deployment": "server",
    "project_key": "PROJ",
    "default_issue_type": "Story",
    "board": { "url": "https://jira.company.com/secure/RapidBoard.jspa?rapidView=42", "id": 42 },
    "pending": false
  }
}
```

Not used:

```json settings
{ "jira": { "provider": "none" } }
```

The V&V board, saved by `/bc:vnv-sprint-prep`:

```json settings
{
  "jira": {
    "vnv_board": {
      "url": "https://jira.company.com/secure/RapidBoard.jspa?rapidView=77",
      "id": 77,
      "project_key": "PROJ",
      "discriminator": { "kind": "label", "value": "vnv", "confidence": "sampled", "sampled_at": "2026-09-01T12:00:00Z" }
    },
    "platform": {
      "field_name": "OS",
      "field_id": "customfield_10050",
      "values": { "ios": ["iOS"], "android": ["Android"] },
      "require_parity": ["ios", "android"],
      "title_prefix_fallback": true
    },
    "vnv_workflow": {
      "approval_phrases": ["approved", "lgtm", "looks good"],
      "labels": {
        "pending_validation": "pending-validation",
        "pending_questions": "pending-questions",
        "pending_scenario_validation": "pending-scenario-validation",
        "ready_for_test_cases": "ready-for-test-case-creation"
      }
    }
  }
}
```
