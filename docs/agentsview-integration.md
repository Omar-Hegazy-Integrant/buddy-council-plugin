# AgentsView Integration

This repository uses the **AgentsView-based export path** as the primary evaluation export flow:

1. AgentsView ingests and indexes sessions.
2. Buddy Council adapter reads AgentsView programmatic session APIs.
3. DeepEval-ready JSONL is exported from the adapter.

## AgentsView-based Export Script

Buddy Council calls an installed logger CLI from the shared private logger repository.

- Repo: `https://github.com/dexcom-inc/Plugins-logger`
- CLI command: `plugins-logger-export`
- Install (pinned release example):

```bash
uv tool install --from git+ssh://git@github.com/dexcom-inc/Plugins-logger.git@v0.1.0 plugins-logger-export
```

Use:

```bash
plugins-logger-export \
  --output /tmp/bc-session-logs.jsonl \
  --agent copilot \
  --agent-system buddy-council
```

Optional filters:

```bash
plugins-logger-export \
  --output /tmp/bc-session-logs.jsonl \
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
2. It emits two record types:
  - `session_usage`: one summary record per session.
  - `turn`: one record per relevant user turn.

## Export record model

The exporter writes JSONL records with these shapes.

### `session_usage` record (one per session)

Top-level fields:

- `record_id`: `<session_id>:session-usage`
- `record_type`: `session_usage`
- `session_id`
- `channel`: `agentsview`
- `input`: empty string
- `actual_output`: empty string
- `tool_calls`: empty array
- `metadata`: session-level usage and context

Session metadata includes:

- `session_total_input_tokens`
- `session_total_output_tokens`
- `session_total_cache_creation_input_tokens`
- `session_total_cache_read_input_tokens`
- `session_cost_usd`
- `session_cost_note`
- `session_has_cost`
- `session_has_token_data`
- `session_models`

### `turn` record (one per relevant command turn)

Top-level fields:

- `record_id`: `<session_id>:turn-<n>`
- `record_type`: `turn`
- `session_id`
- `channel`: `agentsview`
- `input`: relevant user command text
- `actual_output`: concatenated assistant output for that turn
- `tool_calls`: tool calls scoped to that turn window
- `metadata`: turn-level metrics and traceability

Turn metadata includes:

- `user_ts`
- `assistant_ts`
- `turn_output_tokens`
- `turn_peak_context_tokens`
- `turn_estimated_cost_usd`
- `turn_cost_note`
- `session_usage_record_id` (pointer to `<session_id>:session-usage`)

Important: session-wide usage fields are intentionally not duplicated in each `turn` record.

## Automatic export after each command

Session logs export can run automatically after each assistant response via hook:

- Hook script: `${CLAUDE_PLUGIN_ROOT}/hooks/auto-export-session-logs.sh`
- Hook event: `agentStop`

Default output path:

- `~/.buddy-council/logs/deepeval/session-logs.jsonl`

Optional environment overrides:

- `BC_LOGGER_OUTPUT_PATH`: output file path
- `BC_LOGGER_AGENT`: agent filter (default `copilot`)
- `BC_LOGGER_AGENT_SYSTEM`: metadata label (default `buddy-council`)
- `BC_LOGGER_COMMAND`: logger command name (default `plugins-logger-export`)
- `BC_LOGGER_SYNC_BEFORE_EXPORT`: `1` to run `agentsview sync` before export (default `1`)
- `BC_LOGGER_EXPORT_LOCK_DIR`: lock directory path for overlap protection
- `BC_LOGGER_COMMAND_PREFIXES`: comma-separated prefixes (default `/bc:,/bc-local:`)
- `BC_LOGGER_INCLUDE_AUTOMATED`: set `1` to include automated sessions
- `BC_LOGGER_INCLUDE_ONE_SHOT`: set `1` to include one-shot sessions

## Notes

1. Export records are rewritten on each run (snapshot behavior).
2. Auto-export is configured to run on `agentStop` for near real-time updates.
3. `session_cost_usd` is an estimate from AgentsView usage data, not billing ground truth.
4. Input token totals are larger than typed user text because they include system prompts, conversation history, and tool schema context.
5. Cached token fields (`session_total_cache_*`) represent context reuse and should be analyzed separately from fresh input/output tokens.

## Consumer query patterns

Use `session_usage_record_id` from each `turn` record to attach session-wide usage.

Example flow:

1. Load all JSONL rows.
2. Build a map keyed by `record_id` for `record_type == session_usage`.
3. For each `turn`, lookup `metadata.session_usage_record_id` in that map.
4. Combine fields as needed for reporting.

Python example:

```python
import json

rows = []
with open("/tmp/bc-session-logs.jsonl", "r", encoding="utf-8") as f:
  for line in f:
    line = line.strip()
    if line:
      rows.append(json.loads(line))

session_usage_by_id = {
  row["record_id"]: row
  for row in rows
  if row.get("record_type") == "session_usage"
}

joined_turns = []
for row in rows:
  if row.get("record_type") != "turn":
    continue
  turn_md = row.get("metadata", {})
  session_row = session_usage_by_id.get(turn_md.get("session_usage_record_id"), {})
  session_md = session_row.get("metadata", {})

  joined_turns.append(
    {
      "record_id": row.get("record_id"),
      "session_id": row.get("session_id"),
      "turn_output_tokens": turn_md.get("turn_output_tokens"),
      "turn_estimated_cost_usd": turn_md.get("turn_estimated_cost_usd"),
      "session_total_input_tokens": session_md.get("session_total_input_tokens"),
      "session_total_output_tokens": session_md.get("session_total_output_tokens"),
      "session_total_cache_creation_input_tokens": session_md.get("session_total_cache_creation_input_tokens"),
      "session_total_cache_read_input_tokens": session_md.get("session_total_cache_read_input_tokens"),
      "session_cost_usd": session_md.get("session_cost_usd"),
    }
  )
```

`jq` example (single turn + session summary projection):

```bash
jq -s '
  (map(select(.record_type == "session_usage"))
   | map({key: .record_id, value: .})
   | from_entries) as $s
  | map(select(.record_type == "turn")
    | . as $t
    | ($s[$t.metadata.session_usage_record_id]) as $u
    | {
      record_id: $t.record_id,
      session_id: $t.session_id,
      turn_output_tokens: $t.metadata.turn_output_tokens,
      turn_estimated_cost_usd: $t.metadata.turn_estimated_cost_usd,
      session_total_input_tokens: $u.metadata.session_total_input_tokens,
      session_total_output_tokens: $u.metadata.session_total_output_tokens,
      session_cost_usd: $u.metadata.session_cost_usd
    }
  )
' /tmp/bc-session-logs.jsonl
```