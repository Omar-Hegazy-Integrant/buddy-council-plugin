#!/usr/bin/env python3
"""Export DeepEval-ready records from AgentsView session APIs.

This script is a parallel adapter path. It does not replace the current custom
Buddy Council logger; it reads from AgentsView when AgentsView is installed and
indexed, then emits evaluation-ready JSONL records.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export DeepEval dataset from AgentsView")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--agent", default="copilot", help="AgentsView agent filter")
    parser.add_argument("--project", help="Optional exact project filter")
    parser.add_argument("--session-id", help="Optional single session id")
    parser.add_argument(
        "--agent-system",
        default="buddy-council",
        help="Logical system label for exported metadata",
    )
    parser.add_argument(
        "--command-prefix",
        action="append",
        default=["/bc:", "/bc-local:"],
        help="Only export turns whose user input starts with one of these prefixes",
    )
    parser.add_argument(
        "--include-automated",
        action="store_true",
        help="Include automated sessions when listing sessions",
    )
    parser.add_argument(
        "--include-one-shot",
        action="store_true",
        help="Include one-shot sessions when listing sessions",
    )
    return parser.parse_args()


def run_agentsview_json(args: List[str]) -> Any:
    if shutil.which("agentsview") is None:
        raise SystemExit("agentsview is not installed or not on PATH")

    cmd = ["agentsview", *args, "--format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"agentsview command failed: {' '.join(cmd)}\n{result.stderr.strip()}")

    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse JSON from agentsview command {' '.join(cmd)}: {exc}") from exc


def extract_rows(payload: Any, preferred_keys: Iterable[str]) -> List[Dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def list_sessions(parsed: argparse.Namespace) -> List[Dict[str, Any]]:
    if parsed.session_id:
        session = run_agentsview_json(["session", "get", parsed.session_id])
        return [session] if isinstance(session, dict) else []

    cmd = ["session", "list", "--agent", parsed.agent]
    if parsed.project:
        cmd.extend(["--project", parsed.project])
    if parsed.include_automated:
        cmd.append("--include-automated")
    if parsed.include_one_shot:
        cmd.append("--include-one-shot")

    payload = run_agentsview_json(cmd)
    return extract_rows(payload, ["sessions", "items", "rows"])


def load_messages(session_id: str) -> List[Dict[str, Any]]:
    payload = run_agentsview_json(["session", "messages", session_id])
    return extract_rows(payload, ["messages", "items", "rows"])


def load_tool_calls(session_id: str) -> List[Dict[str, Any]]:
    payload = run_agentsview_json(["session", "tool-calls", session_id])
    return extract_rows(payload, ["tool_calls", "toolCalls", "items", "rows"])


def load_session_usage(session_id: str) -> Dict[str, Any]:
    # Usage may be unavailable for some sessions; keep export non-fatal.
    cmd = ["agentsview", "session", "usage", session_id, "--format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {}

    stdout = result.stdout.strip()
    if not stdout:
        return {}

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


def first_str(dct: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = dct.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def first_list(dct: Dict[str, Any], *keys: str) -> List[Any]:
    for key in keys:
        value = dct.get(key)
        if isinstance(value, list):
            return value
    return []


def normalize_role(message: Dict[str, Any]) -> Optional[str]:
    role = first_str(message, "role", "author_role", "kind", "type")
    if role is None:
        return None
    role = role.lower()
    if role in {"user", "assistant", "system"}:
        return role
    return None


def normalize_message_text(message: Dict[str, Any]) -> Optional[str]:
    return first_str(message, "content", "text", "body", "markdown")


def is_relevant_user_input(text: str, prefixes: List[str]) -> bool:
    stripped = text.strip()
    return any(stripped.startswith(prefix) for prefix in prefixes)


def normalize_tool_call(call: Dict[str, Any]) -> Dict[str, Any]:
    raw_input = first_str(call, "input_json", "input", "arguments", "args")
    parsed_input: Optional[Any] = None
    if raw_input:
        try:
            parsed_input = json.loads(raw_input)
        except json.JSONDecodeError:
            parsed_input = raw_input

    result_length = call.get("result_length")
    status = first_str(call, "status", "result", "outcome")
    if status is None and isinstance(result_length, int):
        status = "ok"

    return {
        "ts": first_str(call, "timestamp", "ts", "started_at", "created_at"),
        "event": first_str(call, "event", "type") or "tool_call",
        "tool_name": first_str(call, "tool_name", "toolName", "name"),
        "tool_call_id": first_str(call, "tool_call_id", "toolCallId", "tool_use_id", "id"),
        "status": status,
        "detail": first_str(call, "detail", "arguments", "args", "output", "result_text", "input_json"),
        "category": first_str(call, "category"),
        "ordinal": call.get("ordinal"),
        "result_length": result_length,
        "input": parsed_input,
    }


def timestamp_for(row: Dict[str, Any]) -> Optional[str]:
    return first_str(row, "timestamp", "ts", "created_at", "started_at")


def in_time_window(ts: Optional[str], start_ts: Optional[str], end_ts: Optional[str]) -> bool:
    if ts is None:
        return False
    if start_ts and ts < start_ts:
        return False
    if end_ts and ts > end_ts:
        return False
    return True


def relevant_tool_calls(tool_calls: List[Dict[str, Any]], user_ts: Optional[str], assistant_ts: Optional[str]) -> List[Dict[str, Any]]:
    # Scope tool calls to the turn window [user_ts, assistant_ts].
    in_window = [
        normalize_tool_call(call)
        for call in tool_calls
        if in_time_window(timestamp_for(call), user_ts, assistant_ts)
    ]
    if in_window:
        return in_window

    # Fallback for sources with missing timestamps on tool rows.
    if user_ts is None and assistant_ts is None:
        return [normalize_tool_call(call) for call in tool_calls]
    return []


def build_records(parsed: argparse.Namespace) -> List[Dict[str, Any]]:
    sessions = list_sessions(parsed)
    records: List[Dict[str, Any]] = []

    for session in sessions:
        session_id = first_str(session, "id", "session_id")
        if not session_id:
            continue

        messages = load_messages(session_id)
        tool_calls = load_tool_calls(session_id)
        usage = load_session_usage(session_id)
        session_cost_usd = usage.get("cost_usd")
        session_total_output_tokens = usage.get("total_output_tokens")
        cost_per_output_token: Optional[float] = None
        if isinstance(session_cost_usd, (int, float)) and isinstance(session_total_output_tokens, int) and session_total_output_tokens > 0:
            cost_per_output_token = float(session_cost_usd) / float(session_total_output_tokens)

        turn_index = 0
        active_user_input: Optional[str] = None
        active_user_ts: Optional[str] = None
        assistant_messages: List[Dict[str, Any]] = []
        last_assistant_ts: Optional[str] = None

        def flush_turn() -> None:
            nonlocal turn_index
            nonlocal active_user_input
            nonlocal active_user_ts
            nonlocal assistant_messages
            nonlocal last_assistant_ts

            if not active_user_input or not assistant_messages:
                return

            turn_index += 1
            assistant_output = "\n\n".join(
                text.strip()
                for text in (normalize_message_text(msg) or "" for msg in assistant_messages)
                if text.strip()
            )
            turn_output_tokens = 0
            turn_peak_context_tokens = 0
            for msg in assistant_messages:
                output_tokens = msg.get("output_tokens")
                context_tokens = msg.get("context_tokens")
                if isinstance(output_tokens, int):
                    turn_output_tokens += output_tokens
                if isinstance(context_tokens, int) and context_tokens > turn_peak_context_tokens:
                    turn_peak_context_tokens = context_tokens

            estimated_turn_cost_usd: Optional[float] = None
            if cost_per_output_token is not None and turn_output_tokens > 0:
                estimated_turn_cost_usd = cost_per_output_token * float(turn_output_tokens)

            records.append(
                {
                    "record_id": f"{session_id}:turn-{turn_index}",
                    "session_id": session_id,
                    "channel": "agentsview",
                    "input": active_user_input,
                    "actual_output": assistant_output,
                    "tool_calls": relevant_tool_calls(tool_calls, active_user_ts, last_assistant_ts),
                    "metadata": {
                        "agent_system": parsed.agent_system,
                        "source": "agentsview",
                        "agent": first_str(session, "agent") or parsed.agent,
                        "project": (session.get("project") or {}).get("display_label") if isinstance(session.get("project"), dict) else first_str(session, "project"),
                        "started_at": first_str(session, "started_at"),
                        "ended_at": first_str(session, "ended_at"),
                        "message_count": session.get("message_count"),
                        "turn_count": session.get("turn_count"),
                        "user_ts": active_user_ts,
                        "assistant_ts": last_assistant_ts,
                        "turn_output_tokens": turn_output_tokens,
                        "turn_peak_context_tokens": turn_peak_context_tokens,
                        "turn_estimated_cost_usd": estimated_turn_cost_usd,
                        "session_total_output_tokens": session_total_output_tokens,
                        "session_peak_context_tokens": usage.get("peak_context_tokens"),
                        "session_cost_usd": session_cost_usd,
                        "session_has_cost": usage.get("has_cost"),
                        "session_has_token_data": usage.get("has_token_data"),
                        "session_models": usage.get("models"),
                    },
                }
            )

            active_user_input = None
            active_user_ts = None
            assistant_messages = []
            last_assistant_ts = None

        for message in messages:
            role = normalize_role(message)
            text = normalize_message_text(message)
            if role is None or not text:
                continue

            if role == "user":
                flush_turn()
                if is_relevant_user_input(text, parsed.command_prefix):
                    active_user_input = text
                    active_user_ts = timestamp_for(message)
                    assistant_messages = []
                    last_assistant_ts = None
                else:
                    active_user_input = None
                    active_user_ts = None
                    assistant_messages = []
                    last_assistant_ts = None
                continue

            if role == "assistant" and active_user_input is not None:
                assistant_messages.append(message)
                last_assistant_ts = timestamp_for(message)

        flush_turn()

    return records


def main() -> int:
    parsed = parse_args()
    records = build_records(parsed)
    output_path = Path(parsed.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    print(f"exported={len(records)} output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())