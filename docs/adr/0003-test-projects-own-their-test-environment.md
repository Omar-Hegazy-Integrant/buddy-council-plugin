# Test Projects own their Test Environment

Each Test Project keeps its own Test Environment working through its own Setup Script. The plugin runs that script, reports what it found, and hands back anything only a person can do. The only things the plugin installs are its Plugin Tools. We chose this because the hard parts of a Test Environment are things only the team knows: private build servers and their tokens, and services shared with other projects on the same Mac. A script the plugin wrote by reading the code would look complete but miss exactly those parts, and teams that never use the plugin still need a Setup Script.

Two rules keep the plugin independent:

1. Nothing about any one Test Project or company is built into the plugin. It learns what it needs by reading the project: how a test runs, how tests are written, and how an Automated Test is tied to its Test Case. It checks what it learned by running one existing Automated Test, then saves it in the Test Project's `.buddy-council/` folder.
2. Every Test Project works fully without the plugin.

Each project folder is set up on its own, and nothing links an app's code to its Test Project. The plugin and its Plugin Tools are installed once per Mac.

## Considered options

- **The plugin writes a Setup Script for each project.** It can't see what only the team knows, so the script would miss the hard parts.
- **The plugin becomes the shared setup for all of a company's Test Projects.** That would tie the plugin to one company.

## Consequences

- The plugin runs a Setup Script only when the script keeps five promises: it is in one of a few usual places, has a `--check` mode that changes nothing, is safe to run again, never waits for an answer when there is no terminal, and exits with 0 when the Mac is ready. A setup command that doesn't keep them goes in the Recap for a person to run, because a command never meant to be run by a tool can change services that other projects on the same Mac rely on.
- In every Test Project, one existing Automated Test, run the project's own way, proves that the Test Environment is ready and that the plugin learned the project correctly. It is the sample test the project's docs name, or else the project's shortest test, and its result is never posted. A Test Project without a Setup Script isn't given one: this test is its check.
- Setup shows two readiness lines. Running tests is ready once that existing Automated Test passes. Automating is ready once running tests is, and the Plugin Tools have also read one screen from the phone, so a failed download of the Plugin Tools holds up only automating.
- Setting up a Test Project asks for the same sources as any other project folder, requirements included, because Automated Tests are written against requirements. Keys belong to sites ([ADR 0008](./0008-keys-per-site.md)), so a Test Project on sites this Mac already knows asks for no new keys.
- The automation setup still in progress drops its two parts written for the first pilot: reading that project's tool-version file, and searching its setup scripts for its phone setting. The plugin learns both instead.
- Projects with no Automated Tests are left for a later phase. Whatever it adds to such a project must run without the plugin: a standard framework, or one of our own published as a separate package.
