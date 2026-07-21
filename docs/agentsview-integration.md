# AgentsView Integration

This repository uses the **AgentsView-based export path** as the primary evaluation export flow:

1. AgentsView ingests and indexes sessions.
2. Buddy Council adapter reads AgentsView programmatic session APIs.
3. DeepEval-ready JSONL is exported from the adapter.

## AgentsView-based Export Script

Use:

```bash
python3 scripts/export-agentsview-deepeval-dataset.py \
  --output /tmp/bc-agentsview-deepeval.jsonl \
  --agent copilot \
  --agent-system buddy-council
```

Optional filters:

```bash
python3 scripts/export-agentsview-deepeval-dataset.py \
  --output /tmp/bc-agentsview-deepeval.jsonl \
  --agent copilot \
  --project CW \
  --command-prefix /bc-local: \
  --command-prefix /bc:
```

## Requirements

The script assumes:

1. `agentsview` is installed and on `PATH`.
2. AgentsView has already indexed your local sessions.
3. The following commands are available and return JSON:
   - `agentsview session list --format json`
   - `agentsview session get <id> --format json`
   - `agentsview session messages <id> --format json`
   - `agentsview session tool-calls <id> --format json`

## Current status

This adapter is intentionally conservative:

1. It filters to Buddy Council command-style user prompts such as `/bc:` and `/bc-local:`.
2. It emits per-turn evaluation records.

## Automatic export after each command

AgentsView export can run automatically after each assistant response via hook:

- Hook script: `${CLAUDE_PLUGIN_ROOT}/hooks/auto-export-agentsview.sh`
- Hook event: `agentStop`

Default output path:

- `~/.buddy-council/logs/deepeval/agentsview.jsonl`

Optional environment overrides:

- `BC_AGENTSVIEW_EXPORT_PATH`: output file path
- `BC_AGENTSVIEW_AGENT`: agent filter (default `copilot`)
- `BC_AGENTSVIEW_AGENT_SYSTEM`: metadata label (default `buddy-council`)
- `BC_AGENTSVIEW_SYNC_BEFORE_EXPORT`: `1` to run `agentsview sync` before export (default `1`)
- `BC_AGENTSVIEW_EXPORT_LOCK_DIR`: lock directory path for overlap protection

## Notes

1. Export records are rewritten on each run (snapshot behavior).
2. Auto-export is configured to run on `agentStop` for near real-time updates.