# Commands are skills only the user can start; internal how-tos are plain files

The open plugin format that Copilot, VS Code, Codex and Cursor read (Agent Plugins) carries skills, not commands, so each `/bc:` command becomes a skill that only the user can start, under the same name. The internal skills become plain how-to files that commands read by path: as skills, every description was loaded into each Claude Code and Copilot session in every repository, and Codex and Cursor would list them all in their skill pickers. A plain file costs nothing until a command reads it.

## Consequences

- The model reaches a how-to only through a command's instructions, never on its own. None of them was meant to run alone.
- How-tos live outside `skills/`, because assistants load every skill they find there.
