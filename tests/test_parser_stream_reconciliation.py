from __future__ import annotations

import unittest
from collections import Counter

from codex_transcript_viewer.parser import extract_conversation


def _event_msg(ts: str, payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": ts, "payload": payload}


def _response_item(ts: str, payload: dict) -> dict:
    return {"type": "response_item", "timestamp": ts, "payload": payload}


class ParserStreamReconciliationTests(unittest.TestCase):
    def test_prefers_response_items_for_assistant_and_reasoning(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            _event_msg(
                "2026-02-27T10:26:22.100Z",
                {"type": "agent_message", "message": "status update"},
            ),
            _event_msg(
                "2026-02-27T10:26:22.200Z",
                {"type": "agent_reasoning", "text": "thinking summary"},
            ),
            _response_item(
                "2026-02-27T10:26:22.300Z",
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "status update"}],
                },
            ),
            _response_item(
                "2026-02-27T10:26:22.400Z",
                {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "thinking summary"},
                    ],
                },
            ),
            _event_msg(
                "2026-02-27T10:26:22.500Z",
                {"type": "task_complete", "last_agent_message": "final answer"},
            ),
            _response_item(
                "2026-02-27T10:26:22.600Z",
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "final answer"}],
                },
            ),
        ]

        _meta, events = extract_conversation(entries)
        counts = Counter(event["type"] for event in events)

        self.assertEqual(counts["assistant_text"], 2)
        self.assertEqual(counts["reasoning"], 1)
        self.assertEqual(counts["agent_commentary"], 0)
        self.assertEqual(counts["task_complete"], 0)
        self.assertEqual(events[0]["text"], "status update")
        self.assertEqual(events[1]["text"], "thinking summary")
        self.assertEqual(events[2]["text"], "final answer")

    def test_keeps_event_msg_fallback_when_response_items_absent(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            _event_msg(
                "2026-02-27T10:26:22.100Z",
                {"type": "agent_message", "message": "status update"},
            ),
            _event_msg(
                "2026-02-27T10:26:22.200Z",
                {"type": "agent_reasoning", "text": "thinking summary"},
            ),
            _event_msg(
                "2026-02-27T10:26:22.500Z",
                {"type": "task_complete", "last_agent_message": "final answer"},
            ),
        ]

        _meta, events = extract_conversation(entries)
        counts = Counter(event["type"] for event in events)

        self.assertEqual(counts["agent_commentary"], 1)
        self.assertEqual(counts["reasoning"], 1)
        self.assertEqual(counts["task_complete"], 1)
        self.assertEqual(events[0]["text"], "status update")
        self.assertEqual(events[1]["text"], "thinking summary")
        self.assertEqual(events[2]["text"], "final answer")

    def test_preserves_task_complete_when_no_final_response_message(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            _response_item(
                "2026-02-27T10:26:22.300Z",
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "status update"}],
                },
            ),
            _event_msg(
                "2026-02-27T10:26:22.500Z",
                {"type": "task_complete", "last_agent_message": "final answer"},
            ),
        ]

        _meta, events = extract_conversation(entries)
        counts = Counter(event["type"] for event in events)

        self.assertEqual(counts["assistant_text"], 1)
        self.assertEqual(counts["task_complete"], 1)

    def test_collapses_redundant_token_totals_across_limit_ids(self) -> None:
        total_1 = {
            "input_tokens": 100,
            "cached_input_tokens": 25,
            "output_tokens": 12,
            "reasoning_output_tokens": 4,
            "total_tokens": 112,
        }
        total_2 = {
            "input_tokens": 150,
            "cached_input_tokens": 50,
            "output_tokens": 20,
            "reasoning_output_tokens": 7,
            "total_tokens": 170,
        }

        def token_payload(total: dict, limit_id: str) -> dict:
            return {
                "type": "token_count",
                "info": {"total_token_usage": total},
                "rate_limits": {"limit_id": limit_id},
            }

        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            _event_msg("2026-02-27T10:26:22.100Z", token_payload(total_1, "codex")),
            _event_msg(
                "2026-02-27T10:26:22.101Z",
                token_payload(total_1, "codex_bengalfox"),
            ),
            _event_msg("2026-02-27T10:26:22.102Z", token_payload(total_1, "codex")),
            _event_msg(
                "2026-02-27T10:26:22.200Z",
                {"type": "agent_message", "message": "status update"},
            ),
            _event_msg(
                "2026-02-27T10:26:22.300Z",
                token_payload(total_2, "codex_bengalfox"),
            ),
            _event_msg("2026-02-27T10:26:22.301Z", token_payload(total_2, "codex")),
        ]

        _meta, events = extract_conversation(entries)
        token_events = [event for event in events if event["type"] == "token_count"]

        self.assertEqual(len(token_events), 2)
        self.assertEqual(token_events[0]["total"], total_1)
        self.assertEqual(token_events[1]["total"], total_2)


if __name__ == "__main__":
    unittest.main()
