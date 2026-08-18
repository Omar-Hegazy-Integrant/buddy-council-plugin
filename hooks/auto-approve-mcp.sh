#!/bin/bash
# PreToolUse hook for MCP tools: auto-approve the plugin's read-only data
# fetches so the user isn't prompted for calls they would always accept.
#
# Write-capable tools (e.g. Atlassian's createJiraIssue) are intentionally NOT
# matched here and fall through to the normal permission prompt. Tools from
# other servers also fall through untouched.
#
# The Atlassian names below are the official remote MCP server's tools
# (https://mcp.atlassian.com). They are enumerated rather than globbed: the
# server also exposes Confluence/Bitbucket/Compass tools, and at least one
# (bitbucketPullRequest) is both a read and a write tool, so a get*-style glob
# would be unsafe.
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
  mcp__atlassian__atlassianUserInfo | atlassianUserInfo | \
  mcp__atlassian__getAccessibleAtlassianResources | getAccessibleAtlassianResources | \
  mcp__atlassian__getVisibleJiraProjects | getVisibleJiraProjects | \
  mcp__atlassian__getJiraProjectIssueTypesMetadata | getJiraProjectIssueTypesMetadata | \
  mcp__atlassian__getJiraIssueTypeMetaWithFields | getJiraIssueTypeMetaWithFields | \
  mcp__atlassian__getJiraIssue | getJiraIssue | \
  mcp__atlassian__getJiraIssueRemoteIssueLinks | getJiraIssueRemoteIssueLinks | \
  mcp__atlassian__getIssueLinkTypes | getIssueLinkTypes | \
  mcp__atlassian__getTransitionsForJiraIssue | getTransitionsForJiraIssue | \
  mcp__atlassian__lookupJiraAccountId | lookupJiraAccountId | \
  mcp__atlassian__searchJiraIssuesUsingJql | searchJiraIssuesUsingJql)
    # Both decision shapes: top-level keys for Copilot CLI, the
    # hookSpecificOutput wrapper for Claude Code.
    printf '{"permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"bc: read-only MCP fetch"}}\n'
    ;;
esac

exit 0
