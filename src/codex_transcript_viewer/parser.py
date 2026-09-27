"""Parse Codex CLI JSONL session transcripts into structured events."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
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
    raw_events = _drop_repeated_reasoning_summaries(raw_events)
    raw_events = _drop_unchanged_goal_updates(raw_events)
    reconciled = _mark_reviews_repeated_by_reply(_reconcile_events(raw_events))
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


# Record kinds the parser reads, and kinds it skips on purpose because they
# duplicate other records or carry no transcript content. Anything outside both
# sets is reported by unrecognized_record_kinds() so format changes get noticed.
_HANDLED_EVENT_MSG = {
    "user_message", "agent_message", "agent_reasoning", "task_complete",
    "task_started", "turn_aborted", "token_count", "thread_rolled_back",
    "item_completed", "exec_command_end", "patch_apply_end",
    "thread_goal_updated", "entered_review_mode", "exited_review_mode", "error",
}
# guardian_assessment and collab_* come from one alpha build; the commands and
# spawn_agent calls they describe are already shown as tool calls.
_IGNORED_EVENT_MSG = {
    "mcp_tool_call_end", "view_image_tool_call", "web_search_end",
    "context_compacted", "thread_settings_applied", "dynamic_tool_call_request",
    "dynamic_tool_call_response", "thread_name_updated", "image_generation_end",
    "guardian_assessment", "collab_agent_spawn_end", "collab_waiting_end",
    "collab_close_end", "undo_completed",
}
_HANDLED_ITEM_COMPLETED = {"UserMessage", "EnteredReviewMode", "ExitedReviewMode", "HookPrompt"}
_IGNORED_ITEM_COMPLETED = {
    "AgentMessage", "CommandExecution", "Reasoning", "FileChange", "McpToolCall",
    "WebSearch", "Extension", "Plan", "ContextCompaction", "SubAgentActivity",
    "ImageView", "FunctionCallOutput", "CollabAgentToolCall", "DynamicToolCall",
}
_HANDLED_RESPONSE_ITEM = {
    "function_call", "function_call_output", "custom_tool_call",
    "custom_tool_call_output", "web_search_call", "tool_search_call",
    "tool_search_output", "message", "reasoning", "image_generation_call",
}
_IGNORED_RESPONSE_ITEM = {"ghost_snapshot", "agent_message"}
_IGNORED_TOP_LEVEL = {
    "token_usage_record", "turn_context", "compacted", "world_state",
    "inter_agent_communication_metadata", "realtime_item",
}


def unrecognized_record_kinds(entries: list[dict]) -> Counter:
    """Count record kinds that are neither parsed nor deliberately ignored."""
    unknown: Counter = Counter()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        payload = entry.get("payload")
        subtype = payload.get("type") if isinstance(payload, dict) else None
        if etype == "session_meta":
            continue
        if etype == "event_msg":
            if subtype == "item_completed":
                item = payload.get("item")
                kind = item.get("type") if isinstance(item, dict) else None
                if kind not in _HANDLED_ITEM_COMPLETED and kind not in _IGNORED_ITEM_COMPLETED:
                    unknown[f"item_completed/{kind}"] += 1
            elif subtype not in _HANDLED_EVENT_MSG and subtype not in _IGNORED_EVENT_MSG:
                unknown[f"event_msg/{subtype}"] += 1
        elif etype == "response_item":
            if subtype not in _HANDLED_RESPONSE_ITEM and subtype not in _IGNORED_RESPONSE_ITEM:
                unknown[f"response_item/{subtype}"] += 1
        elif etype not in _IGNORED_TOP_LEVEL:
            unknown[str(etype)] += 1
    return unknown


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
        info = payload.get("info")
        total = info.get("total_token_usage") if isinstance(info, dict) else None
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
    elif msg_type == "thread_goal_updated":
        goal = payload.get("goal")
        if isinstance(goal, dict):
            events.append(_goal_event(goal, ts, turn_seq))
    elif msg_type == "entered_review_mode":
        events.append(_review_started_event(payload, ts, turn_seq))
    elif msg_type == "exited_review_mode":
        events.append(_review_finished_event(payload.get("review_output"), ts, turn_seq))
    elif msg_type == "error":
        events.append(
            {
                "type": "error",
                "ts": ts,
                "message": _as_text(payload.get("message")),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )


def _goal_event(goal: dict, ts: str, turn_seq: int) -> dict:
    tokens = goal.get("tokensUsed")
    seconds = goal.get("timeUsedSeconds")
    return {
        "type": "goal_updated",
        "ts": ts,
        "objective": _as_text(goal.get("objective")),
        "status": _as_text(goal.get("status")),
        "tokens_used": tokens if isinstance(tokens, int) else None,
        "time_used_seconds": seconds if isinstance(seconds, (int, float)) else None,
        "_source": "event_msg",
        "_turn_seq": turn_seq,
    }


def _drop_unchanged_goal_updates(events: list[dict]) -> list[dict]:
    """Keep goal updates that set a goal or change its status.

    Codex logs the goal again after almost every step to update its token and
    time counters; one session has 2,477 updates and six real changes.
    """
    kept = []
    last: tuple[str, str] | None = None
    for event in events:
        if event.get("type") == "goal_updated":
            key = (event["objective"], event["status"])
            if key == last:
                continue
            event["new_objective"] = last is None or last[0] != event["objective"]
            last = key
        kept.append(event)
    return kept


def _review_started_event(payload: dict, ts: str, turn_seq: int) -> dict:
    hint = _as_text(payload.get("user_facing_hint")) or _as_text(payload.get("prompt"))
    return {
        "type": "review_started",
        "ts": ts,
        "hint": hint,
        "_source": "event_msg",
        "_turn_seq": turn_seq,
    }


def _review_finished_event(output: Any, ts: str, turn_seq: int) -> dict:
    output = output if isinstance(output, dict) else {}
    findings = []
    raw_findings = output.get("findings")
    for finding in raw_findings if isinstance(raw_findings, list) else []:
        if not isinstance(finding, dict):
            continue
        location = finding.get("code_location")
        where = ""
        if isinstance(location, dict):
            where = _as_text(location.get("absolute_file_path"))
            lines = location.get("line_range")
            if where and isinstance(lines, dict) and isinstance(lines.get("start"), int):
                start, end = lines["start"], lines.get("end")
                where += f":{start}" + (f"-{end}" if isinstance(end, int) and end != start else "")
        findings.append(
            {
                "title": _as_text(finding.get("title")),
                "body": _as_text(finding.get("body")),
                "location": where,
            }
        )
    return {
        "type": "review_finished",
        "ts": ts,
        "verdict": _as_text(output.get("overall_correctness")),
        "explanation": _as_text(output.get("overall_explanation")),
        "findings": findings,
        "_source": "event_msg",
        "_turn_seq": turn_seq,
    }


def _mark_reviews_repeated_by_reply(events: list[dict]) -> list[dict]:
    """Flag reviews whose findings an assistant message right after them repeats.

    Codex usually writes the review as an assistant message too; the earliest
    review versions went straight back to the model, so the findings are only
    in the review record.
    """
    for idx, event in enumerate(events):
        if event.get("type") != "review_finished":
            continue
        for later in events[idx + 1:]:
            if later.get("type") in ("user_message", "task_started"):
                break
            if later.get("type") in ("assistant_text", "agent_commentary"):
                event["repeated_by_reply"] = True
                break
    return events


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
    """Read typed prompts from CLI 0.135+ sessions, plus review and hook items.

    The other item kinds duplicate response_item records that are already parsed.
    """
    item = payload.get("item")
    if not isinstance(item, dict):
        return
    kind = item.get("type")
    if kind == "EnteredReviewMode":
        events.append(_review_started_event(item, ts, turn_seq))
        return
    if kind == "ExitedReviewMode":
        events.append(_review_finished_event(item.get("review_output"), ts, turn_seq))
        return
    if kind == "HookPrompt":
        _handle_hook_prompt(item, ts, events, turn_seq)
        return
    if kind != "UserMessage":
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


def _handle_hook_prompt(item: dict, ts: str, events: list[dict], turn_seq: int) -> None:
    """Text a hook sent to the model; otherwise it is only in the model's input."""
    fragments = item.get("fragments")
    if not isinstance(fragments, list):
        return
    texts, hooks = [], []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        text = _as_text(fragment.get("text")).strip()
        if text:
            texts.append(text)
        # hookRunId looks like "stop:1:/path/to/hooks.json".
        hook = _as_text(fragment.get("hookRunId")).split(":", 1)[0]
        if hook and hook not in hooks:
            hooks.append(hook)
    if texts:
        events.append(
            {
                "type": "hook_prompt",
                "ts": ts,
                "text": "\n\n".join(texts),
                "hook": ", ".join(hooks),
                "_source": "event_msg",
                "_turn_seq": turn_seq,
            }
        )


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


