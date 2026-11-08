#!/bin/bash
# PreToolUse hook for Write|Edit: auto-approve writes to the plugin's OWN
# generated files, so /bc:setup and /bc:onboarding don't prompt for every
# config/progress save the user already asked for.
#
# Scope is deliberately narrow — only these exact files are approved:
#   */.buddy-council/sources.json              (per-project source config)
#   */.buddy-council/onboarding-progress.json  (onboarding progress log)
# Any other path falls through to the normal permission prompt — every key in
# ~/.buddy-council/keys/ above all, so saving a key always asks first, and every
# assistant's MCP settings: the connectors ship inside the plugin, so nothing of
# the plugin's ever writes one, and a silent write there could add a server.
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
esac

exit 0
