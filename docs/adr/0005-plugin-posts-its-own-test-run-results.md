# The plugin posts its own Test Run results

A Test Project may already post its results to TestRail; both pilot projects can, though it is switched off by default. When `/bc:run-tests` runs Automated Tests, the plugin still posts the results itself, the same way in every Test Project, because only it has the failure explanation and the link to the bug it filed. It posts only after a person approves the batch at the end of the run, and only into a Test Run that person picks, so automated results reach an official V&V Test Run only when someone chooses one.

## Considered options

- **Switch on each project's own reporting.** Every project reports differently, and none of them has the explanation or the bug link.
- **Don't write results back.** TestRail would show the automated cases as untested.

## Consequences

- The TestRail connector gains tools that write results. Like every TestRail write, they always ask first.
- When a project's own TestRail reporting is switched on, the plugin doesn't post a second result for the same Automated Test, and says why.
