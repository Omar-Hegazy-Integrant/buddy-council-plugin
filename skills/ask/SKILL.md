---
name: ask
description: Ask a natural-language question about requirements and test cases — routes to the right analysis, answers directly, finds where something you describe is already specified, maps scenarios you already have into TestRail test cases, or edits the test cases you name.
disable-model-invocation: true
---

# /bc:ask — Buddy-Council Natural Language Query

Route a natural language question to the appropriate analysis or answer it directly — find where a behavior
the user describes is already specified, or, when the user brings their own test scenarios, map them into
TestRail, and when they ask to change specific test cases, edit them.

**Plugin root:** two folders above this skill's base directory, which is `<plugin root>/skills/ask/`. Every file these instructions name — `howtos/…`, `providers/…`, `scripts/…`, `com.github.copilot/agents/…` — is a path from the plugin root, and so is every file those files name.

## Arguments

The user's question: the text they typed after the command.

## Intent Classification

Analyze the user's question and classify it into one of these intents:

### Intent: CONTRADICTION
**Trigger phrases**: contradictions, conflicts, inconsistencies, mismatches, "does X contradict Y", "conflicts between", alignment gaps, "mismatch between requirement and test"
**Examples**:
- "Are there contradictions in the Patient Monitoring feature?"
- "Does TC-1234 contradict REQ-85?"
- "Find conflicts between requirements and test cases"
- "What's mismatched in the BGM feature?"

**Action**: Follow the instructions in `com.github.copilot/agents/contradiction-agent.agent.md`, using the user's question to determine scope (extract requirement ID, feature name, or default to "all").

**Asking to fix the test cases that contradict a requirement** ("fix the test cases that contradict CWA-REQ-85") is this intent too: run the analysis for that scope, then treat the request as the user's yes to the agent's fix offer — it still shows every change before writing.

### Intent: COVERAGE
**Trigger phrases**: coverage, untested, orphan, missing tests, gaps, "what's not tested", "which requirements have no tests", "test coverage"
**Examples**:
- "What's the test coverage for login?"
- "Which requirements don't have test cases?"
- "Find orphan test cases"
- "Are there untested requirements in Patient Monitoring?"

**Action**: Follow the instructions in `com.github.copilot/agents/coverage-agent.agent.md`, using the user's question to determine scope.

**Asking for test cases without supplying scenarios** ("create test cases for the untested requirements in Login", "write cases for CWA-REQ-85") is this intent too: the coverage agent finds which requirements truly lack a case, and its gap-fill offer drafts them from the requirement text. Treat the request as the user's yes to that offer — the agent still shows the plan before anything is written. If the requirement turns out to be covered already, say so rather than writing a duplicate.

**Asking to repair orphan test cases** ("fix the orphan test cases in Sync", "link the orphans") is this intent as well: the coverage run finds them, and its repair offer — treated as the user's yes — re-links them through `update-test-cases`, showing the plan first.

### Intent: EDIT (change specific test cases)
**Trigger**: the user asks to change **existing** test cases they name or point at — by id (`C1234`, `TC-1234`) or by a folder or feature. Phrases: "update TC-1234", "change the expected result of C1234 to …", "rename C1234", "set priority High on TC-12 and TC-13", "add CWA-REQ-90 to TC-55", "remove the link to CWA-REQ-12 from C1234", "undo that change"
**Examples**:
- "Change step 2 of C1234 to expect a 3-second refresh"
- "Add CWA-REQ-90 to TC-55's requirements"
- "Set every case in Sync/Offline handling to High priority"

**Action**: Follow `howtos/update-test-cases.md` through its `edits` path, passing the user's request and the cases it names. The how-to reads each case fresh, shows a before → after plan, and writes only after the user confirms. A request to *create* cases is AUTHOR or COVERAGE, not this.

### Intent: AUTHOR (map the user's scenarios into TestRail)
**Trigger**: the user **supplies** scenarios — pasted, a file path, or a Jira key whose description holds them — and wants them as TestRail test cases. Phrases: "map these scenarios to TestRail", "turn these into test cases", "import login.feature into TestRail", "post these scenarios to TestRail", "create TestRail cases from the scenarios in PROJ-123"
**Examples**:
- "Map the scenarios in docs/sync-scenarios.md into TestRail"
- "Turn these into TestRail test cases: 1. Sync retries on network loss 2. Banner clears on reconnect"
- "Create TestRail cases from the scenarios in PROJ-123"
- "Preview how login.feature would map to TestRail"

