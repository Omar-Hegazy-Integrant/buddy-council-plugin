#!/bin/bash
# PreToolUse hook for Write|Edit: auto-approve writes to the plugin's OWN
# generated files, so /bc:setup and /bc:onboarding don't prompt for every
# config/progress save the user already asked for.
#
# Scope is deliberately narrow — only these exact files are approved:
#   */.buddy-council/sources.json              (per-project source config)
#   */.buddy-council/onboarding-progress.json  (onboarding progress log)
#   $HOME/.buddy-council/secrets.json          (credentials; chmod 600 follows)
#   <this plugin's install dir>/.mcp.json      (MCP server config, Claude Code)
#   $HOME/.copilot/mcp-config.json             (MCP server config, Copilot CLI)
# Any other path — including .mcp.json files of OTHER projects — falls through
# to the normal permission prompt.
#
# Runs under BOTH runtimes: Claude Code (Write/Edit tools, tool_input.file_path)
# and Copilot CLI (create/edit tools, toolArgs JSON string with a `path` field).
# Emits both decision shapes; exit 0 with no JSON = defer to the normal prompt.

INPUT=$(cat)

FILE=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input')
    if ti is None:
        ta = d.get('toolArgs')
        ti = json.loads(ta) if isinstance(ta, str) else (ta or {})
    print(ti.get('file_path') or ti.get('path') or '')
except Exception:
    print('')
" 2>/dev/null)

[ -z "$FILE" ] && exit 0

# Emits BOTH decision shapes: top-level keys for Copilot CLI, the
# hookSpecificOutput wrapper for Claude Code.
allow() {
  printf '{"permissionDecision":"allow","permissionDecisionReason":"%s","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"%s"}}\n' "$1" "$1"
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

# MCP config is security-relevant (it defines which servers run), so only the
# two exact files the plugin owns are approved: the .mcp.json inside THIS
# plugin's install directory (Claude Code) and the user-level Copilot config.
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"
if [ -n "$PLUGIN_ROOT" ] && [ "$FILE" = "$PLUGIN_ROOT/.mcp.json" ]; then
  allow "bc: plugin MCP config"
fi
if [ "$FILE" = "$HOME/.copilot/mcp-config.json" ]; then
  allow "bc: Copilot MCP config"
fi

exit 0
