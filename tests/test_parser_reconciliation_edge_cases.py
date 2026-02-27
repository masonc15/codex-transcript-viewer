from __future__ import annotations

import unittest

from codex_transcript_viewer.parser import extract_conversation


def _event_msg(ts: str, payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": ts, "payload": payload}


def _response_item(ts: str, payload: dict) -> dict:
    return {"type": "response_item", "timestamp": ts, "payload": payload}


class ParserReconciliationEdgeCaseTests(unittest.TestCase):
    def test_mixed_stream_keeps_unmatched_event_msg_content(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            _event_msg(
                "2026-01-01T00:00:01Z",
                {"type": "agent_message", "message": "early commentary"},
            ),
            _event_msg(
                "2026-01-01T00:00:02Z",
                {"type": "agent_reasoning", "text": "early reasoning"},
            ),
            _response_item(
                "2026-01-01T00:00:03Z",
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "late commentary"}],
                },
            ),
        ]

        _meta, events = extract_conversation(entries)
        event_texts = [event.get("text", "") for event in events]
        self.assertIn("early commentary", event_texts)
        self.assertIn("early reasoning", event_texts)
        self.assertIn("late commentary", event_texts)

    def test_task_complete_not_globally_suppressed(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            _response_item(
                "2026-01-01T00:00:01Z",
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "turn 1 final"}],
                },
            ),
            _event_msg(
                "2026-01-01T00:00:02Z",
                {"type": "task_started", "turn_id": "turn-2"},
            ),
            _event_msg(
                "2026-01-01T00:00:03Z",
                {"type": "task_complete", "last_agent_message": "turn 2 final"},
            ),
        ]

        _meta, events = extract_conversation(entries)
        self.assertTrue(
            any(
                event["type"] == "task_complete"
                and event.get("text") == "turn 2 final"
                for event in events
            )
        )

    def test_token_count_merges_limit_ids_without_cross_turn_loss(self) -> None:
        total = {
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 10,
            "reasoning_output_tokens": 5,
            "total_tokens": 110,
        }
        entries = [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            _event_msg(
                "2026-01-01T00:00:01Z",
                {
                    "type": "token_count",
                    "info": {"total_token_usage": total},
                    "rate_limits": {"limit_id": "codex"},
                },
            ),
            _event_msg(
                "2026-01-01T00:00:02Z",
                {
                    "type": "token_count",
                    "info": {"total_token_usage": total},
                    "rate_limits": {"limit_id": "codex_bengalfox"},
                },
            ),
            _event_msg(
                "2026-01-01T00:00:03Z",
                {"type": "task_started", "turn_id": "turn-2"},
            ),
            _event_msg(
                "2026-01-01T00:00:04Z",
                {
                    "type": "token_count",
                    "info": {"total_token_usage": total},
                    "rate_limits": {"limit_id": "codex"},
                },
            ),
        ]

        _meta, events = extract_conversation(entries)
        token_events = [event for event in events if event["type"] == "token_count"]
        self.assertEqual(len(token_events), 2)
        self.assertEqual(
            token_events[0].get("rate_limit_ids"),
            ["codex", "codex_bengalfox"],
        )
        self.assertEqual(token_events[1].get("rate_limit_ids"), ["codex"])


if __name__ == "__main__":
    unittest.main()
