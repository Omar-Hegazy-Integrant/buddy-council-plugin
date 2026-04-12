#!/bin/bash
# PreToolUse hook for Bash: allow only safe commands used by the plugin.
# Reads tool input JSON from stdin, inspects the command field.
# Exit 0 = allow, Exit 1 = warn + ask user, Exit 2 = hard block.

set -e

INPUT=$(cat)

# Extract the command and strip leading whitespace to get the actual executable
COMMAND=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
cmd = data.get('tool_input', {}).get('command', '').strip()
print(cmd)
" 2>/dev/null)

# Allow list: patterns the plugin legitimately needs

# 1. Python commands (Excel parsing, JSON processing)
if echo "$COMMAND" | head -1 | grep -qE '^\s*python3?\s'; then
  exit 0
fi

# 2. chmod on secrets file
if echo "$COMMAND" | head -1 | grep -qE '^\s*chmod\s+600\s+.*secrets'; then
  exit 0
fi

# 3. curl to TestRail only (setup connection test fallback)
if echo "$COMMAND" | head -1 | grep -qE '^\s*curl\s.*testrail\.io'; then
  exit 0
fi

# 4. pip/uv install for dependencies
if echo "$COMMAND" | head -1 | grep -qE '^\s*(pip|uv)\s+install'; then
  exit 0
fi

# Not in allowlist — warn and ask for user approval
echo "[bc plugin] This Bash command is not in the auto-approved list." >&2
echo "" >&2
echo "  Auto-approved: python3, chmod secrets, curl testrail, pip/uv install" >&2
echo "  Command: $(echo "$COMMAND" | head -1 | head -c 120)" >&2
echo "" >&2
echo "  You can approve this manually if it looks safe." >&2
exit 1
