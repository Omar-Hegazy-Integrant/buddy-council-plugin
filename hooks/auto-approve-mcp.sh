#!/bin/bash
# PreToolUse hook for MCP tools: auto-approve the plugin's read-only data
# fetches so the user isn't prompted for calls they would always accept.
#
# Write-capable tools (e.g. mcp__jira__jira_create_issue) are intentionally NOT
# matched here and fall through to the normal permission prompt. Tools from
# other servers also fall through untouched.
#
# Hook protocol: stdout JSON with permissionDecision = decision; exit 0 with no
# JSON = defer to the normal permission flow.

INPUT=$(cat)

TOOL=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('tool_name', ''))
except Exception:
    print('')
" 2>/dev/null)

case "$TOOL" in
  mcp__testrail__testrail_get_* | mcp__github__get_file_contents | mcp__jira__jira_get_*)
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch"}}\n'
    ;;
esac

exit 0
