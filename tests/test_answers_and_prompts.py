from __future__ import annotations

import time
import unittest

from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import extract_conversation

TS = "2026-09-27T01:00:00Z"
PNG = "data:image/png;base64,iVBORw0KGgo="


def _event_msg(payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": TS, "payload": payload}


def _response(text: str, phase: str | None = None, *more: str) -> dict:
    payload = {"type": "message", "role": "assistant",
               "content": [{"type": "output_text", "text": t} for t in (text, *more)]}
    if phase is not None:
        payload["phase"] = phase
    return {"type": "response_item", "timestamp": TS, "payload": payload}


def _turn(turn_id: str = "t") -> dict:
    return _event_msg({"type": "task_started", "turn_id": turn_id})


def _prompt(text: str = "q") -> dict:
    return _event_msg({"type": "user_message", "message": text})


def _tool_call() -> dict:
    return {"type": "response_item", "timestamp": TS, "payload": {
        "type": "function_call", "name": "exec_command", "arguments": "{}", "call_id": "c"}}


META = {"type": "session_meta", "payload": {"id": "s"}}


def _events(*entries: dict) -> list[dict]:
    return extract_conversation([META, *entries])[1]


def _shown(events: list[dict]) -> list[tuple[str, str, bool]]:
    return [(e["type"], e["text"], bool(e.get("final") or e.get("phase") == "final_answer"
                                        or e["type"] == "task_complete"))
            for e in events if e["type"] in ("assistant_text", "agent_commentary", "task_complete")]


def _answer_nodes(*entries: dict) -> int:
    meta, events = extract_conversation([META, *entries])
    return build_html(meta, events).count('data-kind="final-answer"')


class UnphasedAnswerTests(unittest.TestCase):
    def test_task_complete_marks_its_unphased_response_final(self) -> None:
        events = _events(_turn(), _prompt(), _response("Answer"),
                         _event_msg({"type": "task_complete", "last_agent_message": "Answer"}))
        self.assertEqual(_shown(events), [("assistant_text", "Answer", True)])

    def test_last_unphased_message_of_a_turn_is_the_answer(self) -> None:
        entries = (_turn(), _prompt(), _response("Looking."), _tool_call(), _response("Found it."),
                   _turn("t2"), _prompt("again"), _response("Second."))
        self.assertEqual(
            _shown(_events(*entries)),
            [("assistant_text", "Looking.", False), ("assistant_text", "Found it.", True),
             ("assistant_text", "Second.", True)],
        )
        self.assertEqual(_answer_nodes(*entries), 2)

    def test_sessions_without_turn_records_split_at_prompts(self) -> None:
        events = _events(_prompt("a"), _response("one"), _prompt("b"), _response("two"))
        self.assertEqual([final for _, _, final in _shown(events)], [True, True])

    def test_message_followed_by_a_tool_call_is_not_the_answer(self) -> None:
        events = _events(_turn(), _prompt(), _response("Running it."), _tool_call())
        self.assertEqual(_shown(events), [("assistant_text", "Running it.", False)])

    def test_aborted_turn_has_no_answer(self) -> None:
        events = _events(_turn(), _prompt(), _response("Partial."),
                         _event_msg({"type": "turn_aborted", "reason": "interrupted"}))
        self.assertEqual(_shown(events), [("assistant_text", "Partial.", False)])

    def test_phased_commentary_is_never_promoted(self) -> None:
        events = _events(_turn(), _prompt(), _response("Working.", "commentary"))
        self.assertEqual(_shown(events), [("assistant_text", "Working.", False)])


class EventOnlyTests(unittest.TestCase):
    def test_task_complete_repeating_an_agent_message_shows_once(self) -> None:
        events = _events(_turn(), _prompt(), _event_msg({"type": "agent_message", "message": "Done."}),
                         _event_msg({"type": "task_complete", "last_agent_message": "Done."}))
        self.assertEqual(_shown(events), [("agent_commentary", "Done.", True)])

    def test_different_task_complete_text_is_kept(self) -> None:
        events = _events(_turn(), _prompt(), _event_msg({"type": "agent_message", "message": "Step."}),
                         _event_msg({"type": "task_complete", "last_agent_message": "Other."}))
        self.assertEqual([t for t, _, _ in _shown(events)], ["agent_commentary", "task_complete"])


class MultiBlockTests(unittest.TestCase):
    def test_blocks_of_one_message_dedupe_against_their_copies(self) -> None:
        events = _events(_turn(), _prompt(), _response("first", "final_answer", "second"),
                         _event_msg({"type": "agent_message", "message": "first\nsecond"}),
                         _event_msg({"type": "task_complete", "last_agent_message": "first\nsecond"}))
        shown = _shown(events)
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0][0], "assistant_text")
        self.assertIn("first", shown[0][1])
        self.assertIn("second", shown[0][1])


