from __future__ import annotations

import unittest
from collections import Counter

from codex_transcript_viewer.parser import extract_conversation


def _event_msg(payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": "2026-01-01T00:00:00Z", "payload": payload}


def _response_item(payload: dict) -> dict:
    return {"type": "response_item", "timestamp": "2026-01-01T00:00:00Z", "payload": payload}


def _assistant(text: str, phase: str) -> dict:
    return _response_item(
        {
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "content": [{"type": "output_text", "text": text}],
        }
    )


def _tools(n: int) -> list[dict]:
    return [
        _response_item(
            {"type": "function_call", "name": "exec_command", "arguments": "{}", "call_id": f"c{i}"}
        )
        for i in range(n)
    ]


def _token(total: int) -> dict:
    return _event_msg(
        {
            "type": "token_count",
            "info": {"total_token_usage": {"input_tokens": total, "output_tokens": 1}},
        }
    )


class DedupDensityTests(unittest.TestCase):
    def _counts(self, entries: list[dict]) -> Counter:
        _meta, events = extract_conversation(
            [{"type": "session_meta", "payload": {"id": "s"}}, *entries]
        )
        return Counter(event["type"] for event in events)

    def test_commentary_duplicate_merges_at_any_tool_distance(self) -> None:
        for gap in (0, 7, 8, 9, 50):
            with self.subTest(gap=gap):
                counts = self._counts(
                    [
                        _event_msg({"type": "task_started", "turn_id": "t1"}),
                        _event_msg({"type": "agent_message", "message": "checking the parser"}),
                        *_tools(gap),
                        _assistant("checking the parser", "commentary"),
                    ]
                )
                self.assertEqual(counts["agent_commentary"] + counts["assistant_text"], 1)

    def test_repeated_identical_commentary_stays_separate(self) -> None:
        counts = self._counts(
            [
                _event_msg({"type": "task_started", "turn_id": "t1"}),
                _event_msg({"type": "agent_message", "message": "still waiting"}),
                _assistant("still waiting", "commentary"),
                *_tools(3),
                _event_msg({"type": "agent_message", "message": "still waiting"}),
                _assistant("still waiting", "commentary"),
            ]
        )
        self.assertEqual(counts["assistant_text"], 2)
        self.assertEqual(counts["agent_commentary"], 0)

    def test_token_totals_merge_across_tool_events(self) -> None:
        for gap in (0, 8, 50):
            with self.subTest(gap=gap):
                counts = self._counts(
                    [
                        _event_msg({"type": "task_started", "turn_id": "t1"}),
                        _token(100),
                        *_tools(gap),
                        _token(100),
                        _token(200),
                    ]
                )
                self.assertEqual(counts["token_count"], 2)

    def test_token_totals_do_not_merge_across_messages(self) -> None:
        counts = self._counts(
            [
                _event_msg({"type": "task_started", "turn_id": "t1"}),
                _token(100),
                _assistant("progress note", "commentary"),
                _token(100),
            ]
        )
        self.assertEqual(counts["token_count"], 2)

    def test_task_complete_prefix_of_final_answer_is_dropped(self) -> None:
        counts = self._counts(
            [
                _event_msg({"type": "task_started", "turn_id": "t1"}),
                _assistant("Done. Tests pass.\n\nSources: a, b", "final_answer"),
                _event_msg({"type": "task_complete", "last_agent_message": "Done. Tests pass."}),
            ]
        )
        self.assertEqual(counts["assistant_text"], 1)
        self.assertEqual(counts["task_complete"], 0)

    def test_task_complete_with_different_text_is_kept(self) -> None:
        counts = self._counts(
            [
                _event_msg({"type": "task_started", "turn_id": "t1"}),
                _assistant("Done. Tests pass.", "final_answer"),
                _event_msg({"type": "task_complete", "last_agent_message": "Something else."}),
            ]
        )
        self.assertEqual(counts["task_complete"], 1)

    def test_task_complete_repeating_plan_mode_commentary_is_dropped(self) -> None:
        counts = self._counts(
            [
                _event_msg({"type": "task_started", "turn_id": "t1"}),
                _assistant("Levels confirmed.", "commentary"),
                _assistant("<proposed_plan>steps</proposed_plan>", "final_answer"),
                _event_msg({"type": "task_complete", "last_agent_message": "Levels confirmed."}),
            ]
        )
        self.assertEqual(counts["assistant_text"], 2)
        self.assertEqual(counts["task_complete"], 0)


if __name__ == "__main__":
    unittest.main()
