#!/bin/bash
# Auto-export session logs dataset after each assistant completion.

set -euo pipefail

LOGGER_CMD="${BC_LOGGER_COMMAND:-plugins-logger-export}"
LOCK_DIR="${BC_LOGGER_EXPORT_LOCK_DIR:-/tmp/buddy-council-session-logs-export.lock}"
OUTPUT_PATH="${BC_LOGGER_OUTPUT_PATH:-$HOME/.buddy-council/logs/deepeval/session-logs.jsonl}"
TRACE_PATH="${BC_LOGGER_TRACE_PATH:-$HOME/.buddy-council/logs/deepeval/stop-hook-trace.log}"
TRACE_ENABLED="${BC_LOGGER_TRACE_ENABLED:-1}"
EXPORT_RETRIES="${BC_LOGGER_EXPORT_RETRIES:-2}"
ALLOW_GLOBAL_FALLBACK="${BC_LOGGER_ALLOW_GLOBAL_FALLBACK:-0}"
AGENT_NAME="${BC_LOGGER_AGENT:-}"
AGENT_SYSTEM="${BC_LOGGER_AGENT_SYSTEM:-buddy-council}"
RUN_SYNC="${BC_LOGGER_SYNC_BEFORE_EXPORT:-1}"
COMMAND_PREFIXES="${BC_LOGGER_COMMAND_PREFIXES:-/bc:,/bc-local:}"
INCLUDE_AUTOMATED="${BC_LOGGER_INCLUDE_AUTOMATED:-1}"
INCLUDE_ONE_SHOT="${BC_LOGGER_INCLUDE_ONE_SHOT:-1}"

HOOK_INPUT="$(cat || true)"
SESSION_ID="$(printf '%s' "$HOOK_INPUT" | python3 -c "
import json, sys
try:
  data = json.load(sys.stdin)
  print((data.get('session_id') or '').strip())
except Exception:
  print('')
" 2>/dev/null || true)"

HOOK_EVENT_NAME="$(printf '%s' "$HOOK_INPUT" | python3 -c "
import json, sys
try:
  data = json.load(sys.stdin)
  print((data.get('hook_event_name') or '').strip())
except Exception:
  print('')
" 2>/dev/null || true)"

if ! [[ "$EXPORT_RETRIES" =~ ^[0-9]+$ ]] || [[ "$EXPORT_RETRIES" -lt 1 ]]; then
  EXPORT_RETRIES=1
fi

log_trace() {
  [[ "$TRACE_ENABLED" == "1" ]] || return 0
  local ts event
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  event="$1"
  mkdir -p "$(dirname "$TRACE_PATH")" 2>/dev/null || true
  printf '%s hook_event=%s session_id=%s event=%s agent_filter=%s output=%s\n' \
    "$ts" "${HOOK_EVENT_NAME:-unknown}" "${SESSION_ID:-}" "$event" "${AGENT_NAME:-all}" "$OUTPUT_PATH" >>"$TRACE_PATH" 2>/dev/null || true
}

log_trace "start"

if ! command -v agentsview >/dev/null 2>&1; then
  log_trace "skip_missing_agentsview"
  exit 0
fi

if ! command -v "$LOGGER_CMD" >/dev/null 2>&1; then
  log_trace "skip_missing_logger_cmd"
  exit 0
fi

# Avoid overlapping export runs when hooks fire close together.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log_trace "skip_lock_busy"
  exit 0
fi

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

if [[ "$RUN_SYNC" == "1" ]]; then
  agentsview sync >/dev/null 2>&1 || true
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"
TMP_OUTPUT="$(mktemp -t bc-export.XXXXXX)"
BASE_ARGS=(
  --output "$TMP_OUTPUT"
  --agent-system "$AGENT_SYSTEM"
)

if [[ -n "$SESSION_ID" ]]; then
  SESSION_ARGS=(--session-id "$SESSION_ID")
else
  SESSION_ARGS=()
fi

if [[ -n "$AGENT_NAME" ]]; then
  BASE_ARGS+=(--agent "$AGENT_NAME")
fi

IFS=',' read -r -a prefix_array <<< "$COMMAND_PREFIXES"
for prefix in "${prefix_array[@]}"; do
  trimmed="${prefix//[[:space:]]/}"
  if [[ -n "$trimmed" ]]; then
    BASE_ARGS+=(--command-prefix "$trimmed")
  fi
done

if [[ "$INCLUDE_AUTOMATED" == "1" ]]; then
  BASE_ARGS+=(--include-automated)
fi
if [[ "$INCLUDE_ONE_SHOT" == "1" ]]; then
  BASE_ARGS+=(--include-one-shot)
fi

export_succeeded=0
attempt=1
while [[ "$attempt" -le "$EXPORT_RETRIES" ]]; do
  if "$LOGGER_CMD" "${BASE_ARGS[@]}" "${SESSION_ARGS[@]}" >/dev/null 2>&1; then
    export_succeeded=1
    log_trace "export_ok_attempt_${attempt}"
    break
  fi

  log_trace "export_retry_needed_attempt_${attempt}"
  # Sync + pause so AgentsView finishes indexing before the next attempt.
  agentsview sync >/dev/null 2>&1 || true
  sleep 3
  attempt=$((attempt + 1))
done

if [[ "$export_succeeded" != "1" ]] && [[ "$ALLOW_GLOBAL_FALLBACK" == "1" ]] && [[ ${#SESSION_ARGS[@]} -gt 0 ]] && "$LOGGER_CMD" "${BASE_ARGS[@]}" >/dev/null 2>&1; then
  export_succeeded=1
  log_trace "export_ok_fallback_all_sessions"
fi

if [[ "$export_succeeded" != "1" ]]; then
  log_trace "export_failed"
fi

if [[ "$export_succeeded" == "1" ]]; then
  if [[ ! -f "$OUTPUT_PATH" || ! -s "$OUTPUT_PATH" ]]; then
    mv "$TMP_OUTPUT" "$OUTPUT_PATH"
  else
    python3 - "$OUTPUT_PATH" "$TMP_OUTPUT" <<'PY' >/dev/null 2>&1 || true
import json
import sys

output_path = sys.argv[1]
tmp_path = sys.argv[2]

seen = set()
with open(output_path, 'r', encoding='utf-8', errors='ignore') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        key = line
        try:
            obj = json.loads(line)
            key = obj.get('record_id', line)
        except Exception:
            pass
        seen.add(key)

with open(output_path, 'a', encoding='utf-8') as out_f, open(tmp_path, 'r', encoding='utf-8', errors='ignore') as in_f:
    for line in in_f:
        line = line.strip()
        if not line:
            continue
        key = line
        try:
            obj = json.loads(line)
            key = obj.get('record_id', line)
        except Exception:
            pass
        if key in seen:
            continue
        out_f.write(line + '\n')
        seen.add(key)
PY
    rm -f "$TMP_OUTPUT"
  fi
else
  rm -f "$TMP_OUTPUT"
fi

exit 0