# Load Settings

How every command starts: find the project folder, learn which tools it can read, and fix on the spot what it can't
run without. Every command needs requirements; beyond them, each names the tools it **needs** and the ones it
**reads** when the project has them.

## Rules for the whole command

The shell remembers a `cd` from one call to the next, and the bash hook approves only the plugin's own scripts run
plainly; anything else stops for the user's permission. So, from here to the end of the command:

- **Run a plugin script as its own command**, in the shape these instructions show:
  `python3 '<plugin root>/scripts/<script>' <subcommand> …`, every argument bare or in single quotes. Never `cd`
  first, and never add a variable, `&&`, `;`, a pipe, a heredoc, or a redirect.
- **Read files with your view or grep tool**, never through the shell: the how-tos, the settings, a run's results,
  and a bundle — one part per read, since each part is sized to fit. To check or count something in a run, use the
  script that prints it (`bc_run.py summary`, `show`, `search`); don't parse its JSON with `python3 -c` or `jq`.
- **Keep a tool's answer small enough to show at once.** An answer too large lands in a file outside the project;
  call again for less (fewer fields, a smaller page) rather than reading that file.

## Run

```bash
python3 '<plugin root>/scripts/bc_setup.py' status --from '<the folder this session started in>' --need '<tools>' --reads '<tools>'
```

Run it as its own command, exactly in this shape — the one the bash hook approves, so it asks nothing. `--from` is
your working directory when the session started, so an earlier `cd` can't point it at the wrong folder. Each list is
the command's own — `test_cases`, `board`, `vnv_board`, comma-separated — and an empty one is left out with its
option. A tool in neither list is left alone: no key asked for, no line printed. It prints one JSON object:

- **`project`** — the project folder: the nearest folder at or above the current one that holds `.buddy-council/`.
  Every `.buddy-council/…` path in these instructions is inside it, and every script that takes `--project` gets
  it.
- **`stop`** — show it as it is and end the command. It covers: no settings yet; settings from an older version
  ("run /bc:setup once to finish updating"); requirements still from Excel; and requirements, test cases, or a
  needed tool saved but not verified yet.
- **`ask`** — what to fix on the spot before reading anything, then carry on:
  - `need: "link"`: the project doesn't use a tool this command needs. Ask for `words` (`your board link`), then
    follow the guide in `guide` → *From another command*: it connects the tool and saves it.
  - `need: "key"`: this computer has no key for `site`, a tool the command reads. Ask for `words`, and save it as
    the setup command does — `bc_keys.py path`, your file tool, `bc_keys.py check` (`skills/setup/SKILL.md` →
    *Saving a key*).
  Run `status` again afterwards.
- **`lines`** — print each one in the command's report, as it is: a tool it reads that the project doesn't use
  (`Board: not used in this project. Add it with /bc:setup <board link>.`) or one skipped because it isn't verified.
- **`tools`** — each tool's `state` (`ready`, `pending`, `none`, `missing`), `site`, and, when its key has one, the
  `account` it signs in as (Jira Cloud's `user_identifier`). Read only the `ready` ones.

Then read `<project>/.buddy-council/sources.json` with your view tool for what the command needs. Every connector
call passes `site`: `requirements.base_url` for Jama, `test_cases.base_url` for TestRail, `jira.base_url` for Jira
(ADR 0008).

## When a tool says no

Something can change on a tool's side after setup — a key expires, a board or suite is deleted. The command that
runs into it asks only for the fix, makes it, and carries on; it never sends the user to `/bc:setup` for these:

- **The site refuses the key, or has none** (HTTP 401, "no longer accepts", "no key is saved", a key file that
  can't be read): ask for that site's key, worded by `bc_setup.py links --link '<base_url>'` → `key.ask`, save it
  as the setup command does (`bc_keys.py path`, your file tool, `bc_keys.py check` — `skills/setup/SKILL.md` →
  *Saving a key*), and retry the call that failed.
- **The board, suite, or Jama project is gone** (HTTP 404, "does not exist", a board the site no longer lists):
  ask for its new link and follow its guide → *From another command* (`providers/jira/setup.md`,
  `providers/testrail/setup.md`, `providers/jama/setup.md`): it connects the tool and saves it through
  `bc_setup.py`. Then carry on. A Jama requirements root that moved needs no question: the Jama guide's *Scope*
  anchors on the test suite again.
- **The account may not see it** (HTTP 403): name the account and what it can't see — that is for the site's
  admin, not a new key.
- **The site can't be reached**: say so — a self-hosted site usually needs the VPN. There is nothing to save.

## Saving

A command that changes the settings — a V&V board, a cached field id — saves through the same script, as its own
command with the change as JSON on one line, every `'` written as `\u0027`:

```bash
python3 '<plugin root>/scripts/bc_setup.py' save --project '<project>' --patch '<json>'
```

The bash hook approves that shape, so it asks nothing; the script checks the change against
`config/sources.example.json` before it saves.
