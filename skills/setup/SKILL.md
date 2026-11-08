---
name: setup
description: Connect this project folder to its Jama project, TestRail suite, and Jira board from their links, asking only for what can't be worked out, and install the Plugin Tools when it finds a test project.
disable-model-invocation: true
---

# /bc:setup — Connect a project folder to its tools

**Plugin root:** two folders above this skill's base directory, which is `<plugin root>/skills/setup/`. Every file these instructions name — `howtos/…`, `providers/…`, `scripts/…` — is a path from the plugin root, and so is every file those files name.

Setup works from **links**. A link to the Jama project, the TestRail suite, or the Jira board names the tool and its
site; everything else is worked out from the tools themselves and shown in one **recap**, followed by one question:
"Save?". Ask anything else only for a missing link or key, or when something fails or can't be found.

**Each script call is its own command**: `python3 '<plugin root>/scripts/<script>' <subcommand> …`, the plugin root
written out in full, every argument bare or in single quotes, JSON in one single-quoted argument on one line with
every `'` written as `\u0027`. That is the shape the bash hook approves, so these calls ask nothing — a `cd`, a
variable, `&&`, `;`, a pipe, or a redirect turns one into a permission prompt. Read the guides, the settings, and a
tool result saved to a file with your view or grep tool.

**Every connector call passes `site`**: the tool's `base_url`, from its link or from the settings. Without it, a
call reaches whichever key this computer holds for that tool.

## 1. Where things stand

1. `bc_setup.py status --from '<the folder this session started in>'` gives `project` — the project folder: the
   nearest folder at or above that one holding `.buddy-council/`, else that one — plus `exists`, `update` (set up
   by an older version), `tools` (each tool's `state` and site), `keys` (how many sites this computer has keys
   for), and `legacy`. A `stop` about the home folder or the plugin's folder ends the run.
2. When `legacy` is true, `bc_setup.py migrate`. It moves the keys older setups saved into one file per site, in
   code, so no key passes through you, and lists in `verify` the sites to check before `cleanup`. Its `lines` go in
   the recap, and its `keys` replaces the count from `status`: the moved keys count.
3. Pick the run from the text the user typed after the command:

| Typed | Run |
|---|---|
| nothing, no settings | **First setup** — every tool |
| nothing, settings saved | **Re-check** — below |
| one or more links | **Change** — the tools the links name |
| a sentence: `no Jira`, `leave out Release 3 Drafts`, `put new cases in V&V`, `the whole project` | **Change** — that part only |
| `forget <site>` | `bc_setup.py forget --url '<site>'`, show its `lines`, done |

Requirements still on Excel (`tools.requirements.state` is `excel`): say in one line that requirements come from
Jama only now, and ask for the Jama project link.

## 2. Links and keys

Ask for every link the run needs in **one** message: the Jama project (required), the TestRail suite, and the Jira
dev board, where "none" is an answer for the last two (ADR 0009). When `keys` is 0 — a computer with no keys yet —
ask for the keys in the same message: TestRail email and API key; Jama API client ID and secret; for Jira, the email
and an API token on Cloud, or a personal access token on Server/Data Center.

`bc_setup.py links --link '<link>' --link '<link>' …` reads them all: each gives its `provider`, `base_url`, `site`,
the ids in it, and `key` — whether one is `saved`, and how to `ask` for one. Ask for every key not saved in one
message, worded with its `key.ask` and site. A link with a `problem` is asked for again.

**Saving a key** — one file per site in `~/.buddy-council/keys/` (ADR 0008):

1. `bc_keys.py path --url '<base_url>' --provider <provider>` prints the file's `path`.
2. Write that file with your file tool: `{"provider": "<provider>", "url": "<base_url>", …the key's fields}`. The
   key stays out of every command line, and the assistant's own prompt before the write is the approval — ask
   nothing more about it.
3. `bc_keys.py check --url '<base_url>' --provider <provider>` confirms it is complete and makes it private.

A key appears nowhere after that: not in the recap, a command, or the settings.

## 3. Connect and work each tool out

Follow each guide for the tools this run touches, in this order — Jama's scope reads the test suite, so it waits
for TestRail:

1. `providers/jama/setup.md` — *Connect*, *Project*, *Fields*
2. `providers/testrail/setup.md` — *Connect*, *Suite*, *Authoring*
3. `providers/jama/setup.md` — *Scope*
4. `providers/jira/setup.md` — *Connect*, *Board*
5. `providers/github/setup.md` — when Jama's fields map a GitHub link
6. *Code mapping*, below
7. `howtos/automation-tools.md` → *In setup* — its `detect` decides whether it applies

