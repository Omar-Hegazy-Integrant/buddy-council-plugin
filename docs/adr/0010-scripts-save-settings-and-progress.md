# Scripts save settings and progress, so no hook approves a file write

`/bc:setup` and the onboarding and V&V workflows used to write `.buddy-council/` files with the assistant's file tool, and a hook (`auto-approve-plugin-writes.sh`) approved those writes by path so they didn't prompt. The V&V log was never on its list and prompted every time, nothing checked what was written, and a hook that approves file writes by path is one pattern away from approving a key. Now `scripts/bc_setup.py` saves a project's settings, after checking them against `config/sources.example.json`, and `scripts/bc_run.py` saves the progress logs. The bash hook approves each as one plain command, and no hook approves a file write.

## Considered options

- **Keep the write hook and add the V&V log to it.** Still no check of what gets saved, and the assistant rewrites the whole file every time.
- **Pass the data through stdin, a pipe, or a heredoc.** The bash hook approves a command only when nothing can be chained onto it, and it can't see what a pipe or heredoc carries.

## Consequences

- Structured data travels as one single-quoted JSON argument on one line, with every `'` written as `\u0027`. A save sends only what changed, and the script merges it.
- A key is the one file the assistant still writes with its own tool, and the assistant's prompt before that write is the approval.
