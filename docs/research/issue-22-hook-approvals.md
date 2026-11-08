# What can a hook allow for appium-mcp's phone actions?

Research for [#22](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/22) on map
[#20](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/20). It feeds the decision
[#29](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/issues/29) "How are taps, typing, and app launches approved?".

Read on 2026-11-08. Nothing was run: no hook, no assistant session, no phone, no Appium. Every claim cites the doc or the
source line it comes from. Versions are in [section 5](#5-versions-dates-and-surprises).

A **pre-tool hook** below means Claude Code's `PreToolUse` and Copilot's `preToolUse`: a script the assistant runs
before each tool call, which can let the call through, block it, or ask the person.

## 1. Answer

- **A hook sees each call in full; the assistants' own approvals of an MCP tool cover the whole tool.** In Claude Code,
  Copilot CLI, and VS Code's Copilot sessions, a pre-tool hook gets the tool name, all its arguments, the assistant's
  session id, and the working folder. It can answer allow, deny, or ask, and can rewrite the arguments. The assistants'
  own approvals allow a whole MCP tool (any arguments), for one call, one session, or for good in that repository.
- **Taps, typing, swipes, and button presses name no app, no phone, and no server.** They carry only an optional Appium
  session id. The phone (`appium:udid`), the app (`appium:bundleId`, `appium:appPackage`, `appium:app`, or the `id` of
  `appium_app_lifecycle`), and the server (`remoteServerUrl`) show up only when a session starts or an app is launched.
  So a hook can pin phone, app, and server when the session starts, then let that session's actions through.
- **"Only this Mac's Appium" already holds without a hook.** appium-mcp refuses any other server, because bc starts it
  with `REMOTE_SERVER_URL_ALLOW_REGEX` set to `127.0.0.1`, `localhost`, and `[::1]`.
- **"Only while a `/bc:automate` run is going on" works as a marker file the hook checks.** The marker names the project
  folder, the assistant session, the phone, the app, and an expiry. It must be written by something the model can't do
  alone: a hook on the person's own prompt, or one prompt the hook forces and the person answers.
- **No hook can see which app is on screen when a tap lands.** Only appium-mcp can. Version 1.99.2 loads a guard of
  your own (`--plugin`, with a `beforeCall` check) that sees the live session and can refuse the call.
- **A broken hook never allows.** In Claude Code, a crash or a timeout falls back to the normal approval flow. In
  Copilot, a crash denies the call and a timeout falls back to the normal approval flow.

## 2. What a hook sees for a phone action

### 2.1 What appium-mcp 1.99.2's tools take

| Tool (what it does) | App | Phone | Server | Appium session |
|---|---|---|---|---|
| `appium_gesture`: tap, double tap, long press, scroll, swipe, pinch, scroll to element, back ([schema](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/gestures/schema.ts#L10-L173)) | no | no | no | optional `sessionId` ([L172](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/gestures/schema.ts#L172)) |
| `appium_set_value`: typing; `text`, `elementUUID` ([L16-L32](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/interactions/set-value.ts#L16-L32)) | no | no | no | optional |
| `appium_perform_actions`, `appium_drag_and_drop`, `appium_mobile_keyboard`, `appium_alert`, `appium_mobile_press_key` | no | no | no | optional |
| `appium_app_lifecycle`: activate, terminate, install, uninstall, list, is_installed, query_state, background, clear, deep_link ([L17-L63](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/app-management/app.ts#L17-L63)) | `id`, or `name` matched loosely; `path` for install; `url` for deep links | no | no | optional |
| `appium_session_management` create ([L30-L75](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/session.ts#L30-L75)) | inside `capabilities`, a JSON string: `appium:bundleId`, `appium:appPackage`, `appium:app` (all optional) | inside `capabilities`: `appium:udid` (optional) | `remoteServerUrl` (optional) | returned in the result |
| `appium_session_management` attach, select, detach, delete | no | no | `remoteServerUrl` for attach | `sessionId` |
| `select_device` ([L80-L89](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/select-device.ts#L80-L89)) | no | `deviceUdid` | no | no |
| `appium_mobile_permissions` ([L12-L59](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/app-management/permissions.ts#L12-L59)) | `id` or `name` | no | no | optional |
| `appium_prepare_ios_real_device`: signs WebDriverAgent with a provisioning profile ([L404-L418](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/ios/prepare-ios-real-device.ts#L404-L418)) | no | `udid` | no | no |

What follows from the code:

- **No `sessionId` means "the active session".** appium-mcp then acts on whichever session is active
  ([`resolveDriver`](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/tool-response.ts#L84-L106)), and
  `select` can change which one that is.
- **A session create with no `appium:udid` picks the phone itself.** It uses the device an earlier `select_device` call
  chose, or the only device it finds
  ([Android L121-L163](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/create-session.ts#L121-L163),
  [iOS L190-L253](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/create-session.ts#L190-L253)).
- **A session create with no `remoteServerUrl` runs Appium's drivers inside appium-mcp, on this Mac.** With a URL, it
  connects to that server
  ([L330-L378](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/create-session.ts#L330-L378)).
- **An app `name` resolves loosely.** appium-mcp scores every installed app by display name and package name and takes
  the best match
  ([L19-L79](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/app-management/resolve-app-id.ts#L19-L79)).
  A hook can't tell from the name which app it will hit.
- **Some calls leave the app on purpose:** the HOME and APP_SWITCH buttons
  ([press-key L41-L58](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/interactions/press-key.ts#L41-L58)),
  deep links, and activating another app id.
- **Some calls change the phone, not the app:** lock and unlock
  ([device-control](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/device-control.ts#L16-L33)),
  app permissions, file push and pull
  ([file-transfer L34-L39](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/file-transfer.ts#L34-L39)),
  location ([geolocation](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/geolocation.ts#L8-L37)),
  and WebDriverAgent signing.
- **appium-mcp ends its own sessions when the assistant disconnects**, by default
  (`APPIUM_MCP_ON_CLIENT_DISCONNECT=delete_all`,
  [create-server L241-L268](https://github.com/appium/appium-mcp/blob/v1.99.2/src/create-server.ts#L241-L268)).

What bc already does:

- `bc_tools.py` starts appium-mcp with `NO_UI=true`, `ALLOW_REMOTE_APP_URLS=false`, and `REMOTE_SERVER_URL_ALLOW_REGEX`
  limited to this Mac
  ([bc_tools.py L75](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/scripts/bc_tools.py#L75),
  [L1247-L1256](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/scripts/bc_tools.py#L1247-L1256)).
- It starts the project's own Appium on a free port of `127.0.0.1`
  ([L1504-L1525](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/scripts/bc_tools.py#L1504-L1525)).
- Its own phone check creates a session with `appium:udid` and `appium:noReset: true`
  ([L1545-L1580](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/scripts/bc_tools.py#L1545-L1580)).
- `hooks/auto-approve-mcp.sh` allows the eight read-only appium tools plus page sources and screenshots. Every other
  appium call falls through to the normal approval flow
  ([L54-L91](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/hooks/auto-approve-mcp.sh#L54-L91)).

### 2.2 Claude Code

A tap reaches the plugin's hook (registered for `mcp__.*`,
[hooks/hooks.json L15-L25](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/hooks/hooks.json#L15-L25))
roughly like this. The fields are documented; the values are made up.

```json
{
  "session_id": "…",
  "prompt_id": "…",
  "transcript_path": "…",
  "cwd": "/path/to/test-project",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "mcp__plugin_bc_appium__appium_gesture",
  "tool_input": { "action": "tap", "elementUUID": "…" },
  "tool_use_id": "toolu_…",
  "mcp_server": { "name": "…", "source": "plugin" }
}
```

- **Session and turn.** `session_id` is the assistant session. `prompt_id` names the person's prompt being worked on
  ([common input fields](https://code.claude.com/docs/en/hooks#common-input-fields)). Inside a subagent, the call adds
  `agent_id` and `agent_type` (same section). The session id appears to stay the main one: the
  [SubagentStop example](https://code.claude.com/docs/en/hooks#subagentstop) shows the main session's `session_id`
  beside the subagent's `agent_id`. A plugin's agent shows up as `bc:<name>`
  ([SubagentStart](https://code.claude.com/docs/en/hooks#subagentstart)).
- **Tool name.** The plugin's own servers are named `mcp__plugin_bc_<server>__<tool>`
  ([match MCP tools](https://code.claude.com/docs/en/hooks#match-mcp-tools)). Since v2.1.274, `mcp_server.source`
  says where the server came from, and the docs say to base trust on it rather than on the name
  ([PreToolUse input](https://code.claude.com/docs/en/hooks#pretooluse-input)).
- **Permission mode.** `permission_mode` tells the hook whether the session is in `default`, `auto`, `plan`, and so on.
- **Answers** ([PreToolUse decision control](https://code.claude.com/docs/en/hooks#pretooluse-decision-control)):
  - `allow` skips the prompt. It does not beat a deny or ask rule in the person's settings
    ([precedence](https://code.claude.com/docs/en/permissions#precedence-among-rules-hooks-and-mods)). In plan mode it
    has no effect on calls that change something
    ([hook approvals in plan mode](https://code.claude.com/docs/en/permissions#hook-approvals-in-plan-mode)).
  - `deny` blocks the call, and the model reads the reason.
  - `ask` shows the prompt, with the hook's reason and a `[plugin:bc]` label. In auto mode, an `ask` goes to the
    person, not to the classifier
    ([ask in auto mode](https://code.claude.com/docs/en/hooks#pretooluse-ask-decisions-in-auto-mode)).
  - `defer` works only in `-p` runs.
  - `updatedInput` replaces the whole argument object.
  - When several hooks answer, deny beats defer, which beats ask, which beats allow.
- **Failures** ([exit codes](https://code.claude.com/docs/en/hooks#exit-code-output),
  [timeouts](https://code.claude.com/docs/en/hooks#timeouts)): exit code 2 blocks. Any other failure, and a timeout,
  falls back to the normal approval flow, unless the hook sets `"onFailure": "block"` (v2.1.295 and later).

### 2.3 Copilot CLI

The plugin registers its hook under the camelCase `preToolUse` with no matcher, so it runs before every tool
([com.github.copilot/hooks/hooks.json L12-L16](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/com.github.copilot/hooks/hooks.json#L12-L16)).
A tap arrives like this ([preToolUse input](https://docs.github.com/en/copilot/reference/hooks-reference#pretooluse--pretooluse)):

```json
{
  "sessionId": "…",
  "timestamp": 1794200000000,
  "cwd": "/path/to/test-project",
  "toolName": "appium-appium_gesture",
  "toolArgs": "{\"action\":\"tap\",\"elementUUID\":\"…\"}"
}
```

- **Tool name.** It is `<server>-<tool>`
  ([MCP server naming](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#mcp-server-naming)).
  Nothing in the payload says which `appium` server it is. A plugin's server outranks the user's and the workspace's
  `appium`, but a server passed with `--additional-mcp-config` outranks the plugin's
  ([loading priority](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#mcp-server-loading-priority)).
- **Arguments.** `toolArgs` arrives as a JSON string. The docs type it `unknown`; the PascalCase variant's
  `tool_input` is the same value "parsed from JSON string when possible". This repo's hooks and tests already parse it
  as a string.
- **Subagents.** Inside a subagent, the payload adds `agentId`, `agentType`, and `parentAgentId`
  ([input payloads](https://docs.github.com/en/copilot/reference/hooks-reference#hook-event-input-payloads)). The
  installed SDK's types (1.0.92) say "Child tool hooks use the child session ID"
  (`~/.copilot/pkg/darwin-arm64/1.0.92/copilot-sdk/types.d.ts:1103-1111`). So a subagent's calls may carry a different
  `sessionId` from the main session's.
- **Answers** ([decision control](https://docs.github.com/en/copilot/reference/hooks-reference#pretooluse-decision-control)):
  - Top-level `permissionDecision` (`allow`, `deny`, `ask`), `permissionDecisionReason` (required for deny), and
    `modifiedArgs`.
  - `allow` suppresses the prompt (since 1.0.18,
    [changelog](https://github.com/github/copilot-cli/blob/main/changelog.md)).
  - In autopilot, `ask` shows no prompt and the call is denied.
  - If any hook says deny, the call is blocked
    ([tool names for hook matching](https://docs.github.com/en/copilot/reference/hooks-reference#tool-names-for-hook-matching)).
- **Failures** ([exit codes](https://docs.github.com/en/copilot/reference/hooks-reference#exit-codes-for-command-hooks)):
  exit 2, a crash, or any other non-zero exit denies the call, even after printing `allow`. A timeout lets the call go
  to the normal approval flow.

### 2.4 VS Code

- **Copilot sessions** (the "Copilot" session target) run the same hooks as Copilot CLI, with the same payloads and
  answers ([configure hooks](https://code.visualstudio.com/docs/agent-customization/hooks)). They read the plugin's
  `com.github.copilot/hooks/hooks.json`. Returning `{}` makes no decision; `ask` needs someone there to answer.
- **Older "Local" sessions** send snake_case input (`tool_name`, `tool_input` as an object, `tool_use_id`), read
  decisions from `hookSpecificOutput`, ignore matcher values, and only warn on exit codes other than 0 and 2
  ([Local hooks reference](https://code.visualstudio.com/docs/agents/reference/hooks-reference)). The plugin's scripts
  already print both decision shapes.
- **Claude sessions** in VS Code follow Claude Code's hook docs.

## 3. Ways to allow narrowly

Each way below says what the hook checks, where it finds it, the risks, and where it works. They stack: a real rule
would combine several.

### 3.1 By tool and action (the base of every other way)

- **Checks:** the tool name and its `action`. For example, allow `appium_gesture` taps, scrolls, and swipes,
  `appium_set_value`, `appium_mobile_keyboard`, and `appium_alert`. Keep asking for install, uninstall, clear, and
  deep links, file push and pull, permissions, lock and unlock, `select_device`, and WebDriverAgent signing.
- **Where:** `tool_name` and `tool_input.action` (Claude Code); `toolName` and the parsed `toolArgs.action` (Copilot).
- **Risks:** on its own, this allows those actions in any session, on any phone, in any app, at any time.
  - In Claude Code, the hook should accept only the plugin's own spelling, `mcp__plugin_bc_appium__…`, with
    `mcp_server.source` equal to `plugin`. Today's read-only list also accepts `mcp__appium__…`, which can be someone
    else's Appium server without bc's guards.
  - In Copilot, any server named `appium` matches.
- **Works in:** all three.

### 3.2 By server: this Mac's Appium

- **Checks:** `remoteServerUrl` on create and attach is either absent (drivers inside appium-mcp, on this Mac) or the
  `http://127.0.0.1:<port>` server bc started. bc records that server, with its port, in
  `~/.buddy-council/automation/server.json`
  ([bc_tools.py L1504-L1525](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/scripts/bc_tools.py#L1504-L1525)).
- **Already enforced without a hook:** appium-mcp checks every URL against `REMOTE_SERVER_URL_ALLOW_REGEX` before it
  connects, and again before it reconnects to a saved session
  ([url.ts L22-L51](https://github.com/appium/appium-mcp/blob/v1.99.2/src/utils/url.ts#L22-L51),
  [create-session L332](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/create-session.ts#L332),
  [tool-response L116-L130](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/tool-response.ts#L116-L130)).
  With `ALLOW_REMOTE_APP_URLS=false`, it also refuses app builds given as web addresses
  ([remote-app-policy](https://github.com/appium/appium-mcp/blob/v1.99.2/src/utils/remote-app-policy.ts#L3-L45)).
- **Risks:** the check covers only the URL given. A server's "direct connect" answer can still send the client
  elsewhere, and bc leaves `REMOTE_SERVER_ENABLE_DIRECT_CONNECT` at its default, `true`
  ([README L184-L186](https://github.com/appium/appium-mcp/blob/v1.99.2/README.md#L184-L186)). That matters only if the
  local server is not trusted.
- **Works in:** all three, and in every assistant without a hook, because appium-mcp enforces it.

### 3.3 By phone

- **Checks:**
  - On `appium_session_management` create, `appium:udid` in `capabilities` equals `automation.phone.udid` in the
    project folder's `.buddy-council/sources.json`
    ([sources.example.json L100-L107](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/config/sources.example.json#L100-L107)).
  - `select_device.deviceUdid` and `appium_prepare_ios_real_device.udid` must match too.
  - A create with no udid should ask, because appium-mcp then picks the phone itself (section 2.1).
- **Where:** `capabilities` is a JSON string inside the arguments. The hook must parse it, and ask when it can't.
- **Risks:** later calls name no phone; they go to the session. So "by phone" holds only if the hook also controls
  which session those calls use (3.5).
- **Works in:** all three, at session start.

### 3.4 By app

- **Checks:**
  - At session create, `appium:bundleId` (iOS), `appium:appPackage` (Android), or the `appium:app` path matches
    `automation.apps` in `sources.json`
    ([L108-L110](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/config/sources.example.json#L108-L110)).
    The hook can also refuse a reset that wipes app data (`appium:fullReset`, or `appium:noReset: false`).
  - On `appium_app_lifecycle`, `id` must equal that app id for activate, terminate, query_state, and is_installed.
  - Ask for `name` (it matches loosely), install, uninstall, clear, deep_link, and background, and for the HOME and
    APP_SWITCH buttons.
- **Risks:** taps carry no app. Once a session is open, a tap lands on whatever is on screen: a system dialog, another
  app the app opened (a browser, Settings), or the home screen. The hook can't see the screen; only appium-mcp can
  (3.8).
- **Works in:** all three, at session start and app launch only.

### 3.5 By Appium session id

- **Checks:** the call's `sessionId` equals the session the Walk created.
- **Where:**
  - The hook learns the id after the create call. A post-tool hook reads the result, which says "… session created
    successfully with ID: <id>"
    ([create-session L386-L388](https://github.com/appium/appium-mcp/blob/v1.99.2/src/tools/session/create-session.ts#L386-L388)).
    Claude Code gives the result as `tool_response`
    ([PostToolUse input](https://code.claude.com/docs/en/hooks#posttooluse-input)); Copilot gives
    `toolResult.textResultForLlm`
    ([postToolUse](https://docs.github.com/en/copilot/reference/hooks-reference#posttooluse--posttooluse)).
  - Before each later call, the hook denies a call with no `sessionId`, with a reason telling the model to add it. Or it
    adds the id itself with `updatedInput` (Claude Code) or `modifiedArgs` (Copilot).
  - It asks for `select`, `attach`, `detach`, and any second `create`.
- **Ends on its own:** appium-mcp deletes its sessions when the assistant disconnects (section 2.1), so the id dies
  with the run.
- **Risks:**
  - The hook parses result text, which can change between appium-mcp versions; bc pins 1.99.2.
  - Rewritten arguments mean what runs is not what the model wrote. In Claude Code's auto mode, a rewritten call can be
    denied unless the hook also answers `allow`
    ([updatedInput in auto mode](https://code.claude.com/docs/en/hooks#pretooluse-updatedinput-in-auto-mode)).
- **Works in:** all three.

### 3.6 By "a `/bc:automate` run is going on"

- **Checks:** a marker file the hook reads, for example
  `{project folder, assistant session id, phone udid, app id, server, started_at, expires_at}`. The hook allows a Walk
  action only when:
  - the call's working folder is inside that project folder,
  - the assistant session matches,
  - and the time is before `expires_at`.

  Session start and app launch must also match the phone, app, and server (3.2 to 3.4).
- **Where the marker lives:** outside the working folders, for example under `~/.buddy-council/automation/`. In
  Claude Code's auto and acceptEdits modes, the model can change files inside the working folders without asking
  ([available modes](https://code.claude.com/docs/en/permission-modes#available-modes),
  [how auto mode evaluates actions](https://code.claude.com/docs/en/permission-modes#how-auto-mode-evaluates-actions)),
  so it could write its own marker there.
- **Who writes it:** something the model can't trigger on its own.
  1. **A hook on the person's own prompt.**
     - In Claude Code, `UserPromptExpansion` fires when a command the person typed expands, with `command_name`,
       `command_args`, `command_source`, and `session_id`. When the model calls a skill, the `Skill` tool's
       `PreToolUse` fires instead
       ([UserPromptExpansion](https://code.claude.com/docs/en/hooks#userpromptexpansion)). `UserPromptSubmit` also gets
       the text, but it also fires for scheduled tasks, background subagent reports, and messages from other sessions
       ([UserPromptSubmit](https://code.claude.com/docs/en/hooks#userpromptsubmit)).
     - In Copilot, `userPromptSubmitted` gets `prompt`, `sessionId`, and `cwd`. Its output is ignored, but the script
       can still write a file
       ([userPromptSubmitted](https://docs.github.com/en/copilot/reference/hooks-reference#userpromptsubmitted--userpromptsubmit)).
     - Because `/bc:automate` is a skill only the person can start
       ([ADR 0006](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/docs/adr/0006-commands-are-skills-how-tos-are-files.md)),
       this ties the approval to the person typing the command. It shows the person nothing about the phone or app,
       though.
  2. **One prompt the hook forces.** On the first session create, the hook answers `ask`, with a reason that names the
     phone, the app, the server, and how long the approval lasts. When the person says yes and the call runs, the
     post-tool hook writes the marker. In Claude Code, the prompt appears even in auto mode. In Copilot's autopilot,
     and in `-p` runs of either assistant, `ask` is denied, so this approval must happen in an interactive session,
     before autopilot starts.
  3. **A script step that prompts**, such as a `bc_tools.py` subcommand that the bash hook answers `ask` for, instead of
     approving it as it does today
     ([validate-bash.sh L75-L77](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/hooks/validate-bash.sh#L75-L77)).
     The same prompt rules as option 2 apply.
- **Expiry and end:** a short idle timeout that each allowed call extends, plus a hard limit. The command's last step
  deletes the marker. `SessionEnd` (Claude Code) and `sessionEnd` (Copilot) hooks can delete the markers of a session
  that ends.
- **Matching the session:**
  - In Claude Code, a subagent's calls appear to carry the main `session_id` (2.2), so a marker keyed on it would cover
    a tapping subagent. `prompt_id` changes when the person replies mid-run, so don't key on it.
  - In Copilot, a subagent's calls may carry the subagent's own session id (2.3). A marker keyed on the main session
    may then miss a tapping subagent. Key on the project folder and the expiry instead, or check `parentAgentId` once
    it is clear what it holds.
- **Risks:**
  - Inside the approved window, the hook can't judge intent. Text on the app's screen could steer the model to tap
    something harmful in the same app on the same phone.
  - A marker left by a crashed run stays valid until it expires.
- **Works in:** Claude Code, Copilot CLI, and VS Code's Copilot sessions, with the subagent caveat for the last two.

### 3.7 By agent

- **Checks:** that the call comes from bc's own Walk agent, if the Walk runs in one.
- **Where:**
  - In Claude Code, `agent_type` is `bc:<name>`. A plugin's agent can't carry its own hooks or permission mode (Claude
    Code ignores those fields for plugin agents,
    [subagent scopes](https://code.claude.com/docs/en/sub-agents#choose-the-subagent-scope)), so the plugin-wide hook
    must check it.
  - In Copilot, `agentType` and `agentId`. Copilot also runs hooks declared in a custom agent's own file (CLI 1.0.97
    and later, [agent-scoped hooks](https://docs.github.com/en/copilot/reference/hooks-reference#agent-scoped-hooks));
    the docs don't say whether a plugin's agents get them.
- **Risks:** the main model can start that agent with any instructions.
- **Works in:** Claude Code and Copilot CLI. VS Code's Copilot sessions behave like Copilot CLI.

### 3.8 Inside appium-mcp (not a hook)

- **What:** appium-mcp 1.99.2 loads a plugin given on its command line, `--plugin=<absolute path>`
  ([README L600-L631](https://github.com/appium/appium-mcp/blob/v1.99.2/README.md#L600-L631),
  [cli/index.ts L40-L42](https://github.com/appium/appium-mcp/blob/v1.99.2/src/cli/index.ts#L40-L42)). The plugin's
  `beforeCall` runs before every tool call. It gets the tool name, the arguments, and the live session
  (`getSessionInfo`, `getDriver`), and can refuse the call by returning a result
  ([plugin.ts L43-L79](https://github.com/appium/appium-mcp/blob/v1.99.2/src/plugin.ts#L43-L79),
  [L344-L367](https://github.com/appium/appium-mcp/blob/v1.99.2/src/plugin.ts#L344-L367),
  [README L701](https://github.com/appium/appium-mcp/blob/v1.99.2/README.md#L701)).
- **Checks no hook can make:** the session's real phone and capabilities, and the app in front before each tap. The
  drivers inside appium-mcp have commands for the app in front: `mobile: activeAppInfo` (appium-xcuitest-driver
  12.22.8, `build/lib/execute-method-map.js:276`) and `mobile: getCurrentPackage` (appium-android-driver 14.2.5,
  `build/lib/execute-method-map.js:391`).
- **What it doesn't do:** approve anything. Approvals stay with the assistant. But it makes a broad approval safe to
  give, and it works in every assistant, including Codex and Cursor, which run no hooks.
- **Costs and limits:** one more round trip to the phone for each checked call. The tool allow-list
  (`policy.allowTools`) exists only when you build the server in code, not through the command line bc uses
  ([server.ts L7-L23](https://github.com/appium/appium-mcp/blob/v1.99.2/src/server.ts#L7-L23)). bc would add the flag
  to the record `bc_tools.py` writes.

### Summary: which way works where

| Way | Claude Code | Copilot CLI | VS Code (Copilot sessions) |
|---|---|---|---|
| By tool and action | yes | yes | yes |
| Only bc's own appium server | yes: `mcp__plugin_bc_appium__…` and `mcp_server.source` | by name only | by name only |
| By server | yes, and appium-mcp enforces it | yes, and appium-mcp enforces it | yes, and appium-mcp enforces it |
| By phone | at session start | at session start | at session start |
| By app | at session start and app launch | at session start and app launch | at session start and app launch |
| By Appium session id | yes, with a post-tool hook | yes, with a post-tool hook | yes, with a post-tool hook |
| Run marker written when the person types the command | yes (`UserPromptExpansion`) | yes (`userPromptSubmitted`) | yes |
| Run marker after one forced prompt | yes, even in auto mode, but not in `-p` | yes, but not in autopilot or `-p` | yes, but not in Autopilot |
| Keyed on the session, including subagents | yes, as the docs imply (2.2) | not certain (2.3) | not certain |
| By agent | yes (`agent_type`) | yes (`agentType`) | yes |
| The app on screen at each tap | no; only inside appium-mcp (3.8) | no | no |

## 4. What the assistants offer on their own

None of these allows by argument. Each covers a whole MCP tool, or a whole server, for as long as it lasts.

### Claude Code

| Mechanism | How long it lasts | What else it lets through |
|---|---|---|
| "Yes" on a prompt | that call | nothing |
| "Yes, and don't ask again" on an MCP tool's prompt. Only tools marked `requiresUserInteraction` lack it ([require approval](https://code.claude.com/docs/en/mcp#require-approval-for-a-specific-tool)), and the [2.1.246 changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) says the option writes an allow rule | for good: saved to `.claude/settings.local.json` at the repository root ([permission system](https://code.claude.com/docs/en/permissions#permission-system)) | that tool with any arguments, in every later session in that repository |
| `permissions.allow` with `mcp__plugin_bc_appium__<tool>` or `mcp__plugin_bc_appium__*` in user, project, or local settings; `--allowedTools` for one run ([MCP rules](https://code.claude.com/docs/en/permissions#mcp)) | as long as the setting, or the run | the tool, or every appium tool for the `*` form, with any arguments. Allow rules can't name arguments: settings files skip `mcp__` rules with parentheses ([match by input parameter](https://code.claude.com/docs/en/permissions#match-by-input-parameter)) |
| `--disallowedTools` with a parameter, such as `mcp__plugin_bc_appium__appium_app_lifecycle(action:uninstall)` (same section) | that run | the opposite: it denies matching calls. It is the only way Claude Code's own rules look at an MCP tool's arguments, and only to deny |
| `allowed-tools` in the command's `SKILL.md` ([pre-approve tools](https://code.claude.com/docs/en/skills#pre-approve-tools-for-a-skill)) | the turn that starts `/bc:automate`; cleared at the person's next message | the listed tools with any arguments, during that turn. Managed settings with `allowManagedPermissionRulesOnly` can switch it off for some skill sources |
| `auto` mode, the starting mode for interactive terminal and VS Code sessions since v2.1.283 ([which mode a session starts in](https://code.claude.com/docs/en/permission-modes#which-mode-a-session-starts-in)) | the session | a classifier judges each call that no rule or hook settled, against the conversation, on any tool. 3 blocks in a row or 20 in a session pause auto mode and bring back prompts ([repeated-block thresholds](https://code.claude.com/docs/en/permission-modes#repeated-block-thresholds)) |
| `dontAsk` mode, set at startup or as the default mode ([dontAsk](https://code.claude.com/docs/en/permission-modes#allow-only-pre-approved-tools-with-dontask-mode)) | the session | nothing extra: allow rules and hook allows run, and everything else is denied instead of prompting. This fits an unattended Walk with a hook |
| `bypassPermissions` mode | the session | everything, apart from the few actions no mode approves |
| A `PermissionRequest` hook ([PermissionRequest](https://code.claude.com/docs/en/hooks#permissionrequest)) | per prompt, or for the session through `updatedPermissions` with `destination: "session"` | whatever the hook allows; it answers the prompt for the person |

### Copilot CLI

| Mechanism | How long it lasts | What else it lets through |
|---|---|---|
| Allow once on a prompt | that call | nothing |
| Allow for the rest of the session ([allowing tools](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools)) | until the session ends or `/reset-allowed-tools` | that tool with any arguments, in any later task in that session |
| "Don't ask again in this repo" | for good: saved in `~/.copilot/permissions-config.json` under the repository root, as `kind: "mcp"`, `serverName: "appium"`, and a `toolName`, or `null` for every tool of the server ([permissions-config.json](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference#permissions-configjson)) | that tool, or the whole server, with any arguments, in every later session there |
| `--allow-tool='appium(appium_gesture)'` or `--allow-tool=appium` ([tool permission patterns](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#tool-permission-patterns)) | that run | the tool, or the whole server, with any arguments. `--deny-tool` still wins |
| `/allow-all`, `/yolo`, `--allow-all-tools`, `--allow-all` | the session or the run | every tool, path, and web address, not just the phone. Deny rules still apply |
| Assisted permissions: `/permissions assisted`, or `--assisted-approval` for `-p` ([assisted permissions](https://docs.github.com/en/copilot/concepts/customizations/allowing-safe-commands)) | the session or the run | a model approves the calls it judges safe. MCP calls are subject to it ([trust levels](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#mcp-server-trust-levels)). In autopilot and `-p`, calls it won't approve are denied |
| Autopilot mode ([autopilot](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/use-autopilot-mode)) | until switched off | with manual approval, every call that would prompt is denied, including a hook's `ask` |
| `allowed-tools` in a skill ([add skills](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)) | not stated | the listed tools. How to name an MCP tool there is not stated |
| A `permissionRequest` hook ([decision control](https://docs.github.com/en/copilot/reference/hooks-reference#permissionrequest-decision-control)) | per request | whatever it allows; it runs before rules and session approvals |

### VS Code (Copilot sessions)

From [Manage approvals and permissions](https://code.visualstudio.com/docs/agents/run/approvals) unless noted.

| Mechanism | How long it lasts | What else it lets through |
|---|---|---|
| Approve once on a prompt, or a broader scope the prompt offers (the scopes depend on the tool and the session type) | the call, the session, or later sessions on the same agent host | that tool with any arguments |
| "Chat: Manage Tool Approval", per MCP tool or for a whole MCP server | until changed | the same |
| `chat.tools.eligibleForAutoApproval` set to `false` for a tool | until changed | the opposite: that tool always asks, and no prompt offers to stop asking |
| Permission levels Manual, Assisted, and Allow all; `/yolo`, `/autoApprove`; `chat.tools.global.autoApprove` | the session, or every workspace | Allow all: every tool |
| Autopilot mode | until switched off | approves every tool |
| Worktree sessions in the Agents window | always | always use Allow all |
| A plugin's MCP servers | from install | trusted without a separate server prompt ([plugin reference](https://github.com/microsoft/vscode-docs/blob/main/docs/agents/reference/agent-plugin-reference.md)) |

### What any tool-wide approval lets through besides the Walk

- Taps and typing in any app on the phone, in any session, for as long as the approval lasts. That includes text typed
  into another app's field.
- With `appium_session_management`: sessions on any connected phone or simulator, attaching to sessions on this Mac's
  servers, and deleting sessions.
- With `appium_app_lifecycle`: installing a build from a local path, uninstalling, clearing app data, deep links, and
  bringing any app to the front.
- With the whole server: pushing and pulling files, changing app permissions, setting the location, locking and
  unlocking, and signing WebDriverAgent with a provisioning profile.
- With allow-all modes and Autopilot: everything else too, such as shell commands, file writes, and the web.

## 5. Versions, dates, and surprises

### Versions read

- **Claude Code docs:** `code.claude.com/docs/en/` pages hooks, permissions, permission-modes, skills, mcp, settings,
  plugins-reference, plugins/components, sub-agents, and auto-mode-config, fetched 2026-11-08. The docs carry no
  version. The newest release in the
  [public changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) is 2.1.319 (file last changed
  2026-11-06). Facts tied to a version give it above.
- **Copilot CLI docs:** the `docs.github.com` pages linked above, fetched 2026-11-08. The newest release in the
  [public changelog](https://github.com/github/copilot-cli/blob/main/changelog.md) is 1.0.107 (2026-11-06). The CLI on
  this Mac reports 1.0.108-1. The SDK types quoted come from the installed 1.0.92 package.
- **VS Code docs:** `microsoft/vscode-docs`, `DateApproved: 11/4/2026`. `docs/agent-customization/hooks.md` and
  `docs/agents/reference/hooks-reference.md` at 062330f (2026-11-04), `docs/agents/run/approvals.md` at ff8e923
  (2026-11-05), and `docs/agents/reference/agent-plugin-reference.md` at b4683a7 (2026-11-08).
- **appium-mcp 1.99.2**, installed at `~/.buddy-council/tools/appium-mcp/1.99.2-11d9b60ce84c/`. Every file cited is
  identical to the upstream tag `v1.99.2` (c1b3fc3), which the links point to.
- **This plugin:** `origin/main` at 3d980ff (version 0.28.0).

### Not checked

These need a probe: a hook that only logs its input, run on a read-only tool. No phone is needed.

- Whether Copilot's `toolArgs` is always a string.
- Which `sessionId` a Copilot subagent's tool calls carry, and what `parentAgentId` holds.
- That a Claude Code subagent's tool calls carry the main session's `session_id`, as the docs imply.
- Whether Copilot's `userPromptSubmitted` sees the typed `/bc:automate …` text.
- How long Copilot's skill `allowed-tools` lasts, and how to name an MCP tool there.
- Whether a Claude Code hook's `allow` skips the auto-mode classifier in a terminal session. Only the Cowork change
  below is documented.
- What `mcp_server.name` holds for a plugin's server in Claude Code.

### Surprises

- **Claude Code now starts in auto mode.** Interactive terminal and VS Code sessions start in auto mode since v2.1.283.
  A tap that no rule or hook settles goes to a classifier, not to the person, and 3 blocks in a row or 20 in a session
  pause auto mode. "Every tap asks" is true only in Manual mode. This changes the starting point of #29, and what the
  person sees in #34.
- **Copilot's autopilot denies anything that would prompt**, including a hook's `ask`. An unattended Walk there needs
  hook allows, or approvals given before autopilot starts (#29).
- **A Copilot subagent's tool calls may carry their own session id** (SDK 1.0.92 types). A run marker keyed on the main
  session may then miss a low-cost tapping subagent. This matters for #29 and for how the command splits up (the map's
  "Not yet specified" list).
- **appium-mcp itself can host a guard** (`--plugin`, `beforeCall`) that sees the live session and the app in front.
  It is the only place a per-tap app check can live, and it works in Codex and Cursor too (#29, #21).
- **The plugin's root `settings.json` allow-list grants nothing.** Claude Code reads only `agent` and
  `subagentStatusLine` from a plugin's `settings.json` and drops every other key
  ([default settings](https://code.claude.com/docs/en/plugins/components#default-settings)). Copilot's
  [plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference) names no
  such file. Adding appium tools there would change nothing.
- **Typed text is visible to hooks.** `appium_set_value.text` appears in hook input and in the assistant's transcript.
  bc's own log hook doesn't record it
  ([log-tool-use.sh](https://github.com/Omar-Hegazy-Integrant/buddy-council-plugin/blob/3d980ff/hooks/log-tool-use.sh)).
  This matters for #31 if a step types a password.
- **Hook allows for MCP calls may count for less later.** Claude Code 2.1.314 made a hook's `allow` of an MCP call
  count as "no answer" in auto mode, but only in Cowork cloud sessions
  ([changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)). It may spread to other surfaces.
- **bc leaves one appium-mcp guard off.** Its record for starting appium-mcp leaves
  `REMOTE_SERVER_ENABLE_DIRECT_CONNECT` at `true` (3.2).
