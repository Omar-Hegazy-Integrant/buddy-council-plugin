# Keys belong to sites, and every connector call names its site

Each project folder can use its own Jama, TestRail and Jira sites, but the connectors ship once inside the plugin (ADR 0007), so they can't be configured per project. Instead, every connector call names the site it's for, and the connector uses the key saved for that site: one file per site in `~/.buddy-council/keys/`. A key is only ever sent to the site it was saved for.

## Considered options

- **One site per computer** (the design before this): setting up a second project on another site silently broke the first.
- **Per-project connector settings in each assistant's own files**: a different file and format per assistant, written into the user's repository.

## Consequences

- mcp-atlassian talks to one Jira site per process, so the plugin's Jira connector runs one copy per site behind it. The Jira token joins the other keys, and `atlassian.env` goes.
- Adding a key never shows the AI the other keys, and the assistant asks once per new key before it's saved.
- One key per site: two accounts on the same site aren't supported.
- Connectors read a site's key when a call needs it, so a key works the moment setup saves it.
