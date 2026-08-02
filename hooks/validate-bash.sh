#!/bin/bash
# PreToolUse hook for the shell tool (Claude Code "Bash" / Copilot CLI "bash").
# Two jobs:
#   1. HARD-BLOCK destructive operations (exit 2 — always wins, checked first).
#   2. AUTO-APPROVE the plugin's curated, read-only operations by emitting an
#      "allow" permission decision, so the user isn't prompted for commands they
#      would always accept anyway.
# Everything else falls through (exit 0, no JSON) to the normal permission prompt.
#
# Runs under BOTH runtimes: Claude Code sends {tool_name, tool_input: {command}};
# Copilot CLI sends {toolName, toolArgs: "<json string with command>"}. Exit 2 =
# block/deny on both. NOTE: no `set -e` here — Copilot treats any non-zero exit
# from a preToolUse hook as DENY (fail-closed), so incidental failures must not
# change the exit code.

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input')
    if ti is None:
        ta = d.get('toolArgs')
        ti = json.loads(ta) if isinstance(ta, str) else (ta or {})
    print((ti.get('command') or '').strip())
except Exception:
    print('')
" 2>/dev/null)

FIRST_LINE=$(echo "$COMMAND" | head -1)

# ---------------------------------------------------------------------------
# 1. HARD BLOCK — destructive operations
# ---------------------------------------------------------------------------

if echo "$FIRST_LINE" | grep -qE '^\s*rm\s+(-[rRf]+|--force|--recursive)'; then
  echo "[bc plugin] BLOCKED: Destructive rm command." >&2; exit 2
fi
if echo "$FIRST_LINE" | grep -qE '>\s*/etc/|>\s*/usr/|>\s*/var/'; then
  echo "[bc plugin] BLOCKED: Writing to system directories." >&2; exit 2
fi
if echo "$FIRST_LINE" | grep -qE '^\s*(kill|pkill|killall)\s'; then
  echo "[bc plugin] BLOCKED: Process termination commands." >&2; exit 2
fi
if echo "$FIRST_LINE" | grep -qE 'git\s+(push\s+--force|push\s+-f|reset\s+--hard)'; then
  echo "[bc plugin] BLOCKED: Destructive git operation." >&2; exit 2
fi
if echo "$COMMAND" | grep -iqE '(DROP\s+(TABLE|DATABASE)|TRUNCATE\s+TABLE)'; then
  echo "[bc plugin] BLOCKED: Destructive database operation." >&2; exit 2
fi

# ---------------------------------------------------------------------------
# 2. AUTO-APPROVE — curated, read-only plugin operations
# ---------------------------------------------------------------------------

# Emits BOTH decision shapes: top-level keys for Copilot CLI, the
# hookSpecificOutput wrapper for Claude Code. Each runtime reads its own.
allow() {
  printf '{"permissionDecision":"allow","permissionDecisionReason":"%s","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"%s"}}\n' "$1" "$1"
  exit 0
}

# An HTTP-write indicator (used to keep gh api auto-approval read-only).
has_gh_write() {
  echo "$COMMAND" | grep -iqE '(-X|--request|--method)[[:space:]]*(POST|PUT|PATCH|DELETE)|(^|[[:space:]])(-f|--field|--input)([[:space:]]|=)'
}

# (a) The Excel requirements parser — a fixed, read-only script path.
if echo "$COMMAND" | grep -q 'providers/excel/parse\.py'; then
  allow "bc: Excel parser (read-only)"
fi

# (b) jq — read-only JSON processing.
if echo "$FIRST_LINE" | grep -qE '^[[:space:]]*jq[[:space:]]'; then
  allow "bc: jq (read-only)"
fi

# (c) gh read-only: --version / auth status / api GET (no write verbs or fields).
if echo "$FIRST_LINE" | grep -qE '^[[:space:]]*gh[[:space:]]+(--version|auth[[:space:]]+status|api[[:space:]])'; then
  if ! has_gh_write; then
    allow "bc: gh read-only"
  fi
fi

# (d) chmod 600 — locking down the secrets file.
if echo "$FIRST_LINE" | grep -qE '^[[:space:]]*chmod[[:space:]]+600[[:space:]]'; then
  allow "bc: chmod secrets file"
fi

# (e) TestRail connection smoke-test via curl (GET only).
if echo "$FIRST_LINE" | grep -qE '^[[:space:]]*curl[[:space:]]' && echo "$COMMAND" | grep -qi 'testrail'; then
  if ! echo "$COMMAND" | grep -iqE '(-X|--request)[[:space:]]*(POST|PUT|PATCH|DELETE)|(^|[[:space:]])(-d|--data)([[:space:]]|=)'; then
    allow "bc: TestRail connection test (read-only)"
  fi
fi

# (f) Dependency setup (pip / uv pip install, uv venv).
if echo "$FIRST_LINE" | grep -qE '^[[:space:]]*(pip3?|python3?[[:space:]]+-m[[:space:]]+pip|uv[[:space:]]+pip)[[:space:]]+install[[:space:]]' \
   || echo "$FIRST_LINE" | grep -qE '^[[:space:]]*uv[[:space:]]+venv([[:space:]]|$)'; then
  allow "bc: dependency setup"
fi

# ---------------------------------------------------------------------------
# 3. Everything else — defer to the normal permission prompt.
# ---------------------------------------------------------------------------
exit 0
