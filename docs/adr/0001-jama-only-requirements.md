# Requirements come from Jama only

Buddy-Council reads requirements live from Jama and no longer reads them from an Excel file. The Excel reader only understood Jama's own export layout (title rows on top, requirements grouped under "Folder" rows), so any other spreadsheet read wrong, and offering it cost every setup a "Jama or Excel?" question.

## Considered options

- **Keep Excel as a fallback.** It only ever worked for Jama exports, which the live Jama connection already covers.
- **Read any spreadsheet layout.** We had no real non-Jama sheet to build against, so this meant guessing at formats.

## Consequences

- If Jama can't connect, setup saves everything else and tries Jama again on the next run, as it does for Jira.
- A team that keeps requirements in a spreadsheet gets a new provider, built around their real sheet.
