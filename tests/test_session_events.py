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


class GoalTests(unittest.TestCase):
    def test_counter_only_updates_are_dropped(self) -> None:
        goals = [
            e for e in _events(
                _turn(),
                _goal("ship it", "active", 0),
                _goal("ship it", "active", 500),
                _goal("ship it", "active", 900),
                _goal("ship it", "complete", 1200),
                _goal("ship it", "complete", 1300),
                _goal("next thing", "active", 0),
            )
            if e["type"] == "goal_updated"
        ]
        self.assertEqual(
            [(g["status"], g["new_objective"]) for g in goals],
            [("active", True), ("complete", False), ("active", True)],
        )

    def test_goal_rows_render(self) -> None:
        html = _html(_turn(), _goal("ship **it**", "active"), _goal("ship **it**", "budgetLimited", 42))
        self.assertIn("Goal set: ship **it**", html)
        self.assertEqual(html.count("<strong>it</strong>"), 1)
        self.assertIn("Goal stopped at its budget", html)
        self.assertIn("42 tokens, 1m 30s", html)
        self.assertIn('data-kind="goal"', html)


class ReviewTests(unittest.TestCase):
    OUTPUT = {
        "findings": [
            {
                "title": "[P1] Popup closes",
                "body": "Chrome focuses new windows.",
                "priority": 1,
                "code_location": {"absolute_file_path": "/r/popup.js",
                                  "line_range": {"start": 52, "end": 56}},
            }
        ],
        "overall_correctness": "patch is incorrect",
        "overall_explanation": "One blocker.",
    }

    def test_modern_review_items(self) -> None:
        events = _events(
            _turn(),
            _item({"type": "EnteredReviewMode", "id": "a", "user_facing_hint": "changes against 'main'"}),
            _item({"type": "ExitedReviewMode", "id": "b", "review_output": self.OUTPUT}),
        )
        started, finished = [e for e in events if e["type"].startswith("review_")]
        self.assertEqual(started["hint"], "changes against 'main'")
        self.assertEqual(finished["verdict"], "patch is incorrect")
        self.assertEqual(finished["findings"][0]["location"], "/r/popup.js:52-56")
        self.assertNotIn("repeated_by_reply", finished)

    def test_findings_render_when_no_reply_repeats_them(self) -> None:
        html = _html(
            _turn(),
            _event_msg({"type": "entered_review_mode", "prompt": "review current changes"}),
            _event_msg({"type": "exited_review_mode", "review_output": self.OUTPUT}),
            _event_msg({"type": "user_message", "message": "fix it"}),
        )
        self.assertIn("Review started: review current changes", html)
        self.assertIn("Review done: patch is incorrect, 1 finding", html)
        self.assertIn("[P1] Popup closes", html)
        self.assertIn("/r/popup.js:52-56", html)

    def test_findings_point_to_reply_that_repeats_them(self) -> None:
        html = _html(
            _turn(),
            _event_msg({"type": "exited_review_mode", "review_output": self.OUTPUT}),
            _response_item({"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": "Review: popup closes."}]}),
        )
        self.assertIn("The full review is in the reply below.", html)
        self.assertNotIn("Chrome focuses new windows.", html)
        self.assertNotIn("One blocker.", html)


class HookAndErrorTests(unittest.TestCase):
    def test_hook_prompt_renders(self) -> None:
        html = _html(
            _turn(),
            _item({"type": "HookPrompt", "id": "h", "fragments": [
                {"text": "YOUR PLAN WAS NOT APPROVED.", "hookRunId": "stop:1:/h/hooks.json"}]}),
        )
        self.assertIn("stop hook: YOUR PLAN WAS NOT APPROVED.", html)
        self.assertIn('data-kind="hook"', html)

    def test_error_renders(self) -> None:
        html = _html(_turn(), _event_msg({"type": "error", "message": "You've hit your usage limit.",
                                          "codex_error_info": "usage_limit_exceeded"}))
        self.assertIn("usage limit", html)
        self.assertIn("tree-role-error", html)


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


class TurnSettingsTests(unittest.TestCase):
    def _context(self, model: str, effort: str) -> dict:
        return {"type": "turn_context", "timestamp": "2026-09-27T01:00:00Z",
                "payload": {"model": model, "effort": effort, "cwd": "/x"}}

    def test_only_changes_are_kept_and_header_shows_first_model(self) -> None:
        entries = [
            _turn(), self._context("gpt-5.6-sol", "high"),
            _turn(), self._context("gpt-5.6-sol", "high"),
            _turn(), self._context("gpt-6-astra", "high"),
            _turn(), self._context("gpt-6-astra", "xhigh"),
        ]
        settings = [e for e in _events(*entries) if e["type"] == "turn_settings"]
        self.assertEqual([(s["model"], s["effort"]) for s in settings],
                         [("gpt-5.6-sol", "high"), ("gpt-6-astra", "high"), ("gpt-6-astra", "xhigh")])
        html = _html(*entries)
        self.assertIn('<span class="info-value">gpt-5.6-sol (high effort)</span>', html)
        self.assertIn("Switched model gpt-5.6-sol \u2192 gpt-6-astra", html)
        self.assertIn("Switched effort high \u2192 xhigh", html)
        self.assertIn('data-kind="settings"', html)

    def test_header_falls_back_to_provider(self) -> None:
        meta, events = extract_conversation(
            [{"type": "session_meta", "payload": {"id": "s", "model_provider": "openai"}}, _turn()]
        )
        self.assertIn('<span class="info-value">openai</span>', build_html(meta, events))


class RecordKindTests(unittest.TestCase):
    def test_new_kinds_are_handled_or_ignored(self) -> None:
        entries = [
            _event_msg({"type": kind}) for kind in (
                "thread_goal_updated", "entered_review_mode", "exited_review_mode", "error",
                "guardian_assessment", "collab_agent_spawn_end", "collab_waiting_end",
                "collab_close_end", "undo_completed", "image_generation_end")
        ] + [
            _item({"type": kind}) for kind in ("EnteredReviewMode", "ExitedReviewMode", "HookPrompt")
        ] + [
            {"type": "realtime_item", "payload": {"type": "realtime_session_started"}},
            {"type": "turn_context", "payload": {"model": "m"}},
            _response_item({"type": "image_generation_call"}),
        ]
        self.assertEqual(unrecognized_record_kinds(entries), Counter())


if __name__ == "__main__":
    unittest.main()
