# Only requirements are required; the other tools can be "none"

Commit 453c8b2 (August 2026) made the Jira dev board required, because while it was optional most projects skipped it and analysis never saw the board. Now that each project folder picks its own tools (ADR 0008), some projects really have no Jira or no TestRail, so only requirements stay required. Setup accepts "none" for test cases and the board, and saves that choice so it never asks again.

## Consequences

- The reason Jira was required stays in sight: every report names the tools the project doesn't use, and how to add one (`/bc:setup <link>`).
- A command that can't run without a tool asks for its link on the spot and carries on, so changing your mind doesn't need a trip through setup.