_TRUNCATION_NOTICE = "Warning: truncated output"


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
        if text.startswith(_TRUNCATION_NOTICE) and "\n{" in text:
            # Code-mode prefixes an oversized chunk with a notice; the JSON
            # chunk after it still carries the exit code when it is complete.
            head, _, tail = text.partition("\n{")
            if _json_object("{" + tail) is not None:
                texts.append(head.rstrip())
                add_text("{" + tail)
                return
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


def _web_search_summary(action: Any) -> str:
    if not isinstance(action, dict) or not action:
        return "(no details recorded)"
    kind = _as_text(action.get("type")) or "search"
    if kind == "search":
        query = _as_text(action.get("query"))
        queries = action.get("queries")
        extra = [
            _as_text(q) for q in queries if _as_text(q) and _as_text(q) != query
        ] if isinstance(queries, list) else []
        lines = [f"search: {query}" if query else "search"]
        lines += [f"  also: {q}" for q in extra]
        return "\n".join(lines)
    if kind == "open_page":
        return f"open_page: {_as_text(action.get('url'))}"
    if kind == "find_in_page":
        return f'find_in_page: "{_as_text(action.get("pattern"))}" in {_as_text(action.get("url"))}'
    return f"{kind}: {_as_text(action)}"


def _tool_search_query(arguments: Any) -> str:
    """tool_search arguments arrive as JSON or as a Python dict repr."""
    parsed = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(arguments)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                return arguments
    if isinstance(parsed, dict) and "query" in parsed:
        return _as_text(parsed.get("query"))
    return _as_text(parsed)


