"""Parse Codex CLI JSONL session transcripts into structured events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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

    for entry in entries:
        ts = entry.get("timestamp", "")
        etype = entry.get("type", "")
        payload = entry.get("payload", {})

        if etype == "session_meta":
            meta = payload
            continue

        if etype == "event_msg":
            _handle_event_msg(payload, ts, events)
            continue

        if etype == "response_item":
            _handle_response_item(payload, ts, events)
            continue

    return meta, events


def _handle_event_msg(
    payload: dict[str, Any], ts: str, events: list[dict]
) -> None:
    msg_type = payload.get("type", "")

    if msg_type == "user_message":
        events.append(
            {
                "type": "user_message",
                "ts": ts,
                "text": payload.get("message", ""),
                "images": payload.get("local_images", []),
            }
        )
    elif msg_type == "agent_message":
        events.append(
            {
                "type": "agent_commentary",
                "ts": ts,
                "text": payload.get("message", ""),
            }
        )


def _handle_response_item(
    payload: dict[str, Any], ts: str, events: list[dict]
) -> None:
    item_type = payload.get("type", "")
    role = payload.get("role", "")

    if item_type == "message" and role == "assistant":
        content = payload.get("content", [])
        phase = payload.get("phase", "")
        for block in content:
            if block.get("type") == "output_text":
                events.append(
                    {
                        "type": "assistant_text",
                        "ts": ts,
                        "text": block.get("text", ""),
                        "phase": phase,
                    }
                )
