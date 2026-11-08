---
name: contradiction
description: Detect contradictions, inconsistencies, and alignment gaps between requirements and test cases.
disable-model-invocation: true
---

# /bc:contradiction — Detect Contradictions Between Requirements and Test Cases

Analyze requirements and test cases to find contradictions, inconsistencies, and alignment gaps.

**Plugin root:** two folders above this skill's base directory, which is `<plugin root>/skills/contradiction/`. Every file these instructions name — `howtos/…`, `providers/…`, `scripts/…`, `com.github.copilot/agents/…` — is a path from the plugin root, and so is every file those files name.

## Usage

```
/bc:contradiction                       # Analyze all requirements and test cases
/bc:contradiction CWA-REQ-85            # Analyze a specific requirement
/bc:contradiction "Patient Monitoring"  # Analyze a specific feature
```

## Arguments

The text the user typed after the command: a requirement ID, a feature name, or nothing for everything.

## Execution

1. First, verify that `.buddy-council/sources.json` exists. If not, tell the user:
   > Configuration not found. Please run `/bc:setup` to configure your data sources first.

2. Run the contradiction agent's steps in this session: follow `com.github.copilot/agents/contradiction-agent.agent.md`, passing any arguments as the analysis scope.

3. The agent will:
   - Fetch requirements from Jama
   - Fetch test cases from TestRail
   - Fetch the in-flight issues on the configured Jira dev board, and check those against the requirements too — so a sprint ticket that contradicts a requirement is caught before it ships. Skipped with a visible line if the board isn't configured or isn't verified yet
   - Normalize and cross-link all artifacts
   - Analyze for 7 types of contradictions
   - Return a human-readable report with severity-classified findings

## What Gets Analyzed

| Contradiction Type | Description |
|-------------------|-------------|
| Direct conflicts | Requirement says X, test validates opposite |
| Behavioral conflicts | Different expected behaviors for same trigger |
| Scope overlaps | Overlapping requirements with incompatible constraints |
| Test vs requirement | Test steps contradict linked requirement |
| Cross-feature tensions | Requirements in different features that conflict |
| Missing alignment | Untested requirements or orphan test cases |
| Temporal/state conflicts | Conflicting state or timing expectations |
