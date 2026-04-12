#!/bin/bash
# PreToolUse hook for Bash: allow only safe commands used by the plugin.
# Reads tool input JSON from stdin, inspects the command field.
# Exit 0 = allow, Exit 2 = block.

set -e

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('tool_input', {}).get('command', ''))
" 2>/dev/null)

# Allow list: patterns the plugin legitimately needs
# 1. Python commands (Excel parsing, JSON processing)
if echo "$COMMAND" | grep -qE '^python3?\s'; then
  exit 0
fi

# 2. chmod on secrets file
if echo "$COMMAND" | grep -qE '^chmod\s+600\s+.*secrets'; then
  exit 0
fi

# 3. curl to TestRail only (setup connection test fallback)
if echo "$COMMAND" | grep -qE '^curl\s.*testrail\.io'; then
  exit 0
fi

# 4. pip/uv install for dependencies
if echo "$COMMAND" | grep -qE '^(pip|uv)\s+install'; then
  exit 0
fi

# Block everything else
echo "[Hook] BLOCKED: Bash command not in plugin allowlist." >&2
echo "[Hook] Allowed: python3, chmod on secrets, curl to testrail, pip/uv install" >&2
exit 2
