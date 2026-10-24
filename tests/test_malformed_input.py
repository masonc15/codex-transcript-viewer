"""Malformed or hostile records must neither crash the viewer nor inject markup."""

from __future__ import annotations

import copy
import math
import tempfile
import unittest
from pathlib import Path

from codex_transcript_viewer.formatting import format_ts, format_ts_full
from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import extract_conversation, parse_jsonl

TS = "2026-09-27T01:00:00Z"
SVG = "<svg/onload=x=42>"  # short enough to survive a timestamp fallback


def _event_msg(payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": TS, "payload": payload}


def _response_item(payload: dict) -> dict:
    return {"type": "response_item", "timestamp": TS, "payload": payload}


# One record of every kind the parser reads.
SESSION = [
    {"type": "session_meta", "timestamp": TS, "payload": {
        "id": "01990000-1000-7000-8000-000000000000", "timestamp": TS, "cli_version": "0.1",
        "cwd": "/r", "git": {"branch": "main", "commit_hash": "abc"}, "model_provider": "openai",
        "source": {"subagent": {"thread_spawn": {"agent_nickname": "a", "parent_thread_id": "p",
                                                 "depth": 1}}}}},
    _event_msg({"type": "task_started", "turn_id": "t1", "model_context_window": 1000}),
    {"type": "turn_context", "timestamp": TS, "payload": {"model": "gpt", "effort": "high"}},
    _event_msg({"type": "user_message", "message": "hi", "local_images": ["/tmp/a.png"]}),
    _event_msg({"type": "item_completed", "turn_id": "t1", "item": {
        "type": "UserMessage", "id": "u", "content": [{"type": "text", "text": "hi"},
                                                      {"type": "local_image", "path": "/tmp/a.png"}]}}),
    _response_item({"type": "message", "role": "user", "content": [
        {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="}]}),
    _response_item({"type": "reasoning", "summary": [{"type": "summary_text", "text": "**Plan**"}]}),
    _event_msg({"type": "agent_reasoning", "text": "**Plan**"}),
    _response_item({"type": "function_call", "name": "exec_command", "arguments": '{"cmd":"ls"}',
                    "call_id": "c1"}),
    _response_item({"type": "function_call_output", "call_id": "c1",
                    "output": '{"output":"x","metadata":{"exit_code":0,"duration_seconds":1}}'}),
    _event_msg({"type": "exec_command_end", "call_id": "c1", "exit_code": 0, "success": True,
                "duration": {"secs": 1, "nanos": 5}}),
    _response_item({"type": "custom_tool_call", "name": "apply_patch", "input": "*** Begin",
                    "call_id": "c2"}),
    _response_item({"type": "custom_tool_call_output", "call_id": "c2", "output": "Done"}),
    _response_item({"type": "web_search_call", "action": {"type": "search", "query": "q"}}),
    _response_item({"type": "tool_search_call", "call_id": "c3", "arguments": '{"query":"q"}'}),
    _response_item({"type": "tool_search_output", "call_id": "c3", "tools": [{"name": "t"}]}),
    _response_item({"type": "image_generation_call", "id": "ig", "revised_prompt": "cat",
                    "result": "iVBORw0KGgo=", "status": "completed"}),
    _event_msg({"type": "thread_goal_updated", "goal": {"objective": "o", "status": "active",
                                                        "tokensUsed": 5, "timeUsedSeconds": 9}}),
    _event_msg({"type": "entered_review_mode", "user_facing_hint": "h"}),
    _event_msg({"type": "exited_review_mode", "review_output": {
        "overall_correctness": "ok", "overall_explanation": "e", "findings": [
            {"title": "t", "body": "b", "code_location": {"absolute_file_path": "/r/a",
                                                          "line_range": {"start": 1, "end": 2}}}]}}),
    _event_msg({"type": "item_completed", "turn_id": "t1", "item": {
        "type": "HookPrompt", "fragments": [{"text": "stop", "hookRunId": "stop:1:/h"}]}}),
    _response_item({"type": "message", "role": "assistant", "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "done"}]}),
    _event_msg({"type": "agent_message", "message": "done"}),
    _event_msg({"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": 10, "output_tokens": 2, "reasoning_output_tokens": 1}},
        "rate_limits": {"limit_id": "x"}}),
    _event_msg({"type": "task_complete", "turn_id": "t1", "last_agent_message": "done"}),
    _event_msg({"type": "thread_rolled_back", "num_turns": 1}),
    _event_msg({"type": "turn_aborted", "reason": "interrupted"}),
    _event_msg({"type": "error", "message": "limit"}),
]

ODD_VALUES = [None, 0, -1, 1.5, math.inf, math.nan, True, "", SVG, [], {}, [None], {"a": None}]


def _paths(value, prefix=()):
    """Every (path) to a dict value or list item inside value."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield prefix + (key,)
            yield from _paths(child, prefix + (key,))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield prefix + (i,)
            yield from _paths(child, prefix + (i,))


def _set(root, path, value):
    for key in path[:-1]:
        root = root[key]
    root[path[-1]] = value


def _render(entries) -> str:
    meta, events = extract_conversation(entries)
    return build_html(meta, events)


class FieldFuzzTests(unittest.TestCase):
    def test_every_field_tolerates_odd_values(self) -> None:
        for r, record in enumerate(SESSION):
            for path in list(_paths(record)):
                for value in ODD_VALUES:
                    session = copy.deepcopy(SESSION)
                    _set(session[r], path, value)
                    with self.subTest(record=r, path=path, value=value):
                        html = _render(session)
                        self.assertNotIn(SVG, html)

    def test_records_that_are_not_objects_are_skipped(self) -> None:
        for odd in (None, 1, "x", [1, 2], [SESSION[1]]):
            with self.subTest(odd=odd):
                html = _render([*SESSION[:3], odd, *SESSION[3:]])
                self.assertIn("done", html)


class InjectionTests(unittest.TestCase):
    def test_timestamp_is_escaped(self) -> None:
        entry = {"type": "event_msg", "timestamp": SVG,
                 "payload": {"type": "agent_message", "message": "hi"}}
        html = _render([SESSION[0], entry])
        self.assertNotIn(SVG, html)
        self.assertIn("&lt;svg/onload=x=42&gt;", html)

    def test_rollback_count_is_not_markup(self) -> None:
        html = _render([SESSION[0], _event_msg({"type": "thread_rolled_back",
                                                "num_turns": "<img src=x onerror=1>"})])
        self.assertNotIn("<img src=x", html)
        self.assertIn("Rolled back ? turn(s)", html)


class ReaderTests(unittest.TestCase):
    def test_invalid_utf8_and_partial_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            path.write_bytes(
                b'{"type":"session_meta","payload":{"id":"x"}}\n'
                b'null\n[1]\n'
                b'{"type":"event_msg","timestamp":"t","payload":{"type":"agent_message","message":"bad \xff byte"}}\n'
                b'{"type":"event_msg","payload":{"type":"agent_mess'
            )
            entries = parse_jsonl(path)
        self.assertEqual(len(entries), 2)
        self.assertIn("bad \ufffd byte", entries[1]["payload"]["message"])


class TimestampTests(unittest.TestCase):
    def test_offsets_are_converted_to_utc(self) -> None:
        self.assertEqual(format_ts_full("2026-10-24T12:00:00-04:00"), "2026-10-24 16:00:00 UTC")
        self.assertEqual(format_ts("2026-10-24T12:00:00-04:00"), "16:00:00")

    def test_naive_timestamps_are_not_labelled_utc(self) -> None:
        self.assertEqual(format_ts_full("2026-10-24T12:00:00"), "2026-10-24 12:00:00")

    def test_odd_values(self) -> None:
        for value in (None, 5, [], "garbage"):
            with self.subTest(value=value):
                self.assertIsInstance(format_ts(value), str)
                self.assertIsInstance(format_ts_full(value), str)


if __name__ == "__main__":
    unittest.main()
