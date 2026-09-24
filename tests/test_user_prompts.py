from __future__ import annotations

import json
import unittest

import codex_transcript_viewer.parser as parser
from codex_transcript_viewer.parser import extract_conversation


def _event_msg(payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": "2026-09-26T19:00:00Z", "payload": payload}


def _response_item(payload: dict) -> dict:
    return {"type": "response_item", "timestamp": "2026-09-26T19:00:00Z", "payload": payload}


def _user_item(content: list, **extra: object) -> dict:
    item = {"type": "UserMessage", "id": "item-1", "content": content, **extra}
    return _event_msg({"type": "item_completed", "turn_id": "turn-1", "item": item})


def _injected(text: str) -> dict:
    return _response_item(
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
    )


def _prompts(entries: list[dict]) -> list[dict]:
    _meta, events = extract_conversation(
        [{"type": "session_meta", "payload": {"id": "s"}}, *entries]
    )
    return [event for event in events if event["type"] == "user_message"]


class ModernUserPromptTests(unittest.TestCase):
    def test_reads_typed_prompts_and_ignores_injected_input(self) -> None:
        prompts = _prompts(
            [
                _event_msg({"type": "task_started", "turn_id": "turn-1"}),
                _injected("<environment_context><cwd>/tmp</cwd></environment_context>"),
                _injected("# AGENTS.md instructions for /tmp"),
                _response_item(
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "<permissions/>"}],
                    }
                ),
                _injected("fix the flaky test"),
                _user_item(
                    [{"type": "text", "text": "fix the flaky test\n", "text_elements": []}],
                    client_id="client-1",
                ),
            ]
        )

        self.assertEqual([p["text"] for p in prompts], ["fix the flaky test"])
        self.assertEqual(prompts[0]["turn_id"], "turn-1")
        self.assertEqual(prompts[0]["item_id"], "item-1")
        self.assertEqual(prompts[0]["client_id"], "client-1")

    def test_client_id_is_optional(self) -> None:
        prompts = _prompts([_user_item([{"type": "text", "text": "hello"}])])
        self.assertNotIn("client_id", prompts[0])

    def test_attachments_keep_type_and_order_without_touching_text(self) -> None:
        data_url = "data:image/png;base64," + "A" * 400
        prompts = _prompts(
            [
                _user_item(
                    [
                        {"type": "skill", "name": "git-storytime", "path": "/s/SKILL.md"},
                        {"type": "text", "text": "use $git-storytime on this"},
                        {"type": "local_image", "path": "/var/folders/x/T/shot.png"},
                        {"type": "image", "image_url": data_url},
                        {"type": "mention", "name": "Computer Use", "path": "plugin://cu"},
                    ]
                )
            ]
        )

        prompt = prompts[0]
        self.assertEqual(prompt["text"], "use $git-storytime on this")
        self.assertEqual(
            [a["kind"] for a in prompt["attachments"]],
            ["skill", "local_image", "image", "mention"],
        )
        self.assertEqual(prompt["attachments"][2]["bytes"], 300)
        self.assertNotIn("base64", prompt["text"])

    def test_attachment_only_prompt_is_kept(self) -> None:
        prompts = _prompts([_user_item([{"type": "local_image", "path": "/tmp/a.png"}])])
        self.assertEqual(prompts[0]["text"], "")
        self.assertEqual(prompts[0]["attachments"], [{"kind": "local_image", "path": "/tmp/a.png"}])

    def test_other_item_kinds_are_ignored(self) -> None:
        entries = [
            _event_msg(
                {
                    "type": "item_completed",
                    "item": {"type": "AgentMessage", "content": [{"type": "Text", "text": "x"}]},
                }
            )
        ]
        self.assertEqual(_prompts(entries), [])

    def test_malformed_items_do_not_crash(self) -> None:
        for item in (None, "text", [], {"type": "UserMessage"}, {"type": "UserMessage", "content": "x"},
                     {"type": "UserMessage", "content": [None, 3, {"type": "text", "text": None}]}):
            with self.subTest(item=item):
                entries = [_event_msg({"type": "item_completed", "item": item})]
                self.assertEqual(_prompts(entries), [])

    def test_prompt_joins_current_turn_without_starting_one(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {"id": "s"}},
            _event_msg({"type": "task_started", "turn_id": "turn-1"}),
            _user_item([{"type": "text", "text": "first"}]),
        ]
        raw: list[dict] = []
        turn_seq = 0
        for entry in entries[1:]:
            payload = entry["payload"]
            if payload.get("type") == "task_started":
                turn_seq += 1
            parser._handle_event_msg(payload, entry["timestamp"], raw, turn_seq)
        prompt = [e for e in raw if e["type"] == "user_message"][0]
        self.assertEqual(prompt["_turn_seq"], 1)


class LegacyUserPromptTests(unittest.TestCase):
    def test_legacy_prompt_unchanged_with_attachments(self) -> None:
        prompts = _prompts(
            [
                _event_msg(
                    {
                        "type": "user_message",
                        "message": "look at this",
                        "local_images": ["/tmp/a.png"],
                    }
                )
            ]
        )
        self.assertEqual(prompts[0]["text"], "look at this")
        self.assertEqual(prompts[0]["images"], ["/tmp/a.png"])
        self.assertEqual(prompts[0]["attachments"], [{"kind": "local_image", "path": "/tmp/a.png"}])

    def test_item_prompt_shadows_legacy_prompt_in_same_turn(self) -> None:
        prompts = _prompts(
            [
                _event_msg({"type": "task_started", "turn_id": "turn-1"}),
                _event_msg({"type": "user_message", "message": "same words"}),
                _user_item([{"type": "text", "text": "same words"}]),
                _event_msg({"type": "task_started", "turn_id": "turn-2"}),
                _event_msg({"type": "user_message", "message": "legacy only"}),
            ]
        )
        self.assertEqual([p["text"] for p in prompts], ["same words", "legacy only"])

    def test_repeated_identical_prompts_stay_separate(self) -> None:
        prompts = _prompts(
            [
                _event_msg({"type": "task_started", "turn_id": "turn-1"}),
                _user_item([{"type": "text", "text": "try again"}]),
                _event_msg({"type": "task_started", "turn_id": "turn-2"}),
                _user_item([{"type": "text", "text": "try again"}]),
            ]
        )
        self.assertEqual(len(prompts), 2)


if __name__ == "__main__":
    unittest.main()