def _tool_search_names(tools: Any, prefix: str = "") -> list[str]:
    """Flatten returned tool namespaces into qualified tool names."""
    names: list[str] = []
    if not isinstance(tools, list):
        return names
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = _as_text(tool.get("name"))
        qualified = f"{prefix}.{name}" if prefix and name else name or prefix
        if isinstance(tool.get("tools"), list):
            names.extend(_tool_search_names(tool["tools"], qualified))
        elif qualified:
            names.append(qualified)
    return names


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
    elif item_type == "custom_tool_call":
        events.append(
            {
                "type": "tool_call",
                "ts": ts,
                "name": _as_text(payload.get("name", "")),
                "arguments": _as_text(payload.get("input", "")),
                "input_kind": "custom",
                "call_id": _as_text(payload.get("call_id", "")),
                "_source": "response_item",
                "_turn_seq": turn_seq,
            }
        )
    elif item_type == "web_search_call":
        # Single record: no call_id and no separate output.
        events.append(
            {
                "type": "tool_call",
                "ts": ts,
                "name": "web_search",
                "arguments": _web_search_summary(payload.get("action")),
                "input_kind": "web_search",
                "call_id": "",
                "_source": "response_item",
                "_turn_seq": turn_seq,
            }
        )
    elif item_type == "tool_search_call":
        events.append(
            {
                "type": "tool_call",
                "ts": ts,
                "name": "tool_search",
                "arguments": _tool_search_query(payload.get("arguments")),
                "input_kind": "tool_search",
                "call_id": _as_text(payload.get("call_id", "")),
                "_source": "response_item",
                "_turn_seq": turn_seq,
            }
        )
    elif item_type == "tool_search_output":
        names = _tool_search_names(payload.get("tools"))
        events.append(
            {
                "type": "tool_output",
                "ts": ts,
                "call_id": _as_text(payload.get("call_id", "")),
                "output": "\n".join(names) if names else "(no tools returned)",
                "attachments": [],
                "exit_codes": [],
                "failed": None,
                "duration": None,
                "_source": "response_item",
                "_turn_seq": turn_seq,
            }
        )
    elif item_type == "custom_tool_call_output":
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
        content = payload.get("content")
        phase = payload.get("phase", "")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "output_text":
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
        summary = payload.get("summary")
        texts = [
            _as_text(s.get("text", ""))
            for s in (summary if isinstance(summary, list) else [])
            if isinstance(s, dict) and s.get("type") == "summary_text"
        ]
        snapshot = [_normalize_text(text) for text in texts]
        for index, text in enumerate(texts):
            events.append(
                {
                    "type": "reasoning",
                    "ts": ts,
                    "text": text,
                    "_summary": snapshot,
                    "_summary_index": index,
                    "_source": "response_item",
                    "_turn_seq": turn_seq,
                }
            )
    elif item_type == "image_generation_call":
        _handle_image_generation(payload, ts, events, turn_seq)


_IMAGE_BASE64_TYPES = (("iVBORw0KGgo", "png"), ("/9j/", "jpeg"), ("UklGR", "webp"), ("R0lGOD", "gif"))