class MixedPromptTests(unittest.TestCase):
    def _item_prompt(self, text: str, image: bool = False) -> dict:
        content = [{"type": "text", "text": text}]
        if image:
            content.append({"type": "local_image", "path": "/tmp/gone.png"})
        return _event_msg({"type": "item_completed", "turn_id": "t",
                           "item": {"type": "UserMessage", "id": "u", "content": content}})

    def _model_image(self) -> dict:
        return {"type": "response_item", "timestamp": TS, "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_image", "image_url": PNG}]}}

    def test_legacy_copy_of_an_item_prompt_is_dropped(self) -> None:
        events = _events(_turn(), _prompt("same"), self._item_prompt("same"))
        prompts = [e for e in events if e["type"] == "user_message"]
        self.assertEqual([p["text"] for p in prompts], ["same"])

    def test_different_legacy_prompt_is_kept(self) -> None:
        events = _events(_turn(), _prompt("steer left"), self._item_prompt("first"))
        prompts = sorted(e["text"] for e in events if e["type"] == "user_message")
        self.assertEqual(prompts, ["first", "steer left"])

    def test_image_goes_to_the_surviving_prompt(self) -> None:
        legacy = _event_msg({"type": "user_message", "message": "look", "local_images": ["/tmp/gone.png"]})
        events = _events(_turn(), legacy, self._item_prompt("look", image=True), self._model_image())
        prompts = [e for e in events if e["type"] == "user_message"]
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["attachments"][0].get("data_url"), PNG)


class SubagentSettingsTests(unittest.TestCase):
    CHILD = "01990000-1000-7000-8000-000000000000"
    PARENT_TURN = "01980000-0000-7000-8000-000000000001"
    PARENT_TURN_2 = "01980000-0000-7000-8000-000000000003"
    CHILD_TURN = "01990000-2000-7000-8000-000000000002"

    def _settings(self, model: str) -> dict:
        return {"type": "turn_context", "timestamp": TS, "payload": {"model": model, "effort": "high"}}

    def test_child_keeps_its_own_first_model(self) -> None:
        meta = {"type": "session_meta", "payload": {"id": self.CHILD, "source": {"subagent": "x"}}}
        entries = [meta,
                   _turn(self.PARENT_TURN), self._settings("A"),
                   _turn(self.PARENT_TURN_2), self._settings("B"),
                   _turn(self.CHILD_TURN), self._settings("B"),
                   _turn("01990000-3000-7000-8000-000000000004"), self._settings("C")]
        meta, events = extract_conversation(entries)
        html = build_html(meta, events)
        self.assertIn('<span class="info-value">B (high effort)</span>', html)


class ReconciliationScaleTests(unittest.TestCase):
    def test_long_turns_stay_linear(self) -> None:
        entries = [_turn()]
        entries += [_response(f"r{i}", "commentary") for i in range(4000)]
        entries += [_event_msg({"type": "agent_message", "message": f"e{i}"}) for i in range(4000)]
        start = time.perf_counter()
        events = _events(*entries)
        self.assertLess(time.perf_counter() - start, 3.0)
        self.assertEqual(len(_shown(events)), 8000)

    def test_matching_still_prefers_the_nearest_copy(self) -> None:
        events = _events(_turn(), _response("Same", "commentary"),
                         _event_msg({"type": "agent_message", "message": "Same"}),
                         _response("Same", "commentary"),
                         _event_msg({"type": "agent_message", "message": "Same"}))
        self.assertEqual([t for t, _, _ in _shown(events)], ["assistant_text", "assistant_text"])


if __name__ == "__main__":
    unittest.main()
