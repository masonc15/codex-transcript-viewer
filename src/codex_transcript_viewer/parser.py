"""Parse Codex CLI JSONL session transcripts into structured events."""

from __future__ import annotations

import json
import re
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
    inherited_turns: set[int] = set()

    for entry in entries:
        ts = entry.get("timestamp", "")
        etype = entry.get("type", "")
        payload = entry.get("payload") or {}

        if etype == "session_meta":
            # A forked subagent log also carries its parent's session_meta;
            # the first record describes this session.
            if meta is None:
                meta = payload
            continue

        if etype == "event_msg":
            if payload.get("type", "") == "task_started":
                turn_seq += 1
                if _is_inherited_turn(meta, payload):
                    inherited_turns.add(turn_seq)
            _handle_event_msg(payload, ts, raw_events, turn_seq)
            continue

        if etype == "response_item":
            _handle_response_item(payload, ts, raw_events, turn_seq)
            continue

    raw_events = _attach_model_input_images(raw_events)
    raw_events = _apply_exec_status(raw_events)
    reconciled = _reconcile_events(raw_events)
    for event in reconciled:
        if event.get("_turn_seq") in inherited_turns:
            event["inherited"] = True
    cleaned = [_strip_internal_keys(event) for event in reconciled]
    return meta, cleaned


_INHERITANCE_TOLERANCE_SECONDS = 3.0


def _uuid7_seconds(value: Any) -> float | None:
    """Creation time embedded in a UUIDv7, in seconds, or None."""
    if not isinstance(value, str):
        return None
    digits = value.replace("-", "")
    if len(digits) != 32 or digits[12] != "7":
        return None
    try:
        return int(digits[:12], 16) / 1000
    except ValueError:
        return None


def _is_inherited_turn(meta: dict | None, task_started: dict) -> bool:
    """True for a turn copied from the parent into a forked subagent log.

    Session and turn ids are UUIDv7, so a turn created before this subagent
    session existed belongs to the parent's history.
    """
    if not isinstance(meta, dict):
        return False
    source = meta.get("source")
    if not isinstance(source, dict) or "subagent" not in source:
        return False
    session_time = _uuid7_seconds(meta.get("id"))
    turn_time = _uuid7_seconds(task_started.get("turn_id"))
    if session_time is None or turn_time is None:
        return False
    return turn_time < session_time - _INHERITANCE_TOLERANCE_SECONDS


def _handle_event_msg(
    payload: dict[str, Any],
    ts: str,
    events: list[dict],
    turn_seq: int,
) -> None:
    msg_type = payload.get("type", "")

    if msg_type == "user_message":
        local_images = payload.get("local_images")
        if not isinstance(local_images, list):
            local_images = []
        events.append(
            {
                "type": "user_message",
                "ts": ts,
                "text": _as_text(payload.get("message", "")),
                "images": local_images,
                "attachments": _legacy_image_attachments(local_images),
                "_source": "event_msg",
                "_source_kind": "user_message",
                "_turn_seq": turn_seq,
            }
        )
    elif msg_type == "item_completed":
        _handle_item_completed(payload, ts, events, turn_seq)
    elif msg_type in ("exec_command_end", "patch_apply_end"):
        _handle_exec_end(payload, events)
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


def _legacy_image_attachments(local_images: list) -> list[dict]:
    attachments = []
    for image in local_images:
        path = image.get("path") if isinstance(image, dict) else image
        if isinstance(path, str) and path:
            attachments.append({"kind": "local_image", "path": path})
    return attachments


def _data_url_bytes(url: str) -> int:
    """Approximate decoded size of a base64 data URL."""
    _, _, data = url.partition(",")
    return len(data) * 3 // 4


def _user_message_attachment(block: dict) -> dict | None:
    kind = block.get("type")
    if kind == "local_image":
        path = block.get("path")
        return {"kind": "local_image", "path": path} if isinstance(path, str) else None
    if kind == "image":
        url = block.get("image_url")
        if isinstance(url, dict):
            url = url.get("url")
        if not isinstance(url, str):
            return None
        return {"kind": "image", "bytes": _data_url_bytes(url), "data_url": url}
    if kind in ("skill", "mention"):
        name = block.get("name")
        if not isinstance(name, str) or not name:
            return None
        return {"kind": kind, "name": name, "path": _as_text(block.get("path"))}
    return None


