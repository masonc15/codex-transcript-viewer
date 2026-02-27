from __future__ import annotations

import unittest

from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import extract_conversation


class NullPayloadFieldsTests(unittest.TestCase):
    def test_extract_conversation_normalizes_nullable_text_fields(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            {
                "type": "event_msg",
                "timestamp": "2026-02-27T05:25:47Z",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": None,
                    "turn_id": None,
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-02-27T05:25:47Z",
                "payload": {
                    "type": "agent_message",
                    "message": None,
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-27T05:25:48Z",
                "payload": {
                    "type": "function_call",
                    "name": None,
                    "arguments": None,
                    "call_id": None,
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-27T05:25:49Z",
                "payload": {
                    "type": "function_call_output",
                    "output": None,
                    "call_id": None,
                },
            },
        ]

        _meta, events = extract_conversation(entries)
        # Empty task_complete messages are now suppressed to avoid blank "final answer" blocks.
        event_types = [event["type"] for event in events]
        self.assertNotIn("task_complete", event_types)
        self.assertEqual(events[0]["text"], "")
        self.assertEqual(events[1]["name"], "")
        self.assertEqual(events[1]["arguments"], "")
        self.assertEqual(events[1]["call_id"], "")
        self.assertEqual(events[2]["output"], "")

    def test_build_html_handles_task_complete_with_null_message(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            {
                "type": "event_msg",
                "timestamp": "2026-02-27T05:25:47Z",
                "payload": {"type": "task_complete", "last_agent_message": None},
            },
        ]

        meta, events = extract_conversation(entries)
        html = build_html(meta, events)
        self.assertIsInstance(html, str)
        self.assertNotIn('assistant-message final-answer" id="msg-', html)


if __name__ == "__main__":
    unittest.main()
