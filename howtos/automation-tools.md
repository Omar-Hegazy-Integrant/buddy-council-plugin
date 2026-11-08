# Automation Tools

`/bc:automate` drives the app on a phone through **appium-mcp**, an MCP server. This how-to gets that server onto
the Mac and proves it works. It runs only when setup finds a **test project** (Appium or WebdriverIO, with an
iPhone or Android app) in the current directory. Analysis-only users download nothing.

Everything mechanical is in one script, run with macOS's own `python3`; `<plugin_root>` is the absolute path of
the plugin root:

```bash
python3 '<plugin_root>/scripts/bc_tools.py' <subcommand> --project '<absolute project path>'
```

Each subcommand prints one JSON object (`ok`, then its results); progress goes to stderr. Never edit what it
wrote by hand, and never retype its `lines`: show them as they are.

| Subcommand | What it does | Prompts? |
|---|---|---|
| `detect` | Reads the project and the Mac: stack, platforms, app builds, language, its setup script, its own Appium, Xcode, a JDK, the Android SDK, the phone. Returns `lines` for the recap. Runs nothing from the project | no |
| `project --mode check` / `--mode fix` | Runs the project's own setup script (`./setup --check`, or `./setup` to fix) without a terminal, and parses its report | **yes** — it runs the project's code |
| `ensure [--shared jdk,android-sdk]` | Installs or upgrades the pinned tools, wires `appium` into both MCP configs, proves the server answers, prunes old versions, records `automation` in `.buddy-council/sources.json`. Reports `phone_check`: `due` means run `smoke` next | no |
| `smoke` | Reads one page source from the phone, installs nothing, and records the result | no |
| `status` | Installed against pinned, wiring, and what is pending — for setup's Step 0 | no |
| `start` / `stop` | Starts or stops the project's own Appium server on `127.0.0.1` | no |
| `prune` | Removes versions nothing points at, unfinished downloads, week-old screen files, old logs | no |
| `remove` | Unwires `appium` and deletes bc's tools — only when the user asks | **yes** |

The bash hook approves every row marked "no" when the script is run as one plain command by its absolute path.
`project` and `remove` always reach the normal permission prompt, by design.

## What gets installed, and where

bc keeps its own copies, the way `uv` provides Python: **never on PATH, never shared**, and bc never edits a
shell profile, `JAVA_HOME`, or `ANDROID_HOME`.

```
~/.buddy-council/tools/node/v26.13.0-darwin-arm64/      Node, by version and SHA-256 (pins.json)
~/.buddy-council/tools/appium-mcp/1.99.2-<lock>/        appium-mcp, by appium-mcp/package-lock.json, npm ci --ignore-scripts
~/.buddy-council/tools/jdk/, android-sdk/               only with --shared, see below
~/.buddy-council/automation/page-sources/, screenshots/ what the server saves
~/.buddy-council/automation/logs/                       npm, server, and project-script logs
```

- **Pins** live in `pins.json` and `appium-mcp/package-lock.json` beside the script, and change only in a plugin
  release whose device check passed. When a release changes a pin, the next `ensure` installs the new version
  beside the old, keeps the old one wired until the new server answers, then rewires and removes the old one.
- **Downloads** use the Mac's own settings: the environment's and macOS's proxy, `~/.npmrc` (registry, proxy,
  `cafile`), the keychain's certificates when the bundled ones fail, and `NODEJS_ORG_MIRROR` for Node. Every file
  is checked against its pinned SHA-256 and every npm package against the lockfile, so any mirror is safe. If a
  download still fails, `ensure` records `pending` and the next setup run tries again.
- **Shared tools** (a JDK and the Android SDK) matter only for Android on appium-mcp's built-in drivers. A working
  one on the Mac is always used and never replaced. A missing one is installed **beside** it in
  `~/.buddy-council/tools` only when `ensure` gets `--shared` — which setup passes only when the recap listed it
  and the user said yes. Its path goes into the `appium` server's own env, nowhere else.

## The driver: the project's own Appium first

`detect` reports `driver.mode`:

- **`project`** — the Mac has the project's `appium` command with the driver for every platform the tests use
  (xcuitest for iOS, uiautomator2 for Android). The proof, and later `/bc:automate`, start that server on a free
  port of `127.0.0.1` and create sessions on it through appium-mcp's `remoteServerUrl`. Screens are then read on
  the same Appium and WebDriverAgent the project's tests run on.
- **`built-in`** — appium-mcp's own embedded drivers. On a real iPhone, WebDriverAgent then needs signing with a
  provisioning profile the user chooses: that is guided, never done for them.

## The proof

Two checks, in two calls so each fits in one 10-minute shell call (Claude Code's limit):

1. **The server answers** (`ensure`) — it is started exactly as the CLIs will start it, over MCP stdio, and
   must list `appium_session_management` and `appium_get_page_source`. Only then is it wired.
2. **One page source from the phone** (`smoke`, when `ensure` says `"phone_check": "due"`) — a session on the
   phone without launching any app, `appium_get_page_source` saved to a file, the session closed, the server
   stopped. The phone is the project's UDID setting when it is set (`APOLLO_UDID`, read from the project's
   setup script), else the first phone connected over USB.

A check that passes is kept as `proof` in the `automation` block, so a phone unplugged later doesn't undo
`ready`; a check that fails on the connected phone drops it, and so does a different phone or driver. No phone,
a locked or untrusted one, or WebDriverAgent that won't start makes `status: "pending"` with the reason and what
the person should do. Setup checks again on its next run, the way it re-tests a pending Jira board.

`ensure` records `pending` before it installs anything, so a run the CLI stops early is finished next time. It
never replaces an `appium` server the user added themselves, and when a new version doesn't answer it puts the
working one back into any config that lost it.

## Rules

- **Never install what the user didn't see.** Run `ensure` only after the recap that listed it was confirmed.
  Pass `--shared` only with the items the recap named.
- **Never run the project's script yourself** with bash: use `project`, which captures and parses its report.
  Run `--mode fix` only when the recap said the project's fix would run.
- **Whatever needs a person is passed on, not attempted**: `sudo`, the App Store, signing, and the phone. When the
  project's fix leaves problems, `project` returns `needs_you` — show it as "run ./setup in a terminal".
- **Never write the `appium` entry by hand**, never add `appium_*` write tools to an allowlist, and never call
  `remove` unless the user asked for it.
- Never delete anything under `~/.buddy-council/tools` with `rm`; `prune` and `remove` are the only ways.
