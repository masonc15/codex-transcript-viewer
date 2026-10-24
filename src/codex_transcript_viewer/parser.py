"""Parse Codex CLI JSONL session transcripts into structured events."""

from __future__ import annotations

import ast
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator
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


def parse_lines(lines: Iterable[str]) -> Iterator[dict]:
    """Parse JSONL lines into records, skipping blank, malformed and non-object lines.

    A session still being written can end mid-line, and old logs can hold
    stray bytes, so neither stops the rest of the file from rendering.
    """
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(entry, dict):
            yield entry


def iter_jsonl(path: str | Path) -> Iterator[dict]:
    """Stream the records of a JSONL file; invalid UTF-8 is replaced, not fatal."""
    with open(path, encoding="utf-8", errors="replace") as f:
        yield from parse_lines(f)


def parse_jsonl(path: str | Path) -> list[dict]:
    """Read a JSONL file and return a list of parsed JSON objects."""
    return list(iter_jsonl(path))


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _has_positive_usage(total: dict[str, Any]) -> bool:
    """Return True when total token usage contains any positive numeric value."""
    return any(_finite_number(value) and value > 0 for value in total.values())


def extract_conversation(
    entries: Iterable[dict],
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
        if not isinstance(entry, dict):
            continue
        ts = entry.get("timestamp")
        ts = ts if isinstance(ts, str) else ""
        etype = entry.get("type", "")
        payload = entry.get("payload")
        payload = payload if isinstance(payload, dict) else {}

        if etype == "session_meta":
            # A forked subagent log also carries its parent's session_meta;
            # the first record describes this session.
            if meta is None and payload:
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

        if etype == "turn_context":
            model = _as_text(payload.get("model"))
            if model:
                raw_events.append(
                    {
                        "type": "turn_settings",
                        "ts": ts,
                        "model": model,
                        "effort": _as_text(payload.get("effort") or payload.get("reasoning_effort")),
                        "_source": "turn_context",
                        "_turn_seq": turn_seq,
                    }
                )
            continue

    # Prompts are reconciled before images are attached, so a dropped copy
    # never takes the image the surviving prompt should get.
    raw_events = _drop_legacy_prompts_shadowed_by_items(raw_events)
    raw_events = _attach_model_input_images(raw_events)
    raw_events = _apply_exec_status(raw_events)
    raw_events = _drop_repeated_reasoning_summaries(raw_events)
    raw_events = _drop_unchanged_goal_updates(raw_events)
    raw_events = _keep_turn_settings_changes(raw_events, inherited_turns)
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
# additional_tools lists tool definitions sent to the model mid-session.
_IGNORED_RESPONSE_ITEM = {"ghost_snapshot", "agent_message", "additional_tools"}
_HANDLED_TOP_LEVEL = {"session_meta", "event_msg", "response_item", "turn_context"}
_IGNORED_TOP_LEVEL = {
    "token_usage_record", "compacted", "world_state",
    "inter_agent_communication_metadata", "realtime_item",
}


def unrecognized_kind(entry: Any) -> str | None:
    """The record kind of entry if it is neither parsed nor deliberately ignored."""
    if not isinstance(entry, dict):
        return None
    etype = entry.get("type")
    payload = entry.get("payload")
    subtype = payload.get("type") if isinstance(payload, dict) else None
    if etype == "session_meta":
        return None
    if etype == "event_msg":
        if subtype == "item_completed":
            item = payload.get("item")
            kind = item.get("type") if isinstance(item, dict) else None
            if kind not in _HANDLED_ITEM_COMPLETED and kind not in _IGNORED_ITEM_COMPLETED:
                return f"item_completed/{kind}"
        elif subtype not in _HANDLED_EVENT_MSG and subtype not in _IGNORED_EVENT_MSG:
            return f"event_msg/{subtype}"
    elif etype == "response_item":
        if subtype not in _HANDLED_RESPONSE_ITEM and subtype not in _IGNORED_RESPONSE_ITEM:
            return f"response_item/{subtype}"
    elif etype not in _HANDLED_TOP_LEVEL and etype not in _IGNORED_TOP_LEVEL:
        return str(etype)
    return None


def unrecognized_record_kinds(entries: Iterable[dict]) -> Counter:
    """Count record kinds that are neither parsed nor deliberately ignored."""
    unknown: Counter = Counter()
    for entry in entries:
        kind = unrecognized_kind(entry)
        if kind is not None:
            unknown[kind] += 1
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
                "num_turns": _count(payload.get("num_turns")),
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


def _count(value: Any) -> int | None:
    """A non-negative integer count, or None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _goal_event(goal: dict, ts: str, turn_seq: int) -> dict:
    tokens = goal.get("tokensUsed")
    seconds = goal.get("timeUsedSeconds")
    return {
        "type": "goal_updated",
        "ts": ts,
        "objective": _as_text(goal.get("objective")),
        "status": _as_text(goal.get("status")),
        "tokens_used": _count(tokens),
        "time_used_seconds": seconds if _finite_number(seconds) and seconds >= 0 else None,
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


def _keep_turn_settings_changes(
    events: list[dict], inherited_turns: set[int] | frozenset = frozenset()
) -> list[dict]:
    """Keep the first turn's model and effort, then only turns that change them.

    Codex records both at the start of every turn; most sessions never change.
    A forked subagent's own turns start over, so its first settings survive
    even when they match the inherited history's last ones.
    """
    kept = []
    last: tuple[str, str] | None = None
    last_inherited: bool | None = None
    for event in events:
        if event.get("type") == "turn_settings":
            inherited = event.get("_turn_seq") in inherited_turns
            if inherited != last_inherited:
                last = None
                last_inherited = inherited
            key = (event["model"], event["effort"])
            if key == last:
                continue
            if last is None:
                event["first"] = True
            else:
                event["previous_model"], event["previous_effort"] = last
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
            start, end = (_count(lines.get("start")), _count(lines.get("end"))) if isinstance(lines, dict) else (None, None)
            if where and start is not None:
                where += f":{start}" + (f"-{end}" if end is not None and end != start else "")
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


# Finding titles start with a priority tag the reply may leave out: "[P1] Popup closes".
_FINDING_PRIORITY_RE = re.compile(r"^\[P\d\]\s*")


def _review_repeated_in(review: dict, reply: str) -> bool:
    """True when the reply carries every finding title, or the explanation."""
    reply = _normalize_text(reply).lower()
    titles = [
        _FINDING_PRIORITY_RE.sub("", _normalize_text(f.get("title"))).lower()
        for f in review.get("findings") or []
    ]
    titles = [title for title in titles if title]
    if titles:
        return all(title in reply for title in titles)
    explanation = _normalize_text(review.get("explanation")).lower()
    return bool(explanation) and explanation in reply


def _mark_reviews_repeated_by_reply(events: list[dict]) -> list[dict]:
    """Flag reviews whose findings the assistant message right after them repeats.

    Codex usually writes the review as an assistant message too; the earliest
    review versions went straight back to the model, so the findings are only
    in the review record. A reply only counts if it carries the findings: one
    that just says it will fix them does not.
    """
    for idx, event in enumerate(events):
        if event.get("type") != "review_finished":
            continue
        for later in events[idx + 1:]:
            if later.get("type") in ("user_message", "task_started"):
                break
            if later.get("type") in ("assistant_text", "agent_commentary"):
                if _review_repeated_in(event, later.get("text", "")):
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
# Keys of the JSON wrappers Codex puts around shell and apply_patch output.
# Anything else in an object with an "output" key is shown, not dropped.
_ENVELOPE_KEYS = {"output", "metadata", "exit_code", "chunk_id", "wall_time_seconds"}


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
            # Fields outside Codex's own envelope belong to the tool's result.
            extras = {k: v for k, v in obj.items() if k not in _ENVELOPE_KEYS}
            if extras:
                texts.append(json.dumps(extras, ensure_ascii=False, indent=2))
            code = _exit_code(obj.get("exit_code"))
            metadata = obj.get("metadata")
            if isinstance(metadata, dict):
                code = code if code is not None else _exit_code(metadata.get("exit_code"))
                seconds = metadata.get("duration_seconds")
                if _finite_number(seconds):
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
        if _finite_number(secs) and _finite_number(nanos):
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
        texts = [
            _as_text(block.get("text", ""))
            for block in (content if isinstance(content, list) else [])
            if isinstance(block, dict) and block.get("type") == "output_text"
        ]
        if texts:
            # One message is one entry, however many text blocks it arrived in;
            # its event copies carry the blocks joined.
            event = {
                "type": "assistant_text",
                "ts": ts,
                "text": "\n\n".join(texts),
                "phase": _as_text(payload.get("phase", "")),
                "_source": "response_item",
                "_turn_seq": turn_seq,
            }
            if len(texts) > 1:
                event["_joined_tight"] = "".join(texts)
            events.append(event)
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
    """Show each reasoning summary heading once per turn, where it first appeared.

    Newer models often start a turn's next reasoning item with the whole
    summary logged so far, then add new parts: ["A"], ["A", "B"], ["A", "B"].
    A part an earlier item of the same turn already showed is a restatement;
    repeats within one item are kept. Summaries never carry over between turns.
    """
    kept: list[dict] = []
    shown: dict[Any, set[str]] = {}
    item_turn: Any = None
    item_seen: set[str] = set()
    for event in events:
        snapshot = event.get("_summary")
        if snapshot is None:
            kept.append(event)
            continue
        if event.get("_summary_index") == 0:
            # A new item: what the previous one showed joins its turn's history.
            shown.setdefault(item_turn, set()).update(item_seen)
            item_turn, item_seen = event.get("_turn_seq"), set()
        text = snapshot[event["_summary_index"]]
        if text in shown.get(item_turn, ()):
            continue
        item_seen.add(text)
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


_MATCHABLE_EVENT_MSG_TYPES = {"agent_commentary", "reasoning", "task_complete"}
_MESSAGE_TYPES = {"agent_commentary", "assistant_text"}


class _ResponseIndex:
    """Each turn's response_item messages and reasoning, looked up by text.

    An event_msg copy matches a same-turn response with the same normalized
    text. Older sessions log each assistant message, final answers included,
    as an agent_message, and task_complete repeats the turn's last message;
    those copies leave out proposed-plan and memory-citation blocks, and
    task_complete may cut a final answer short before a trailing block.
    Looking candidates up by text keeps long turns linear.
    """

    def __init__(self, events: list[dict]) -> None:
        self.reasoning: dict[tuple, list[int]] = {}
        self.messages: dict[tuple, list[int]] = {}
        self.finals: dict[Any, list[tuple[int, str]]] = {}
        for idx, event in enumerate(events):
            if event.get("_source") != "response_item":
                continue
            turn = event.get("_turn_seq")
            if event.get("type") == "reasoning":
                key = (turn, _normalize_text(event.get("text", "")))
                self.reasoning.setdefault(key, []).append(idx)
            elif event.get("type") == "assistant_text":
                text = event.get("text", "")
                full = _normalize_text(text)
                keys = {full, _event_copy_text(text)}
                if "_joined_tight" in event:
                    keys |= {_normalize_text(event["_joined_tight"]), _event_copy_text(event["_joined_tight"])}
                for key in keys:
                    self.messages.setdefault((turn, key), []).append(idx)
                if event.get("phase") == "final_answer":
                    self.finals.setdefault(turn, []).append((idx, full))

    def match(self, idx: int, event: dict, used: set[int]) -> int | None:
        """The nearest unused counterpart of events[idx], earlier one on a tie.

        task_complete repeats a message that its agent_message copy may
        already have matched, so it may match a used response too.
        """
        kind = event.get("type")
        turn = event.get("_turn_seq")
        text = _normalize_text(event.get("text", ""))
        if kind == "reasoning":
            options = self.reasoning.get((turn, text), [])
        else:
            options = list(self.messages.get((turn, text), []))
            if kind == "task_complete" and text:
                options += [j for j, full in self.finals.get(turn, []) if full.startswith(text)]
        best: tuple[int, int] | None = None
        for j in options:
            if j in used and kind != "task_complete":
                continue
            if best is None or (abs(j - idx), j) < best:
                best = (abs(j - idx), j)
        return None if best is None else best[1]


def _drop_overlapped_event_msg_events(events: list[dict]) -> list[dict]:
    index = _ResponseIndex(events)
    filtered: list[dict] = []
    used: set[int] = set()
    for idx, event in enumerate(events):
        etype = event.get("type")
        if etype == "task_complete" and not _normalize_text(event.get("text", "")):
            continue
        if event.get("_source") == "event_msg" and etype in _MATCHABLE_EVENT_MSG_TYPES:
            match = index.match(idx, event, used)
            if match is not None:
                if etype == "task_complete":
                    # The message it repeats is the turn's answer, phase or not.
                    events[match]["final"] = True
                else:
                    used.add(match)
                continue
        filtered.append(event)
    return _fold_task_complete_into_messages(filtered)


def _fold_task_complete_into_messages(events: list[dict]) -> list[dict]:
    """Drop a task_complete that repeats the turn's last surviving message.

    Event-only sessions log the last message as an agent_message and again as
    task_complete, with no response_item for either to match.
    """
    kept: list[dict] = []
    for event in events:
        if event.get("type") == "task_complete":
            text = _normalize_text(event.get("text", ""))
            for prior in reversed(kept):
                if prior.get("_turn_seq") != event.get("_turn_seq"):
                    break
                if prior.get("type") not in _MESSAGE_TYPES:
                    continue
                prior_text = prior.get("text", "")
                if text in (_normalize_text(prior_text), _event_copy_text(prior_text)) or (
                    _normalize_text(prior_text).startswith(text)
                ):
                    prior["final"] = True
                    event = None
                break
            if event is None:
                continue
        kept.append(event)
    return kept


def _is_final(event: dict) -> bool:
    return (
        event.get("type") == "task_complete"
        or event.get("final") is True
        or (event.get("type") == "assistant_text" and event.get("phase") == "final_answer")
    )


def _mark_unphased_final_answers(events: list[dict]) -> list[dict]:
    """Mark the answer of each exchange whose log never says which message it is.

    Sessions from before assistant messages carried a phase, and before
    task_complete repeated the answer, only show it by position: the last
    unphased message of an exchange, when no tool call follows it and the turn
    wasn't aborted. An exchange starts at each turn and at each prompt.
    """
    segment: list[dict] = []

    def close() -> None:
        if not segment or any(_is_final(e) or e.get("type") == "turn_aborted" for e in segment):
            return
        for event in reversed(segment):
            if event.get("type") == "tool_call":
                return
            if event.get("type") in _MESSAGE_TYPES:
                if not event.get("phase"):
                    event["final"] = True
                return

    for event in events:
        if event.get("type") in ("task_started", "user_message"):
            close()
            segment = []
        segment.append(event)
    close()
    return events


def _drop_legacy_prompts_shadowed_by_items(events: list[dict]) -> list[dict]:
    """Drop a legacy prompt when an item prompt in its turn has the same text.

    No session has been seen recording both, but if one does, a legacy prompt
    with different text is a separate prompt and stays.
    """
    item_texts: dict[Any, Counter] = {}
    for event in events:
        if event.get("type") == "user_message" and event.get("_source_kind") == "item_completed":
            item_texts.setdefault(event.get("_turn_seq"), Counter())[_normalize_text(event.get("text"))] += 1
    if not item_texts:
        return events
    kept = []
    for event in events:
        if event.get("type") == "user_message" and event.get("_source_kind") == "user_message":
            texts = item_texts.get(event.get("_turn_seq"))
            key = _normalize_text(event.get("text"))
            if texts and texts[key] > 0:
                texts[key] -= 1
                continue
        kept.append(event)
    return kept


def _reconcile_events(events: list[dict]) -> list[dict]:
    merged = _merge_adjacent_token_events(events)
    return _mark_unphased_final_answers(_drop_overlapped_event_msg_events(merged))


_SOURCED_TYPES = {"user_message", "agent_commentary", "assistant_text", "task_complete", "reasoning"}


def _strip_internal_keys(event: dict) -> dict:
    cleaned = {k: v for k, v in event.items() if not k.startswith("_")}
    # Which stream a message came from: "response" is what the model wrote,
    # "event" a copy Codex logged for the UI. The visual review uses it.
    if event.get("type") in _SOURCED_TYPES and event.get("_source"):
        cleaned["source"] = "response" if event["_source"] == "response_item" else "event"
    return cleaned
