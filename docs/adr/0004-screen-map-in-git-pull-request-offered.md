# The Screen Map is shared in git, and the plugin offers the pull request

Walking an app's screens produces a Screen Map and Captures. The Screen Map is committed to the Test Project in a folder of its own, so a team walks each screen once and reviews changes to it in the same pull requests as the Automated Tests that use it. Captures stay in `.buddy-council/` on each Mac: they are large, they change with every build of the app, and they can hold whatever data the app was showing. Until now nothing the plugin wrote reached a team's repository. The Screen Map is a deliberate exception, because walking every screen on a phone is too slow for each teammate to repeat.

The plugin writes Automated Tests, page objects and Screen Map changes into the working copy, and a person reviews them. Once that person approves them, the plugin offers to open a pull request with a description only it can write: which Test Cases the tests automate, the requirements behind those cases, and proof that each test passed on a phone. On a yes, it commits only the files it wrote, and follows the repository's own rules and the person's own instructions for branch names, templates and tickets. It never pushes or opens a pull request without that yes.

## Considered options

- **Keep the Screen Map on each Mac.** Everyone who automates would walk the app again.
- **Commit the Captures too.** They would bloat the repository, go stale with every build, and could put the app's data in git.
- **Leave pull requests to the person.** Reviewers would lose the description that ties each test to its Test Case and requirements.
- **Open pull requests the plugin's own way.** That would build one team's branch and ticket rules into the plugin, against rule 1 of [Test Projects own their Test Environment](./0003-test-projects-own-their-test-environment.md).

## Consequences

- Automated Tests never read the Screen Map. They find elements through the project's own page objects, so the project still runs without the plugin (rule 2), and deleting the Screen Map loses only what the plugin learned.
- The Screen Map's folder sits outside `.buddy-council/`, which stays out of git.
- Only the Screen Map is shared. What the plugin learns about how a Test Project's tests run and how they're written stays in `.buddy-council/` on each Mac: learning it takes minutes, and each Mac runs one existing Automated Test anyway. When the plugin learns something wrong, the fix goes into the project's own docs, where people read it too.
