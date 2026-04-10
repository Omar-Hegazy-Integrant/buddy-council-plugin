# /bc:contradiction — Detect Contradictions Between Requirements and Test Cases

Analyze requirements and test cases to find contradictions, inconsistencies, and alignment gaps.

## Usage

```
/bc:contradiction                       # Analyze all requirements and test cases
/bc:contradiction CWA-REQ-85            # Analyze a specific requirement
/bc:contradiction "Patient Monitoring"  # Analyze a specific feature
```

## Arguments: $ARGUMENTS

## Execution

1. First, verify that `config/sources.json` exists in the plugin directory. If not, tell the user:
   > Configuration not found. Please run `/bc:setup` to configure your data sources first.

2. Invoke the contradiction agent by following the instructions in `agents/contradiction-agent.md`, passing any arguments as the analysis scope.

3. The agent will:
   - Fetch requirements from the configured source (Excel or Jama)
   - Fetch test cases from TestRail
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