A failed connection gets the guide's diagnosis and one retry; still failing, the tool is saved as not verified
(`"pending": true`), and the next `/bc:setup` connects again. Each guide ends with its recap lines and the settings
it saves.

## 4. Recap, then "Save?"

The recap is the text of the question that asks "Save?" — the message itself when an ask tool asks it — so the user
decides on what they can see. Each guide owns its recap lines (its *Recap* section); this is only their order:

```
Ready to save:
  Requirements, In scope, Background, Fields, Left out   ← providers/jama/setup.md → Recap
  Test cases, New cases                                  ← providers/testrail/setup.md → Recap
  Board                                                  ← providers/jira/setup.md → Recap
  Design docs                                            ← providers/github/setup.md → Recap
  Code mapping                                           ← Code mapping, below
  Automation                                             ← howtos/automation-tools.md → In setup
  migrate's lines

Save?
```

A change run recaps only what changes, as `before → after`, and a tool set to none says what that turns off:

```
  Board:  PROJ board 42 "PROJ Kanban Board" on acme.atlassian.net → none
          analysis reads no board issues; /bc:validate asks for a board link before it creates a ticket;
          /bc:vnv-sprint-prep can't run

Save?
```

"Save?" is the run's one question about choices. A correction instead ("put new cases in V&V") updates the recap
and asks again.

On yes, save everything in one call: `bc_setup.py save --project '<project>' --patch '<the guides' settings,
merged into one object>'`, adding `--unset <block>` for a tool whose link now names another site or project
(`requirements`, `test_cases`, or `jira`), so its old settings go with it. The script checks the settings against
`config/sources.example.json`, keeps `.buddy-council/` out of git, and names any problem; fix it and save again.

Then, in order:

1. The Plugin Tools, when the recap listed them: `howtos/automation-tools.md` → *After the save*.
2. When `migrate` listed sites to `verify`: `bc_setup.py cleanup --verified '<the ones that answered, comma-separated>'`
   — it removes what older setups left for those sites, and names once what it leaves alone. Show its `lines`.
3. One closing line naming what is ready — `/bc:coverage`, `/bc:contradiction`, `/bc:validate`, `/bc:ask`. Nothing
   needs a restart: the connectors were running before setup began, and read each key on the call that needs it.
   The one exception is installing or updating the Plugin Tools, whose lines say so (ADR 0002).
   When reads still prompt in Copilot CLI, point to the README's *Copilot CLI — fewer permission prompts*.

## Re-check

A bare re-run checks every site and setting the project uses: each entry of `status`'s `checks`, through the guide
section it names — Jama, TestRail, and Jira unless a tool is none, the GitHub link when enrichment is on, and the
Plugin Tools when the settings have them. The re-check is done when every entry has a verdict. It redoes only
what broke. An expired key asks for that site's key; a deleted board
or suite asks for a new link; an unverified tool connects again, and one that answers now is saved verified.

- **Nothing changed**: ask nothing — say so, one line per tool, and stop. What a check only records — a tool that
  answers now, the GitHub link it read — is saved without asking.
- **Set up by an older version** (`update`): once the checks pass, save with `--patch '{}'` — it drops
  `plugin_root` and brings the settings up to this version — then `cleanup`. When everything still connects, this
  asks nothing.
- **Something changed**: recap `before → after`, then "Save?".

## Code mapping

`/bc:onboarding` and `/bc:codemap` map features to the code that implements them. Code mapping is on when
`status`'s `code` lists a marker of code in the project folder (`package.json`, `pyproject.toml`, `.git`, …), and off
when it lists none. Recap: `Code mapping:  on — Node project; requirement IDs CWA-REQ-\d+, TC-\d+`, or `off — no code
here`.
Requirement ID patterns come from Jama's `document_key_samples` for the item types in scope: the literal prefix
with the number as `\d+` (`CWA-REQ-85` → `CWA-REQ-\d+`), one pattern per prefix, plus `TC-\d+`. A re-check keeps
saved patterns and ignored folders.

```json settings
{ "project": { "enabled": true, "ignore_dirs": ["node_modules", "dist", ".next", "build", "vendor", "__pycache__", ".venv", "target"], "id_patterns": ["CWA-REQ-\\d+", "TC-\\d+"] } }
```
