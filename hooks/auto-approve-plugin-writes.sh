#!/bin/bash
# PreToolUse hook for Write|Edit: auto-approve writes to the plugin's OWN
# generated files, so /bc:setup and /bc:onboarding don't prompt for every
# config/progress save the user already asked for.
#
# Scope is deliberately narrow — only these exact files are approved:
#   */.buddy-council/sources.json              (per-project source config)
#   */.buddy-council/onboarding-progress.json  (onboarding progress log)
#   $HOME/.buddy-council/secrets.json          (credentials; chmod 600 follows)
#   <this plugin's install dir>/.mcp.json      (MCP server config)
# Any other path — including .mcp.json files of OTHER projects — falls through
# to the normal permission prompt.
#
# Hook protocol: stdout JSON with permissionDecision = decision; exit 0 with no
# JSON = defer to the normal permission flow.

INPUT=$(cat)

FILE=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('tool_input', {}).get('file_path', ''))
except Exception:
    print('')
" 2>/dev/null)

[ -z "$FILE" ] && exit 0

allow() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"%s"}}\n' "$1"
  exit 0
}

case "$FILE" in
  */.buddy-council/sources.json)
    allow "bc: plugin source config" ;;
  */.buddy-council/onboarding-progress.json)
    allow "bc: onboarding progress log" ;;
  "$HOME/.buddy-council/secrets.json")
    allow "bc: plugin secrets file" ;;
esac

# .mcp.json is security-relevant (it defines which MCP servers run), so only
# the copy inside THIS plugin's own install directory is approved.
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"
if [ -n "$PLUGIN_ROOT" ] && [ "$FILE" = "$PLUGIN_ROOT/.mcp.json" ]; then
  allow "bc: plugin MCP config"
fi

exit 0
