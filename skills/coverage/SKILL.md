---
name: coverage
description: Find untested requirements, orphan test cases, and coverage gaps.
disable-model-invocation: true
---

# /bc:coverage — Analyze Test Coverage Gaps

Find untested requirements, orphan test cases, and weak coverage between requirements and test cases.

**Plugin root:** two folders above this skill's base directory, which is `<plugin root>/skills/coverage/`. Every file these instructions name — `howtos/…`, `providers/…`, `scripts/…`, `com.github.copilot/agents/…` — is a path from the plugin root, and so is every file those files name.

## Usage

```
/bc:coverage                        # Analyze all requirements and test cases
/bc:coverage CWA-REQ-85             # Check coverage for a specific requirement
/bc:coverage "Patient Monitoring"   # Check coverage for a specific feature
```

## Arguments

The text the user typed after the command: a requirement ID, a feature name, or nothing for everything.

## Execution

1. First, verify that `.buddy-council/sources.json` exists. If not, tell the user:
   > Configuration not found. Please run `/bc:setup` to configure your data sources first.

2. Run the coverage agent's steps in this session: follow `com.github.copilot/agents/coverage-agent.agent.md`, passing any arguments as the analysis scope.

3. The agent will:
   - Fetch requirements from Jama
   - Fetch test cases from TestRail
   - Fetch the in-flight issues on the configured Jira dev board, and add a **Delivery risk** section: work shipping now whose requirement has no test case, plus board issues that match no requirement at all. Skipped with a visible line if the board isn't configured or isn't verified yet
   - Normalize and cross-link all artifacts
   - Analyze coverage gaps
   - Return a coverage report with metrics and recommendations
   - **Offer to close the gaps.** When the run finds untested requirements and `test_cases.authoring` is configured, the report ends with a one-line offer to draft skeleton TestRail cases for them, linked to their requirement IDs. There is no flag — say yes, pick a subset, or ignore it. Nothing is written without an explicit yes, and a plain run stays read-only exactly as before

## What Gets Analyzed

| Finding Type | Severity | Description |
|-------------|----------|-------------|
| Untested requirement (critical) | HIGH | Safety-critical requirement with no test cases |
| Untested requirement (other) | MEDIUM | Non-critical requirement with no test cases |
| Orphan test case | MEDIUM | Test case with no linked requirement |
| Weak coverage | LOW | Test exists but doesn't fully exercise the requirement |
