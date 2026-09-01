#!/bin/bash
# PreToolUse hook for MCP tools: auto-approve the plugin's read-only data
# fetches so the user isn't prompted for calls they would always accept.
#
# Write-capable tools (e.g. jira_create_issue) are intentionally NOT matched
# here and fall through to the normal permission prompt. Tools from other
# servers also fall through untouched.
#
# The Atlassian names below are mcp-atlassian's tools
# (https://github.com/sooperset/mcp-atlassian). They are enumerated rather than
# globbed. A jira_get_*/jira_search* glob would look tempting and is wrong:
# the server exposes ~50 Jira tools plus Confluence ones, several read-shaped
# names sit one edit away from a write cousin, and the set grows every release.
# An allowlist that only ever grows by hand is the point.
#
# Never add: jira_create_issue, jira_update_issue, jira_delete_issue,
# jira_add_comment, jira_edit_comment, jira_transition_issue,
# jira_create_issue_link, jira_remove_issue_link, jira_add_issues_to_sprint,
# jira_create_sprint, jira_update_sprint, jira_move_issue, jira_assign_issue,
# jira_batch_create_issues, or anything else that mutates Jira. /bc:vnv-sprint-prep
# writes to other teams' tickets and depends on those prompts being reached.
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
  mcp__atlassian__jira_get_user_profile | jira_get_user_profile | \
  mcp__atlassian__jira_get_issue | jira_get_issue | \
  mcp__atlassian__jira_search | jira_search | \
  mcp__atlassian__jira_get_agile_boards | jira_get_agile_boards | \
  mcp__atlassian__jira_get_board_issues | jira_get_board_issues | \
  mcp__atlassian__jira_get_sprints_from_board | jira_get_sprints_from_board | \
  mcp__atlassian__jira_get_sprint_issues | jira_get_sprint_issues | \
  mcp__atlassian__jira_get_all_projects | jira_get_all_projects | \
  mcp__atlassian__jira_get_project_issue_types | jira_get_project_issue_types | \
  mcp__atlassian__jira_get_project_fields | jira_get_project_fields | \
  mcp__atlassian__jira_get_create_fields | jira_get_create_fields | \
  mcp__atlassian__jira_search_fields | jira_search_fields | \
  mcp__atlassian__jira_get_transitions | jira_get_transitions | \
  mcp__atlassian__jira_get_link_types | jira_get_link_types | \
  mcp__atlassian__jira_search_assignable_users | jira_search_assignable_users)
    # Both decision shapes: top-level keys for Copilot CLI, the
    # hookSpecificOutput wrapper for Claude Code.
    printf '{"permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch"}}\n'
    ;;
esac

exit 0
