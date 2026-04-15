# /bc:coverage — Analyze Test Coverage Gaps

Find untested requirements, orphan test cases, and weak coverage between requirements and test cases.

## Usage

```
/bc:coverage                        # Analyze all requirements and test cases
/bc:coverage CWA-REQ-85             # Check coverage for a specific requirement
/bc:coverage "Patient Monitoring"   # Check coverage for a specific feature
```

## Arguments: $ARGUMENTS

## Execution

1. First, verify that `config/sources.json` exists in the plugin directory. If not, tell the user:
   > Configuration not found. Please run `/bc:setup` to configure your data sources first.

2. Invoke the coverage agent by following the instructions in `agents/coverage-agent.agent.md`, passing any arguments as the analysis scope.

3. The agent will:
   - Fetch requirements from the configured source (Excel or Jama)
   - Fetch test cases from TestRail
   - Normalize and cross-link all artifacts
   - Analyze coverage gaps
   - Return a coverage report with metrics and recommendations

## What Gets Analyzed

| Finding Type | Severity | Description |
|-------------|----------|-------------|
| Untested requirement (critical) | HIGH | Safety-critical requirement with no test cases |
| Untested requirement (other) | MEDIUM | Non-critical requirement with no test cases |
| Orphan test case | MEDIUM | Test case with no linked requirement |
| Weak coverage | LOW | Test exists but doesn't fully exercise the requirement |
