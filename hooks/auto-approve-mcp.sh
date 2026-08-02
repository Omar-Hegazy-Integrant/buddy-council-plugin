#!/bin/bash
# PreToolUse hook for MCP tools: auto-approve the plugin's read-only data
# fetches so the user isn't prompted for calls they would always accept.
#
# Write-capable tools (e.g. jira_create_issue) are intentionally NOT matched
# here and fall through to the normal permission prompt. Tools from other
# servers also fall through untouched.
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
  mcp__testrail__testrail_get_* | mcp__github__get_file_contents | mcp__jira__jira_get_* | \
  testrail_get_* | get_file_contents | jira_get_*)
    # Both decision shapes: top-level keys for Copilot CLI, the
    # hookSpecificOutput wrapper for Claude Code.
    printf '{"permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch"}}\n'
    ;;
esac

exit 0
