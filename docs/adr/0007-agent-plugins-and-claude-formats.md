# Ship the open Agent Plugins format and Claude Code's own, with the connectors inside

Buddy-Council used to install only into Claude Code and Copilot CLI, and setup wrote each assistant's connector settings itself. It now ships one repository with two manifests: the open Agent Plugins format (root `plugin.json`, `skills/`, `mcp.json`), which Copilot CLI, VS Code, Codex and Cursor read, and Claude Code's `.claude-plugin/`. The connectors are declared inside the plugin, so each assistant starts them itself and setup writes no connector settings.

## Considered options

- **Claude Code's format only, like mattpocock/skills.** Codex reads it, but doesn't expand `${CLAUDE_PLUGIN_ROOT}` (openai/codex#19372), so connectors declared there don't start in Codex. mattpocock ships only skills, so it never hit this.
- **Setup keeps writing each assistant's connector settings.** One file format per assistant, a path repair after every plugin update, and one site per computer.

## Consequences

- A connector declared in the plugin can't hold per-computer paths or per-project values, so it takes the site on every call (ADR 0008).
- Gemini CLI, OpenCode and Devin Desktop can't load connectors from either format yet; each gets its own card when someone needs it.
- Cursor installs plugins only from a Cursor marketplace or a local folder, not from a link.
