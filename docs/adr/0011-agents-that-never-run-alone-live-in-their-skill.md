# An agent that never runs on its own becomes part of its command's skill

`/bc:contradiction`'s steps lived in `com.github.copilot/agents/contradiction-agent.agent.md`. The command and `/bc:ask` both read that file by path and ran its steps in the session; nothing ever started it as a separate agent. As an agent file, though, it loaded into every session: Copilot reads every file in that folder and Claude Code's manifest listed it, so its description sat in each session's list of agents, and a model could start it by itself, skipping the settings step the command runs first. ADR 0006 removed the same cost for how-tos. Now the steps live in `skills/contradiction/SKILL.md`, which loads only when the user types the command, and `/bc:ask` reads that file by path.

## Considered options

- **Keep the agent file.** Its description costs every session, and it invites a model to start it as an agent.
- **Move the steps into a how-to behind a thin skill.** One more file to read on every run, for a command and a route that both need every step.
- **Start it as a real separate agent.** It talks to the user — missing data, the fix offer — and a separate agent can't.

## Consequences

- An agent file holds only steps that run as their own agent, such as the data collector on its low-cost model.
- The other commands' agent files are read by path the same way. Each moves into its command's skill, or becomes a how-to when no command owns it.
- A file that needs a command's steps names that command's skill file by path, as `/bc:ask` does.
