#!/bin/bash
# Auto-export AgentsView DeepEval dataset after each assistant completion.

set -euo pipefail

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
LOGGER_ROOT="${PLUGINS_LOGGER_ROOT:-$HOME/Desktop/work-ai/Plugins-logger}"
EXPORT_SCRIPT="${PLUGINS_LOGGER_EXPORT_SCRIPT:-$LOGGER_ROOT/scripts/export-agentsview-deepeval-dataset.py}"
LOCK_DIR="${BC_AGENTSVIEW_EXPORT_LOCK_DIR:-/tmp/buddy-council-agentsview-export.lock}"
OUTPUT_PATH="${BC_AGENTSVIEW_EXPORT_PATH:-$HOME/.buddy-council/logs/deepeval/agentsview.jsonl}"
AGENT_NAME="${BC_AGENTSVIEW_AGENT:-copilot}"
AGENT_SYSTEM="${BC_AGENTSVIEW_AGENT_SYSTEM:-buddy-council}"
RUN_SYNC="${BC_AGENTSVIEW_SYNC_BEFORE_EXPORT:-1}"
COMMAND_PREFIXES="${BC_AGENTSVIEW_COMMAND_PREFIXES:-/bc:,/bc-local:}"
INCLUDE_AUTOMATED="${BC_AGENTSVIEW_INCLUDE_AUTOMATED:-0}"
INCLUDE_ONE_SHOT="${BC_AGENTSVIEW_INCLUDE_ONE_SHOT:-0}"

if [[ ! -f "$EXPORT_SCRIPT" ]]; then
  exit 0
fi

if ! command -v agentsview >/dev/null 2>&1; then
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
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

python3 "$EXPORT_SCRIPT" "${EXPORT_ARGS[@]}" >/dev/null 2>&1 || true

exit 0