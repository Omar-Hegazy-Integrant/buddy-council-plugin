#!/bin/bash
# PreToolUse hook for MCP tools: auto-approve the plugin's read-only data
# fetches so the user isn't prompted for calls they would always accept.
#
# Write-capable tools (jira_create_issue, jira_update_issue, jira_add_comment,
# jira_create_issue_link, jira_transition_issue, jira_add_issues_to_sprint) are
# intentionally NOT matched here and fall through to the normal permission
# prompt. Tools from other servers also fall through untouched.
#
# The Jira names below belong to the Dockerized sooperset/mcp-atlassian server
# (ghcr.io/sooperset/mcp-atlassian). They are enumerated rather than globbed on
# purpose: a jira_get_*/jira_search_* glob happens to be read-only against
# today's tool surface, but it would silently auto-approve any future tool that
# matched the pattern, and this server also exposes Confluence tools. Adding a
# name here is a deliberate act; inheriting one is not.
#
# Runs under BOTH runtimes. Claude Code names MCP tools mcp__<server>__<tool>
# and routes only those here (hooks/hooks.json matcher mcp__.*). Copilot CLI
# runs this for every preToolUse (root hooks.json, no matcher) and uses its
# own MCP tool naming, so the case below matches both the Claude-prefixed and
# bare tool names — anything else exits silently. Never exits non-zero:
# Copilot treats a failing preToolUse hook as deny (fail-closed).

INPUT=$(cat)

TOOL=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('tool_name') or d.get('toolName') or '')
except Exception:
    print('')
" 2>/dev/null)

case "$TOOL" in
  mcp__testrail__testrail_get_* | mcp__github__get_file_contents | \
  testrail_get_* | get_file_contents | \
  mcp__atlassian__jira_get_issue | jira_get_issue | \
  mcp__atlassian__jira_search | jira_search | \
  mcp__atlassian__jira_get_all_projects | jira_get_all_projects | \
  mcp__atlassian__jira_get_project_issue_types | jira_get_project_issue_types | \
  mcp__atlassian__jira_get_project_fields | jira_get_project_fields | \
  mcp__atlassian__jira_get_create_fields | jira_get_create_fields | \
  mcp__atlassian__jira_search_fields | jira_search_fields | \
  mcp__atlassian__jira_get_user_profile | jira_get_user_profile | \
  mcp__atlassian__jira_search_assignable_users | jira_search_assignable_users | \
  mcp__atlassian__jira_get_transitions | jira_get_transitions | \
  mcp__atlassian__jira_get_link_types | jira_get_link_types | \
  mcp__atlassian__jira_get_agile_boards | jira_get_agile_boards | \
  mcp__atlassian__jira_get_board_issues | jira_get_board_issues | \
  mcp__atlassian__jira_get_sprints_from_board | jira_get_sprints_from_board | \
  mcp__atlassian__jira_get_sprint_issues | jira_get_sprint_issues)
    # Both decision shapes: top-level keys for Copilot CLI, the
    # hookSpecificOutput wrapper for Claude Code.
    printf '{"permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch"}}\n'
    ;;
esac

exit 0