**Action**: Follow the instructions in `com.github.copilot/agents/scenario-mapping-agent.agent.md`, passing the scenarios (or the file path / Jira key) plus anything the user specified — requirement IDs, a feature, a TestRail folder, a platform, and whether they asked only for a preview.

### Intent: LOOKUP (is it already specified, and where?)
**Trigger**: the user describes a behavior, business rule, or topic **in their own words** — no requirement ID —
and asks whether the requirements already cover it, where it is specified, or which requirements relate to it.
Phrases: "do we have a requirement for …", "is there anything about … in the requirements", "is … already
specified / covered / existing", "which requirements cover / mention / talk about …", "where is … defined",
"does the spec say …", "what do the requirements say about …"
**Examples**:
- "Do we already have a requirement that silences alerts during sensor warm-up?"
- "Where do the requirements talk about Bluetooth permissions?"
- "Which requirements cover what happens when the phone is on battery?"
- "Is exporting a report as PDF already specified?"
- "In the alerts requirements, is snoozing covered?"

**Action**: Follow `howtos/lookup-requirements.md`, passing the ask verbatim and
any feature or area the user named. With Jama, the answer also draws on the rest of the project — system
requirements, risks, labeling, standards, design — as background, which never counts as a requirement in
scope. A question about a requirement the user names by ID, or a request to list or summarize a named
feature, is QA; a question about the test coverage of requirements is COVERAGE.

### Intent: QA (General Question)
**Trigger phrases**: "what does", "tell me about", "which test cases", "list", "summarize", "describe", "how many", "what is", "show me", "explain"
**Examples**:
- "What does CWA-REQ-85 do?"
- "Which test cases cover login?"
- "Summarize the Patient Monitoring requirements"
- "How many test cases are there?"
- "List all requirements in the BGM feature"

**Action**: Handle directly using the QA Execution flow below.

### Ambiguous Intent
If the intent is unclear from the question, ask the user a brief clarifying question. For example:
> I can help with that. Are you looking for:
> 1. **Contradictions** between requirements and test cases
> 2. **Coverage gaps** (untested requirements, orphan tests)
> 3. **General information** about specific requirements or test cases
> 4. **Finding** where something you describe is already specified in the requirements
> 5. **Mapping scenarios** you already have into TestRail test cases
> 6. **Editing** specific test cases

Do not guess — ask.

## QA Execution

When the intent is a general question about requirements or test cases:

1. **Load configuration**: Read `.buddy-council/sources.json`. If missing, direct the user to `/bc:setup`.

2. **Determine the scope** from the question — a requirement ID, a feature name, or "all". Scope narrows each fetch; it never skips one.

3. **Fetch every configured source — always (Data Contract, MANDATORY).** Every answer must be grounded in freshly fetched data. Follow `howtos/fetch-requirements.md` **and** `howtos/fetch-test-cases.md`, both narrowed to the scope. When the config maps a `github_url` field and `requirements.enrichment.enabled` is true, GitHub doc enrichment is a mandatory third source — the fetch-requirements router runs it; include its `Enrichment: fetched K of N` line in the trace. When `jira.board` is configured and `jira.pending` is not `true`, the dev board is a mandatory fourth source — follow `howtos/fetch-board-issues.md`, narrowed to the same scope. Never answer from memory, general knowledge, or previously seen data instead of fetching. Make every attempt visible by printing:

   ```
   Fetch: requirements → <N> fetched
   Fetch: test cases  → <M> fetched
   Fetch: board issues → <K> fetched from board <id>
   ```

   An unconfigured or still-`pending` board prints `Fetch: board issues → skipped (Jira board not configured — run /bc:setup)` and does **not** make the answer PARTIAL; a configured board that errors does.

   If any configured source fails (provider error, MCP unavailable, enrichment failure) — or you could not attempt it — STOP before answering. Name exactly which source could not be fetched and why, then ask the user whether to continue with partial data or abort. Continue only after explicit confirmation, and mark the answer **PARTIAL**, stating which data was missing.

4. **Normalize** by following `howtos/normalize-artifacts.md`.

5. **Answer the question** directly based on the normalized data:
   - Be specific — quote IDs, titles, and exact text
   - For listing questions, use a table or bulleted list
   - For descriptive questions, summarize the key points
   - If the data doesn't contain an answer, say so clearly

## Tool Usage

**CRITICAL**: When fetching data from external systems, always use the available MCP tools. Never use curl, wget, or Bash to call external APIs directly. The provider guides tell you which MCP tools to call.

## Error Handling

- If config is missing → direct user to `/bc:setup`
- If MCP tools are unavailable → tell user to check `.mcp.json` and restart Claude Code
- If no artifacts match the question → report clearly what was searched and that nothing matched
