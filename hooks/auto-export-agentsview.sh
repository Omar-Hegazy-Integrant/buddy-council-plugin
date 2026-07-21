#!/bin/bash
# Auto-export AgentsView DeepEval dataset after each assistant completion.

set -euo pipefail

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
EXPORT_SCRIPT="$ROOT/scripts/export-agentsview-deepeval-dataset.py"
LOCK_DIR="${BC_AGENTSVIEW_EXPORT_LOCK_DIR:-/tmp/buddy-council-agentsview-export.lock}"
OUTPUT_PATH="${BC_AGENTSVIEW_EXPORT_PATH:-$HOME/.buddy-council/logs/deepeval/agentsview.jsonl}"
AGENT_NAME="${BC_AGENTSVIEW_AGENT:-copilot}"
AGENT_SYSTEM="${BC_AGENTSVIEW_AGENT_SYSTEM:-buddy-council}"
RUN_SYNC="${BC_AGENTSVIEW_SYNC_BEFORE_EXPORT:-1}"

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
python3 "$EXPORT_SCRIPT" \
  --output "$OUTPUT_PATH" \
  --agent "$AGENT_NAME" \
  --agent-system "$AGENT_SYSTEM" >/dev/null 2>&1 || true

exit 0