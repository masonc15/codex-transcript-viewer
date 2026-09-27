from __future__ import annotations

import unittest
from collections import Counter

from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import extract_conversation, unrecognized_record_kinds


def _event_msg(payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": "2026-09-27T01:00:00Z", "payload": payload}


def _response_item(payload: dict) -> dict:
    return {"type": "response_item", "timestamp": "2026-09-27T01:00:00Z", "payload": payload}


def _item(item: dict) -> dict:
    return _event_msg({"type": "item_completed", "turn_id": "t1", "item": item})


def _reasoning(*parts: str) -> dict:
    return _response_item(
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": p} for p in parts]}
    )


def _turn() -> dict:
    return _event_msg({"type": "task_started", "turn_id": "t"})


def _events(*entries: dict) -> list[dict]:
    _meta, events = extract_conversation([{"type": "session_meta", "payload": {"id": "s"}}, *entries])
    return events


def _html(*entries: dict, **kwargs) -> str:
    meta, events = extract_conversation([{"type": "session_meta", "payload": {"id": "s"}}, *entries])
    return build_html(meta, events, **kwargs)


def _goal(objective: str, status: str, tokens: int = 0) -> dict:
    return _event_msg(
        {
            "type": "thread_goal_updated",
            "threadId": "s",
            "goal": {"objective": objective, "status": status, "tokensUsed": tokens,
                     "timeUsedSeconds": 90},
        }
    )


class ReasoningSnapshotTests(unittest.TestCase):
    def _reasoning_texts(self, *entries: dict) -> list[str]:
        return [e["text"] for e in _events(*entries) if e["type"] == "reasoning"]

    def test_cumulative_snapshots_show_each_heading_once(self) -> None:
        texts = self._reasoning_texts(
            _turn(),
            _reasoning("A"),
            _reasoning("A", "B"),
            _reasoning("A", "B"),
            _reasoning("A", "B", "C"),
        )
        self.assertEqual(texts, ["A", "B", "C"])

    def test_new_summary_that_does_not_repeat_is_kept_whole(self) -> None:
        texts = self._reasoning_texts(_turn(), _reasoning("A", "B"), _reasoning("B", "C"))
        self.assertEqual(texts, ["A", "B", "B", "C"])

    def test_snapshots_do_not_carry_across_turns(self) -> None:
        texts = self._reasoning_texts(_turn(), _reasoning("A"), _turn(), _reasoning("A", "B"))
        self.assertEqual(texts, ["A", "A", "B"])

    def test_empty_summary_does_not_break_the_chain(self) -> None:
        texts = self._reasoning_texts(
            _turn(), _reasoning("A"), _reasoning(), _reasoning("A", "B")
        )
        self.assertEqual(texts, ["A", "B"])

    def test_event_copies_still_match_collapsed_parts(self) -> None:
        texts = self._reasoning_texts(
            _turn(),
            _event_msg({"type": "agent_reasoning", "text": "A"}),
            _reasoning("A"),
            _event_msg({"type": "agent_reasoning", "text": "B"}),
            _reasoning("A", "B"),
        )
        self.assertEqual(texts, ["A", "B"])


class ImageGenerationTests(unittest.TestCase):
    PNG = "iVBORw0KGgo" + "A" * 400

    def _call(self, result: str | None, status: str = "completed") -> dict:
        payload = {"type": "image_generation_call", "id": "ig_1", "status": status,
                   "revised_prompt": "A lighthouse at dusk"}
        if result is not None:
            payload["result"] = result
        return _response_item(payload)

    def test_generated_image_is_a_tool_call_with_image_output(self) -> None:
        events = _events(_turn(), self._call(self.PNG))
        call = next(e for e in events if e["type"] == "tool_call")
        output = next(e for e in events if e["type"] == "tool_output")
        self.assertEqual((call["name"], call["arguments"]), ("image_generation", "A lighthouse at dusk"))
        self.assertEqual(output["call_id"], call["call_id"])
        self.assertTrue(output["attachments"][0]["data_url"].startswith("data:image/png;base64,"))
        self.assertIs(output["failed"], False)

    def test_generated_image_renders_within_budget(self) -> None:
        html = _html(_turn(), self._call(self.PNG))
        self.assertIn("<img src=\"data:image/png;base64,", html)
        self.assertIn("<figcaption>generated image</figcaption>", html)
        html = _html(_turn(), self._call(self.PNG), max_image_mb=0.0001)
        self.assertNotIn("<img src=", html)
        self.assertIn("1 tool image", html)

    def test_missing_result_is_not_success(self) -> None:
        events = _events(_turn(), self._call(None, status="failed"))
        output = next(e for e in events if e["type"] == "tool_output")
        self.assertEqual(output["attachments"], [])
        self.assertIs(output["failed"], True)


if __name__ == "__main__":
    unittest.main()
