#!/bin/bash
# Auto-export session logs dataset after each assistant completion.

set -euo pipefail

LOGGER_CMD="${BC_LOGGER_COMMAND:-plugins-logger-export}"
LOCK_DIR="${BC_LOGGER_EXPORT_LOCK_DIR:-/tmp/buddy-council-session-logs-export.lock}"
OUTPUT_PATH="${BC_LOGGER_OUTPUT_PATH:-$HOME/.buddy-council/logs/deepeval/session-logs.jsonl}"
AGENT_NAME="${BC_LOGGER_AGENT:-copilot}"
AGENT_SYSTEM="${BC_LOGGER_AGENT_SYSTEM:-buddy-council}"
RUN_SYNC="${BC_LOGGER_SYNC_BEFORE_EXPORT:-1}"
COMMAND_PREFIXES="${BC_LOGGER_COMMAND_PREFIXES:-/bc:,/bc-local:}"
INCLUDE_AUTOMATED="${BC_LOGGER_INCLUDE_AUTOMATED:-0}"
INCLUDE_ONE_SHOT="${BC_LOGGER_INCLUDE_ONE_SHOT:-0}"

if ! command -v agentsview >/dev/null 2>&1; then
  exit 0
fi

if ! command -v "$LOGGER_CMD" >/dev/null 2>&1; then
  exit 0
fi

# Avoid overlapping export runs when hooks fire close together.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
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
EXPORT_ARGS=(
  --output "$OUTPUT_PATH"
  --agent "$AGENT_NAME"
  --agent-system "$AGENT_SYSTEM"
)

IFS=',' read -r -a prefix_array <<< "$COMMAND_PREFIXES"
for prefix in "${prefix_array[@]}"; do
  trimmed="${prefix//[[:space:]]/}"
  if [[ -n "$trimmed" ]]; then
    EXPORT_ARGS+=(--command-prefix "$trimmed")
  fi
done

if [[ "$INCLUDE_AUTOMATED" == "1" ]]; then
  EXPORT_ARGS+=(--include-automated)
fi
if [[ "$INCLUDE_ONE_SHOT" == "1" ]]; then
  EXPORT_ARGS+=(--include-one-shot)
fi

"$LOGGER_CMD" "${EXPORT_ARGS[@]}" >/dev/null 2>&1 || true

exit 0