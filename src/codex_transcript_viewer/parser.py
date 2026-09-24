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
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def parse_jsonl(path: str | Path) -> list[dict]:
    """Read a JSONL file and return a list of parsed JSON objects."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


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
    raw_events: list[dict] = []
    meta: dict | None = None
    turn_seq = 0

    for entry in entries:
        ts = entry.get("timestamp", "")
        etype = entry.get("type", "")
        payload = entry.get("payload") or {}

        if etype == "session_meta":
            meta = payload
            continue

        if etype == "event_msg":
            if payload.get("type", "") == "task_started":
                turn_seq += 1
            _handle_event_msg(payload, ts, raw_events, turn_seq)
            continue

        if etype == "response_item":
            _handle_response_item(payload, ts, raw_events, turn_seq)
            continue

    reconciled = _reconcile_events(raw_events)
    cleaned = [_strip_internal_keys(event) for event in reconciled]
    return meta, cleaned


def _handle_event_msg(
    payload: dict[str, Any],
    ts: str,
    events: list[dict],
    turn_seq: int,
) -> None:
    msg_type = payload.get("type", "")

    if msg_type == "user_message":
        events.append(
            {
                "type": "user_message",
                "ts": ts,
                "text": _as_text(payload.get("message", "")),
                "images": payload.get("local_images", []),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )
    elif msg_type == "agent_message":
        events.append(
            {
                "type": "agent_commentary",
                "ts": ts,
                "text": _as_text(payload.get("message", "")),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )
    elif msg_type == "agent_reasoning":
        events.append(
            {
                "type": "reasoning",
                "ts": ts,
                "text": _as_text(payload.get("text", "")),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )
    elif msg_type == "task_complete":
        events.append(
            {
                "type": "task_complete",
                "ts": ts,
                "text": _as_text(payload.get("last_agent_message", "")),
                "turn_id": _as_text(payload.get("turn_id", "")),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )
    elif msg_type == "task_started":
        events.append(
            {
                "type": "task_started",
                "ts": ts,
                "turn_id": _as_text(payload.get("turn_id", "")),
                "model_context_window": payload.get("model_context_window", ""),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )
    elif msg_type == "turn_aborted":
        events.append(
            {
                "type": "turn_aborted",
                "ts": ts,
                "reason": _as_text(payload.get("reason", "")),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )
    elif msg_type == "token_count":
        info = payload.get("info") or {}
        total = info.get("total_token_usage", {})
        if isinstance(total, dict) and total and _has_positive_usage(total):
            rate_limits = payload.get("rate_limits")
            limit_id = (
                _as_text(rate_limits.get("limit_id", ""))
                if isinstance(rate_limits, dict)
                else ""
            )
            events.append(
                {
                    "type": "token_count",
                    "ts": ts,
                    "total": total,
                    "rate_limit_ids": [limit_id] if limit_id else [],
                    "rate_limits": [rate_limits] if isinstance(rate_limits, dict) else [],
                    "_source": "event_msg",
                    "_turn_seq": turn_seq,
                }
            )
    elif msg_type == "thread_rolled_back":
        events.append(
            {
                "type": "thread_rolled_back",
                "ts": ts,
                "num_turns": payload.get("num_turns", 0),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )


def _handle_response_item(
    payload: dict[str, Any],
    ts: str,
    events: list[dict],
    turn_seq: int,
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
                "_source": "response_item",
                "_turn_seq": turn_seq,
            }
        )
    elif item_type == "function_call_output":
        events.append(
            {
                "type": "tool_output",
                "ts": ts,
                "call_id": _as_text(payload.get("call_id", "")),
                "output": _as_text(payload.get("output", "")),
                "_source": "response_item",
                "_turn_seq": turn_seq,
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
                        "_source": "response_item",
                        "_turn_seq": turn_seq,
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
                        "_source": "response_item",
                        "_turn_seq": turn_seq,
                    }
                )


_TOOL_EVENT_TYPES = {"tool_call", "tool_output"}


def _merge_adjacent_token_events(events: list[dict]) -> list[dict]:
    """Collapse repeated token totals within a turn.

    Tool events between two token_count events do not break the run, so how
    many tool calls are visible never changes which token totals survive.
    """
    merged: list[dict] = []
    last_non_tool: dict | None = None
    for event in events:
        if event.get("type") in _TOOL_EVENT_TYPES:
            merged.append(event.copy())
            continue
        if (
            event.get("type") == "token_count"
            and last_non_tool is not None
            and last_non_tool.get("type") == "token_count"
            and last_non_tool.get("_turn_seq") == event.get("_turn_seq")
            and last_non_tool.get("total") == event.get("total")
        ):
            _merge_token_metadata(last_non_tool, event)
            continue
        copy = event.copy()
        merged.append(copy)
        last_non_tool = copy
    return merged


def _merge_token_metadata(base_event: dict, event: dict) -> None:
    base_ids = list(base_event.get("rate_limit_ids", []))
    seen_ids = set(base_ids)
    for limit_id in event.get("rate_limit_ids", []):
        if limit_id not in seen_ids:
            base_ids.append(limit_id)
            seen_ids.add(limit_id)
    base_event["rate_limit_ids"] = base_ids

    base_rate_limits = list(base_event.get("rate_limits", []))
    for rate_limit in event.get("rate_limits", []):
        if isinstance(rate_limit, dict) and rate_limit not in base_rate_limits:
            base_rate_limits.append(rate_limit)
    base_event["rate_limits"] = base_rate_limits


def _normalize_text(value: Any) -> str:
    return " ".join(_as_text(value).split())


def _is_response_counterpart(candidate: dict, response_event: dict) -> bool:
    if response_event.get("_source") != "response_item":
        return False
    if candidate.get("_turn_seq") != response_event.get("_turn_seq"):
        return False

    candidate_type = candidate.get("type")
    candidate_text = _normalize_text(candidate.get("text", ""))
    response_text = _normalize_text(response_event.get("text", ""))
    if candidate_type == "agent_commentary":
        if response_event.get("type") != "assistant_text":
            return False
        if response_event.get("phase") == "final_answer":
            return False
    elif candidate_type == "reasoning":
        if response_event.get("type") != "reasoning":
            return False
    elif candidate_type == "task_complete":
        if response_event.get("type") != "assistant_text":
            return False
        if response_event.get("phase") != "final_answer":
            # Plan-mode turns end on commentary; task_complete repeats it verbatim.
            return candidate_text == response_text
        # task_complete often repeats the final answer without its trailing block.
        return bool(candidate_text) and response_text.startswith(candidate_text)
    else:
        return False

    return candidate_text == response_text


_MATCHABLE_EVENT_MSG_TYPES = {"agent_commentary", "reasoning", "task_complete"}
_RESPONSE_COUNTERPART_TYPES = {"assistant_text", "reasoning"}


def _find_matching_response_index(
    events: list[dict],
    idx: int,
    turn_pool: list[int],
    used_indices: set[int],
) -> int | None:
    """Return the nearest unused same-turn response_item duplicating events[idx].

    The pool holds only response-side message and reasoning events, so tool
    events never push a counterpart out of reach.
    """
    candidate = events[idx]
    if candidate.get("_source") != "event_msg":
        return None
    if candidate.get("type") not in _MATCHABLE_EVENT_MSG_TYPES:
        return None

    for j in sorted(turn_pool, key=lambda j: abs(j - idx)):
        if j in used_indices:
            continue
        if _is_response_counterpart(candidate, events[j]):
            return j
    return None


def _drop_overlapped_event_msg_events(events: list[dict]) -> list[dict]:
    pools: dict[Any, list[int]] = {}
    for idx, event in enumerate(events):
        if (
            event.get("_source") == "response_item"
            and event.get("type") in _RESPONSE_COUNTERPART_TYPES
        ):
            pools.setdefault(event.get("_turn_seq"), []).append(idx)

    filtered: list[dict] = []
    used_response_indices: set[int] = set()

    for idx, event in enumerate(events):
        if event.get("type") == "task_complete" and not _normalize_text(event.get("text", "")):
            continue

        match_idx = _find_matching_response_index(
            events, idx, pools.get(event.get("_turn_seq"), []), used_response_indices
        )
        if match_idx is not None:
            used_response_indices.add(match_idx)
            continue

        filtered.append(event)

    return filtered


def _reconcile_events(events: list[dict]) -> list[dict]:
    merged = _merge_adjacent_token_events(events)
    return _drop_overlapped_event_msg_events(merged)


def _strip_internal_keys(event: dict) -> dict:
    return {k: v for k, v in event.items() if not k.startswith("_")}
