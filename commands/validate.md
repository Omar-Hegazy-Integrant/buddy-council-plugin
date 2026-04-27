# /bc:validate — Validate Ticket Description and Create Jira Ticket

Validate a ticket description against existing requirements, resolve contradictions, fill gaps, and create a Jira ticket.

## Usage

```
/bc:validate "Add login button to checkout page"                    # Validate and create ticket
/bc:validate "Add real-time heart rate monitor" --dry-run          # Validate without creating real ticket
/bc:validate "User can export reports as PDF"                       # Another example
```

## Arguments: $ARGUMENTS

The command expects:
- **Ticket description** (required): Free-form text describing the ticket to create
- **Flags** (optional):
  - `--dry-run`: Test the workflow without creating a real Jira ticket (returns mock ticket key)

## Execution

1. First, verify that `config/sources.json` exists in the plugin directory. If not, tell the user:
   > Configuration not found. Please run `/bc:setup` to configure your data sources first.

2. Check if Jira is configured in `config/sources.json`:
   - If the `jira` section is missing AND `--dry-run` flag is NOT present, warn:
     > Jira is not configured. Run `/bc:setup` to configure Jira, or use `--dry-run` to test without creating a real ticket.
   - If `--dry-run` flag is present, proceed (dry-run works without Jira config)

3. Invoke the ticket validation agent by following the instructions in `agents/ticket-validation-agent.agent.md`, passing the ticket description and any flags.

4. The agent will:
   - Fetch requirements from the configured source (Excel or Jama)
   - Find requirements related to the ticket description (feature matching + keyword matching)
   - Detect contradictions between ticket and requirements
   - **If contradictions found**: Ask user how to resolve (update ticket, keep as-is, or cancel)
   - Detect gaps in the ticket description (missing acceptance criteria, unclear success conditions)
   - **If gaps found**: Ask user questions to fill the gaps
   - Generate a draft Jira ticket with summary, description, and acceptance criteria
   - **Confirm draft**: Ask user to review and approve (or request changes)
   - **If changes requested**: Apply updates and re-validate against requirements
   - Create Jira ticket via MCP (or dry-run mock)
   - Return the ticket key and URL

## Workflow Overview

```
Ticket Description
  ↓
Fetch Requirements
  ↓
Find Related Requirements (feature + keyword matching)
  ↓                           ↓ (if none found)
  | (if found)                | (skip to gap detection)
  ↓                           |
Detect Contradictions         |
  ↓ (if found)                |
Ask User: Update OR keep?     |
  ↓←──────────────────────────┘
Detect Gaps (acceptance criteria, success conditions)
  ↓ (if found)
Ask User: Fill gaps (or skip)
  ↓
Generate Draft (summary + description + acceptance criteria)
  ↓
Confirm Draft (user reviews)
  ↓ (if changes requested)
Apply Changes → Re-validate → Confirm Again
  ↓ (if approved)
Create Jira Ticket (real or dry-run)
  ↓
Return Ticket Key
```

## What Gets Validated

| Validation Type | Description |
|----------------|-------------|
| **Contradictions** | Ticket proposes behavior that conflicts with requirements |
| **Constraint violations** | Ticket violates timing, capacity, security, or other constraints |
| **Feature removal** | Ticket removes functionality that requirements mandate |
| **Scope creep** | Ticket adds functionality to restricted areas |
| **Implementation conflicts** | Ticket uses incompatible technical approach |
| **Missing acceptance criteria** | Ticket lacks testable "done" conditions |
| **Unclear success conditions** | Ticket has ambiguous or unmeasurable outcomes |

## Dry-Run Mode

Use `--dry-run` to test the entire workflow without creating a real Jira ticket:
- All validation steps execute normally (fetch requirements, detect contradictions, fill gaps)
- Draft is generated and reviewed
- Draft is saved to a markdown file: `ticket-draft-[timestamp].md`
- Instead of creating a real ticket, returns a mock ticket key: `DRY-RUN-XXXXXXXX`

This is useful for:
- Testing the validation workflow before creating real tickets
- Practicing with the tool without cluttering your Jira board
- Validating ticket descriptions when Jira is not yet configured
- Saving ticket drafts for later review or batch creation

## Example Session

**Command:**
```
/bc:validate "Add a real-time heart rate monitor to the patient vitals dashboard"
```

**Agent finds related requirements:**
- CWA-REQ-85: "Patient monitoring view displays vital signs with refresh rate <= 3 seconds"
- CWA-REQ-120: "All patient data must be encrypted at rest and in transit"

**Agent detects gaps:**
- Missing acceptance criteria (what "done" looks like)
- Unclear refresh rate specification

**Agent asks questions:**
1. "What specific vitals should the monitor display?"
2. "What refresh rate should be used?"
3. "What should happen if the device disconnects?"

**User answers questions → Agent generates draft:**
```
Summary: Add real-time heart rate monitor to vitals dashboard

Description:
## Solution
Add a real-time heart rate monitor widget to the patient vitals dashboard...

## Related Requirements
- CWA-REQ-85: Patient monitoring view displays vital signs with refresh rate <= 3 seconds

## Acceptance Criteria
- Widget displays current heart rate in BPM
- Widget updates every 2 seconds when device is connected
- Widget shows connection status (connecting, connected, disconnected)
...
```

**User reviews and approves → Agent creates ticket:**
```
Ticket created successfully! PROJ-1234
https://yourorg.atlassian.net/browse/PROJ-1234
```

## Error Handling

- **No ticket description provided**: Prompt user for description
- **No related requirements found**: Automatically skip contradiction detection and proceed to gap analysis
- **Jira not configured**: Suggest `/bc:setup` or `--dry-run` mode
- **MCP tools unavailable**: Check `.mcp.json` and restart Claude Code
- **Ticket creation fails**: Check Jira credentials and project key

## Tips

- **Start with dry-run**: Use `--dry-run` to test before creating real tickets
- **Be specific**: More detailed descriptions lead to better requirement matching
- **Reference features**: Mention feature names explicitly (e.g., "in the Patient Monitoring view")
- **Review the draft**: Always review the generated draft before creating the ticket
- **Iterate**: If the draft isn't quite right, request changes and the agent will re-validate
