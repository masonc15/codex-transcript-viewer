"""Parse Codex CLI JSONL session transcripts into structured events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _as_text(value: Any) -> str:
    """Normalize possibly-null payload fields to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def parse_jsonl(path: str | Path) -> list[dict]:
    """Read a JSONL file and return a list of parsed JSON objects."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _detect_response_item_support(entries: list[dict]) -> tuple[bool, bool, bool]:
    """Detect whether response_item stream provides assistant/reasoning/final text.

    Returns:
        (has_assistant_messages, has_reasoning_summaries, has_final_answers)
    """
    has_assistant_messages = False
    has_reasoning_summaries = False
    has_final_answers = False

    for entry in entries:
        if entry.get("type") != "response_item":
            continue

        payload = entry.get("payload", {})
        item_type = payload.get("type", "")

        if item_type == "message" and payload.get("role", "") == "assistant":
            for block in payload.get("content", []) or []:
                if block.get("type") != "output_text":
                    continue
                has_assistant_messages = True
                if payload.get("phase", "") == "final_answer":
                    has_final_answers = True
                break

        elif item_type == "reasoning":
            for summary_item in payload.get("summary", []) or []:
                if summary_item.get("type") == "summary_text":
                    has_reasoning_summaries = True
                    break

        if (
            has_assistant_messages
            and has_reasoning_summaries
            and has_final_answers
        ):
            break

    return has_assistant_messages, has_reasoning_summaries, has_final_answers


def _has_positive_usage(total: dict[str, Any]) -> bool:
    """Return True when total token usage contains any positive numeric value."""
    return any(
        isinstance(value, (int, float)) and value > 0
        for value in total.values()
    )


def extract_conversation(
    entries: list[dict],
) -> tuple[dict | None, list[dict]]:
    """Extract session metadata and meaningful conversation events.

    Returns (meta, events) where meta is the session_meta payload and events
    is a flat list of typed dicts representing user messages, assistant
    responses, tool calls, reasoning blocks, and system events.
    """
    events: list[dict] = []
    meta: dict | None = None
    (
        has_response_assistant_messages,
        has_response_reasoning,
        has_response_final_answers,
    ) = _detect_response_item_support(entries)
    last_token_total_key: tuple[tuple[str, Any], ...] | None = None

    for entry in entries:
        ts = entry.get("timestamp", "")
        etype = entry.get("type", "")
        payload = entry.get("payload", {})

        if etype == "session_meta":
            meta = payload
            continue

        if etype == "event_msg":
            last_token_total_key = _handle_event_msg(
                payload,
                ts,
                events,
                include_agent_messages=not has_response_assistant_messages,
                include_agent_reasoning=not has_response_reasoning,
                include_task_complete=not has_response_final_answers,
                last_token_total_key=last_token_total_key,
            )
            continue

        if etype == "response_item":
            _handle_response_item(payload, ts, events)
            continue

    return meta, events


def _handle_event_msg(
    payload: dict[str, Any],
    ts: str,
    events: list[dict],
    *,
    include_agent_messages: bool,
    include_agent_reasoning: bool,
    include_task_complete: bool,
    last_token_total_key: tuple[tuple[str, Any], ...] | None,
) -> tuple[tuple[str, Any], ...] | None:
    msg_type = payload.get("type", "")

    if msg_type == "user_message":
        events.append(
            {
                "type": "user_message",
                "ts": ts,
                "text": _as_text(payload.get("message", "")),
                "images": payload.get("local_images", []),
            }
        )
    elif msg_type == "agent_message" and include_agent_messages:
        events.append(
            {
                "type": "agent_commentary",
                "ts": ts,
                "text": _as_text(payload.get("message", "")),
            }
        )
    elif msg_type == "agent_reasoning" and include_agent_reasoning:
        events.append(
            {
                "type": "reasoning",
                "ts": ts,
                "text": _as_text(payload.get("text", "")),
            }
        )
    elif msg_type == "task_complete" and include_task_complete:
        final_text = _as_text(payload.get("last_agent_message", ""))
        if not final_text:
            return last_token_total_key
        events.append(
            {
                "type": "task_complete",
                "ts": ts,
                "text": final_text,
                "turn_id": _as_text(payload.get("turn_id", "")),
            }
        )
    elif msg_type == "task_started":
        events.append(
            {
                "type": "task_started",
                "ts": ts,
                "turn_id": _as_text(payload.get("turn_id", "")),
                "model_context_window": payload.get("model_context_window", ""),
            }
        )
    elif msg_type == "turn_aborted":
        events.append(
            {
                "type": "turn_aborted",
                "ts": ts,
                "reason": _as_text(payload.get("reason", "")),
            }
        )
    elif msg_type == "token_count":
        info = payload.get("info") or {}
        total = info.get("total_token_usage", {})
        if isinstance(total, dict) and total and _has_positive_usage(total):
            token_total_key = tuple(sorted(total.items()))
            if token_total_key == last_token_total_key:
                return last_token_total_key
            events.append(
                {
                    "type": "token_count",
                    "ts": ts,
                    "total": total,
                }
            )
            return token_total_key
    elif msg_type == "thread_rolled_back":
        events.append(
            {
                "type": "thread_rolled_back",
                "ts": ts,
                "num_turns": payload.get("num_turns", 0),
            }
        )
    return last_token_total_key


def _handle_response_item(
    payload: dict[str, Any], ts: str, events: list[dict]
) -> None:
    item_type = payload.get("type", "")
    role = payload.get("role", "")

    if item_type == "function_call":
        events.append(
            {
                "type": "tool_call",
                "ts": ts,
                "name": _as_text(payload.get("name", "")),
                "arguments": _as_text(payload.get("arguments", "")),
                "call_id": _as_text(payload.get("call_id", "")),
            }
        )
    elif item_type == "function_call_output":
        events.append(
            {
                "type": "tool_output",
                "ts": ts,
                "call_id": _as_text(payload.get("call_id", "")),
                "output": _as_text(payload.get("output", "")),
            }
        )
    elif item_type == "message" and role == "assistant":
        content = payload.get("content", [])
        phase = payload.get("phase", "")
        for block in content:
            if block.get("type") == "output_text":
                events.append(
                    {
                        "type": "assistant_text",
                        "ts": ts,
                        "text": _as_text(block.get("text", "")),
                        "phase": _as_text(phase),
                    }
                )
    elif item_type == "reasoning":
        summary = payload.get("summary", [])
        for s in summary:
            if s.get("type") == "summary_text":
                events.append(
                    {
                        "type": "reasoning",
                        "ts": ts,
                        "text": _as_text(s.get("text", "")),
                    }
                )