_MODEL_INPUT_IMAGES = "_model_input_images"
_IMAGE_ATTACHMENT_KINDS = {"local_image", "image"}


def _input_image_urls(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    urls = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "input_image":
            continue
        url = block.get("image_url")
        if isinstance(url, dict):
            url = url.get("url")
        if isinstance(url, str) and url.startswith("data:image/"):
            urls.append(url)
    return urls


def _attach_model_input_images(events: list[dict]) -> list[dict]:
    """Give prompt image attachments the bytes the model received.

    Local image files are usually gone (temp paths), but the session keeps the
    base64 copy sent to the model in the same turn, in the same order.
    """
    queues: dict[Any, list[str]] = {}
    for event in events:
        if event.get("type") == _MODEL_INPUT_IMAGES:
            queues.setdefault(event.get("_turn_seq"), []).extend(event["urls"])

    for event in events:
        if event.get("type") != "user_message":
            continue
        queue = queues.get(event.get("_turn_seq"))
        for attachment in event.get("attachments", []):
            if attachment.get("kind") not in _IMAGE_ATTACHMENT_KINDS or not queue:
                continue
            url = queue.pop(0)
            if "data_url" not in attachment:
                attachment["data_url"] = url
                attachment["bytes"] = _data_url_bytes(url)

    return [event for event in events if event.get("type") != _MODEL_INPUT_IMAGES]


def _handle_item_completed(
    payload: dict[str, Any],
    ts: str,
    events: list[dict],
    turn_seq: int,
) -> None:
    """Read typed prompts from CLI 0.135+ sessions.

    Only UserMessage items are used. The other item kinds duplicate
    response_item records that are already parsed.
    """
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "UserMessage":
        return
    content = item.get("content")
    if not isinstance(content, list):
        content = []

    texts: list[str] = []
    attachments: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = _as_text(block.get("text"))
            if text:
                texts.append(text)
            continue
        attachment = _user_message_attachment(block)
        if attachment is not None:
            attachments.append(attachment)

    text = "\n\n".join(texts).strip()
    if not text and not attachments:
        return

    event = {
        "type": "user_message",
        "ts": ts,
        "text": text,
        "images": [a["path"] for a in attachments if a["kind"] == "local_image"],
        "attachments": attachments,
        "turn_id": _as_text(payload.get("turn_id")),
        "item_id": _as_text(item.get("id")),
        "_source": "event_msg",
        "_source_kind": "item_completed",
        "_turn_seq": turn_seq,
    }
    client_id = item.get("client_id")
    if isinstance(client_id, str) and client_id:
        event["client_id"] = client_id
    events.append(event)


def _json_object(text: str) -> dict | None:
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _exit_code(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def normalize_tool_output(value: Any) -> dict:
    """Split a tool output into display text, image attachments and exit status.

    Outputs come as plain strings, JSON strings wrapping {output, metadata}
    (apply_patch), or lists of content blocks (images, code-mode exec chunks).
    Exit codes are read only from structured fields, never searched for in text.
    """
    texts: list[str] = []
    attachments: list[dict] = []
    exit_codes: list[int] = []
    duration: float | None = None

    def add_text(text: str) -> None:
        nonlocal duration
        obj = _json_object(text)
        if obj is not None and "output" in obj:
            texts.append(_as_text(obj.get("output")))
            code = _exit_code(obj.get("exit_code"))
            metadata = obj.get("metadata")
            if isinstance(metadata, dict):
                code = code if code is not None else _exit_code(metadata.get("exit_code"))
                seconds = metadata.get("duration_seconds")
                if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                    duration = float(seconds)
            if code is not None:
                exit_codes.append(code)
            return
        texts.append(text)

    if isinstance(value, str):
        add_text(value)
    elif isinstance(value, list):
        for block in value:
            if not isinstance(block, dict):
                texts.append(_as_text(block))
                continue
            kind = block.get("type")
            if kind in ("input_text", "output_text", "text"):
                add_text(_as_text(block.get("text")))
            elif kind == "input_image":
                url = block.get("image_url")
                if isinstance(url, dict):
                    url = url.get("url")
                if isinstance(url, str) and url.startswith("data:image/"):
                    attachments.append(
                        {"kind": "image", "bytes": _data_url_bytes(url), "data_url": url}
                    )
                else:
                    attachments.append({"kind": "image", "bytes": 0})
            else:
                texts.append(_as_text(block))
    else:
        texts.append(_as_text(value))

    return {
        "output": "\n".join(t for t in texts if t),
        "attachments": attachments,
        "exit_codes": exit_codes,
        "failed": None,
        "duration": duration,
    }


_EXEC_END = "_exec_end"
_EXIT_HEADER_RE = re.compile(r"^Process exited with code (-?\d+)\s*$")
_PATCH_FAILED_PREFIX = "apply_patch verification failed"


def _handle_exec_end(payload: dict[str, Any], events: list[dict]) -> None:
    """Record structured exit status, applied to the matching output later."""
    call_id = payload.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return
    status: dict[str, Any] = {"type": _EXEC_END, "call_id": call_id}
    code = _exit_code(payload.get("exit_code"))
    if code is not None:
        status["exit_code"] = code
    success = payload.get("success")
    if isinstance(success, bool):
        status["success"] = success
    duration = payload.get("duration")
    if isinstance(duration, dict):
        secs, nanos = duration.get("secs"), duration.get("nanos")
        if isinstance(secs, (int, float)) and isinstance(nanos, (int, float)):
            status["duration"] = secs + nanos / 1e9
    events.append(status)


def _header_exit_code(output: str) -> int | None:
    """Legacy exec outputs state the exit code in their first few header lines."""
    for line in output.splitlines()[:4]:
        match = _EXIT_HEADER_RE.match(line)
        if match:
            return int(match.group(1))
    return None


def _apply_exec_status(events: list[dict]) -> list[dict]:
    """Attach exit status to tool outputs: end events first, then output headers."""
    statuses = {e["call_id"]: e for e in events if e.get("type") == _EXEC_END}
    for event in events:
        if event.get("type") != "tool_output":
            continue
        status = statuses.get(event.get("call_id"))
        if status is not None:
            if "exit_code" in status and not event.get("exit_codes"):
                event["exit_codes"] = [status["exit_code"]]
            if status.get("success") is False:
                event["failed"] = True
            elif status.get("success") is True and event.get("failed") is None:
                event["failed"] = False
            if event.get("duration") is None and "duration" in status:
                event["duration"] = status["duration"]
        output = event.get("output", "")
        if not event.get("exit_codes") and event.get("failed") is None:
            code = _header_exit_code(output)
            if code is not None:
                event["exit_codes"] = [code]
        if output.startswith(_PATCH_FAILED_PREFIX):
            event["failed"] = True
    return [e for e in events if e.get("type") != _EXEC_END]


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
                **normalize_tool_output(payload.get("output", "")),
                "_source": "response_item",
                "_turn_seq": turn_seq,
            }
        )
    elif item_type == "message" and role == "user":
        # Only the images are used; see the note below about the text.
        urls = _input_image_urls(payload.get("content"))
        if urls:
            events.append(
                {
                    "type": _MODEL_INPUT_IMAGES,
                    "urls": urls,
                    "_source": "response_item",
                    "_turn_seq": turn_seq,
                }
            )
    # Messages with role "user" are the model's input, not what the user typed:
    # they carry injected context (environment, AGENTS.md, skills) and, in
    # forked subagents, the parent's history. Typed prompts come from
    # event_msg user_message (CLI <= 0.125) or item_completed UserMessage.
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


def _drop_legacy_prompts_shadowed_by_items(events: list[dict]) -> list[dict]:
    """Keep one prompt source per turn if a session ever records both."""
    item_turns = {
        event.get("_turn_seq")
        for event in events
        if event.get("type") == "user_message"
        and event.get("_source_kind") == "item_completed"
    }
    return [
        event
        for event in events
        if not (
            event.get("type") == "user_message"
            and event.get("_source_kind") == "user_message"
            and event.get("_turn_seq") in item_turns
        )
    ]


def _reconcile_events(events: list[dict]) -> list[dict]:
    merged = _merge_adjacent_token_events(events)
    deduped = _drop_overlapped_event_msg_events(merged)
    return _drop_legacy_prompts_shadowed_by_items(deduped)


def _strip_internal_keys(event: dict) -> dict:
    return {k: v for k, v in event.items() if not k.startswith("_")}
