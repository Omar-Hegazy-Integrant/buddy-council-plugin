# /bc:ask — Buddy-Council General Query (Future Orchestration)

Route a natural language question to the appropriate Buddy-Council agent.

## Arguments: $ARGUMENTS

## Current Status

The orchestration layer is not yet implemented. Currently available agents:

- `/bc:contradiction` — Detect contradictions between requirements and test cases

## Routing (Future)

When fully implemented, this command will:
1. Analyze the user's intent from their question
2. Route to the appropriate agent:
   - Contradiction-related → contradiction agent
   - Coverage-related → coverage agent (future)
   - General Q&A → QA agent (future)
3. Return the agent's response

## For Now

If the user's question is about contradictions or conflicts between requirements and test cases, suggest they use `/bc:contradiction` directly.

Otherwise, acknowledge that additional agents are planned and explain what's currently available.