def _handle_image_generation(
    payload: dict[str, Any], ts: str, events: list[dict], turn_seq: int
) -> None:
    """Show an image generation as a tool call whose output is the image."""
    call_id = _as_text(payload.get("id")) or f"image-generation-{len(events)}"
    events.append(
        {
            "type": "tool_call",
            "ts": ts,
            "name": "image_generation",
            "arguments": _as_text(payload.get("revised_prompt")) or "(no prompt recorded)",
            "input_kind": "image_generation",
            "call_id": call_id,
            "_source": "response_item",
            "_turn_seq": turn_seq,
        }
    )
    result = payload.get("result")
    attachments = []
    if isinstance(result, str) and result:
        kind = next((k for prefix, k in _IMAGE_BASE64_TYPES if result.startswith(prefix)), None)
        attachment = {"kind": "image", "bytes": _data_url_bytes("," + result), "label": "generated image"}
        if kind:
            attachment["data_url"] = f"data:image/{kind};base64,{result}"
        attachments.append(attachment)
    status = _as_text(payload.get("status"))
    events.append(
        {
            "type": "tool_output",
            "ts": ts,
            "call_id": call_id,
            "output": "" if attachments else f"(no image recorded; status {status or 'unknown'})",
            "attachments": attachments,
            "exit_codes": [],
            "failed": False if attachments else (True if status == "failed" else None),
            "duration": None,
            "_source": "response_item",
            "_turn_seq": turn_seq,
        }
    )


_TOOL_EVENT_TYPES = {"tool_call", "tool_output"}


def _drop_repeated_reasoning_summaries(events: list[dict]) -> list[dict]:
    """Show each reasoning summary heading once per turn.

    Newer models often start a turn's next reasoning item with the whole
    summary logged so far, then add new parts: ["A"], ["A", "B"], ["A", "B"].
    When an item's summary starts with the previous item's full summary, only
    the parts after it are new. Summaries never carry over between turns.
    """
    kept: list[dict] = []
    previous: dict[Any, list[str]] = {}
    repeated = 0
    for event in events:
        snapshot = event.get("_summary")
        if snapshot is None:
            kept.append(event)
            continue
        turn = event.get("_turn_seq")
        if event.get("_summary_index") == 0:
            before = previous.get(turn) or []
            repeated = len(before) if before and snapshot[: len(before)] == before else 0
            previous[turn] = snapshot
        if event["_summary_index"] >= repeated:
            kept.append(event)
    return kept


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


_EVENT_COPY_OMITS_RE = re.compile(r"<(oai-mem-citation|proposed_plan)>(?:.*?</\1>|.*\Z)", re.S)


def _event_copy_text(response_text: Any) -> str:
    """The text an event_msg copy of this response carries.

    agent_message and task_complete copies leave out proposed-plan and
    memory-citation blocks, wherever they sit in the answer.
    """
    return _normalize_text(_EVENT_COPY_OMITS_RE.sub("", _as_text(response_text)))


def _is_response_counterpart(candidate: dict, response_event: dict) -> bool:
    if response_event.get("_source") != "response_item":
        return False
    if candidate.get("_turn_seq") != response_event.get("_turn_seq"):
        return False

    candidate_type = candidate.get("type")
    candidate_text = _normalize_text(candidate.get("text", ""))
    response_text = _normalize_text(response_event.get("text", ""))
    if candidate_type == "reasoning":
        return response_event.get("type") == "reasoning" and candidate_text == response_text
    if candidate_type not in ("agent_commentary", "task_complete"):
        return False
    if response_event.get("type") != "assistant_text":
        return False
    # Older sessions log each assistant message, final answers included, as an
    # agent_message; task_complete repeats the turn's last message.
    if candidate_text in (response_text, _event_copy_text(response_event.get("text", ""))):
        return True
    # task_complete may also cut a final answer short before a trailing block.
    return (
        candidate_type == "task_complete"
        and response_event.get("phase") == "final_answer"
        and bool(candidate_text)
        and response_text.startswith(candidate_text)
    )


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
    events never push a counterpart out of reach. task_complete repeats a
    message that its agent_message copy may already have matched, so it may
    match a used response too.
    """
    candidate = events[idx]
    if candidate.get("_source") != "event_msg":
        return None
    if candidate.get("type") not in _MATCHABLE_EVENT_MSG_TYPES:
        return None

    for j in sorted(turn_pool, key=lambda j: abs(j - idx)):
        if j in used_indices and candidate.get("type") != "task_complete":
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
            if event.get("type") != "task_complete":
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
