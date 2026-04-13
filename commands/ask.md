# /bc:ask — Buddy-Council Natural Language Query

Route a natural language question to the appropriate analysis or answer it directly.

## Arguments: $ARGUMENTS

## Intent Classification

Analyze the user's question and classify it into one of these intents:

### Intent: CONTRADICTION
**Trigger phrases**: contradictions, conflicts, inconsistencies, mismatches, "does X contradict Y", "conflicts between", alignment gaps, "mismatch between requirement and test"
**Examples**:
- "Are there contradictions in the Patient Monitoring feature?"
- "Does TC-1234 contradict REQ-85?"
- "Find conflicts between requirements and test cases"
- "What's mismatched in the BGM feature?"

**Action**: Follow the instructions in `${CLAUDE_PLUGIN_ROOT}/agents/contradiction-agent.md`, using the user's question to determine scope (extract requirement ID, feature name, or default to "all").

### Intent: COVERAGE
**Trigger phrases**: coverage, untested, orphan, missing tests, gaps, "what's not tested", "which requirements have no tests", "test coverage"
**Examples**:
- "What's the test coverage for login?"
- "Which requirements don't have test cases?"
- "Find orphan test cases"
- "Are there untested requirements in Patient Monitoring?"

**Action**: Follow the instructions in `${CLAUDE_PLUGIN_ROOT}/agents/coverage-agent.md`, using the user's question to determine scope.

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

Do not guess — ask.

## QA Execution

When the intent is a general question about requirements or test cases:

1. **Load configuration**: Read `${CLAUDE_PLUGIN_ROOT}/config/sources.json`. If missing, direct the user to `/bc:setup`.

2. **Determine what data is needed** from the question:
   - Asking about a specific requirement → fetch that requirement and its linked test cases
   - Asking about test cases for a feature → fetch test cases filtered to that feature
   - Asking a counting/listing question → fetch all relevant artifacts
   - Asking about a specific test case → fetch that test case and its linked requirements

3. **Fetch data** by following `${CLAUDE_PLUGIN_ROOT}/skills/fetch-requirements/SKILL.md` and/or `${CLAUDE_PLUGIN_ROOT}/skills/fetch-test-cases/SKILL.md` as needed. Only fetch what the question requires — do not fetch everything for a narrow question.

4. **Normalize** by following `${CLAUDE_PLUGIN_ROOT}/skills/normalize-artifacts/SKILL.md`.

5. **Answer the question** directly based on the normalized data:
   - Be specific — quote IDs, titles, and exact text
   - For listing questions, use a table or bulleted list
   - For descriptive questions, summarize the key points
   - If the data doesn't contain an answer, say so clearly

## Tool Usage

**CRITICAL**: When fetching data from external systems, always use the available MCP tools. Never use curl, wget, or Bash to call external APIs directly. The provider skills will tell you which MCP tools to call.

## Error Handling

- If config is missing → direct user to `/bc:setup`
- If MCP tools are unavailable → tell user to check `.mcp.json` and restart Claude Code
- If no artifacts match the question → report clearly what was searched and that nothing matched
