# Setup finishes in one run, with no restart

Setup used to register the connectors, and an assistant loads connectors only when it starts, so a first `/bc:setup` couldn't pick TestRail's case defaults, choose which part of Jama to read, or check the Jira board, and every new user ran setup twice with a restart in between. The connectors now ship inside the plugin (ADR 0007) and read a site's key when a call needs it (ADR 0008), so they're already running when setup starts and use each key the moment setup saves it.

## The one exception

The Plugin Tools. The plugin's `appium` connector starts appium-mcp when the assistant starts — until
`/bc:setup` first installs it in a test project, a stub with no tools that says its list never changes
(`listChanged: false`). So the assistant is restarted once after setup installs those tools, or moves them to a new
pinned version; setup's automation lines say when. Nothing else ever needs a restart — no analysis setup, and no new
key.

## Considered options

- **Setup starts the connectors itself, read-only, for its first run.** This was the first answer, and it's no longer needed once the plugin declares the connectors.
