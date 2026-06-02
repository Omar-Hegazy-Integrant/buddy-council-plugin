#!/bin/bash
# PreToolUse hook for Bash. Two jobs:
#   1. HARD-BLOCK destructive operations (exit 2 — always wins, checked first).
#   2. AUTO-APPROVE the plugin's curated, read-only operations by emitting an
#      "allow" permission decision, so the user isn't prompted for commands they
#      would always accept anyway.
# Everything else falls through (exit 0, no JSON) to the normal permission prompt.
#
# Hook protocol: exit 2 = block; stdout JSON with permissionDecision = decision;
# exit 0 with no JSON = defer to the normal permission flow.

set -e

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('tool_input', {}).get('command', '').strip())
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

allow() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"%s"}}\n' "$1"
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
