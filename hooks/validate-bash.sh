#!/bin/bash
# PreToolUse hook for Bash: block dangerous commands, allow everything else.
# This plugin is read-only (fetching data + generating reports), so most
# commands are safe. We only hard-block destructive operations.
#
# Exit 0 = allow, Exit 1 = warn + ask user, Exit 2 = hard block.

set -e

INPUT=$(cat)

# Extract the command and strip leading whitespace
COMMAND=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
cmd = data.get('tool_input', {}).get('command', '').strip()
print(cmd)
" 2>/dev/null)

FIRST_LINE=$(echo "$COMMAND" | head -1)

# === HARD BLOCK: destructive operations ===

# rm -rf or rm with force/recursive flags
if echo "$FIRST_LINE" | grep -qE '^\s*rm\s+(-[rRf]+|--force|--recursive)'; then
  echo "[bc plugin] BLOCKED: Destructive rm command." >&2
  exit 2
fi

# Overwriting system files
if echo "$FIRST_LINE" | grep -qE '>\s*/etc/|>\s*/usr/|>\s*/var/'; then
  echo "[bc plugin] BLOCKED: Writing to system directories." >&2
  exit 2
fi

# Kill/pkill/killall
if echo "$FIRST_LINE" | grep -qE '^\s*(kill|pkill|killall)\s'; then
  echo "[bc plugin] BLOCKED: Process termination commands." >&2
  exit 2
fi

# Git push --force or reset --hard
if echo "$FIRST_LINE" | grep -qE 'git\s+(push\s+--force|push\s+-f|reset\s+--hard)'; then
  echo "[bc plugin] BLOCKED: Destructive git operation." >&2
  exit 2
fi

# Drop/truncate database
if echo "$COMMAND" | grep -iqE '(DROP\s+(TABLE|DATABASE)|TRUNCATE\s+TABLE)'; then
  echo "[bc plugin] BLOCKED: Destructive database operation." >&2
  exit 2
fi

# === ALLOW everything else ===
# This plugin does read-only work: fetching data, parsing files, generating reports.
exit 0
