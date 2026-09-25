from __future__ import annotations

import unittest

from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import extract_conversation

PNG_A = "data:image/png;base64,iVBORw0KGgoAAAA"
PNG_B = "data:image/jpeg;base64,/9j/4AAQSkZJRgAB"


def _event_msg(payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": "2026-09-26T19:00:00Z", "payload": payload}


def _model_input(*urls: str) -> dict:
    content = [{"type": "input_text", "text": "<image>"}]
    content += [{"type": "input_image", "image_url": url} for url in urls]
    return {
        "type": "response_item",
        "timestamp": "2026-09-26T19:00:00Z",
        "payload": {"type": "message", "role": "user", "content": content},
    }


def _prompt(*blocks: dict) -> dict:
    return _event_msg(
        {"type": "item_completed", "item": {"type": "UserMessage", "id": "i", "content": list(blocks)}}
    )


def _events(entries: list[dict]) -> list[dict]:
    _meta, events = extract_conversation(
        [{"type": "session_meta", "payload": {"id": "s"}},
         _event_msg({"type": "task_started", "turn_id": "t1"}), *entries]
    )
    return events


def _prompts(entries: list[dict]) -> list[dict]:
    return [e for e in _events(entries) if e["type"] == "user_message"]


class PromptImageParsingTests(unittest.TestCase):
    def test_images_attach_in_order(self) -> None:
        prompt = _prompts(
            [
                _prompt(
                    {"type": "text", "text": "compare these"},
                    {"type": "local_image", "path": "/var/folders/x/T/one.png"},
                    {"type": "local_image", "path": "/var/folders/x/T/two.jpg"},
                ),
                _model_input(PNG_A, PNG_B),
            ]
        )[0]
        self.assertEqual([a["data_url"] for a in prompt["attachments"]], [PNG_A, PNG_B])

    def test_prompt_without_images_is_unchanged(self) -> None:
        prompt = _prompts([_prompt({"type": "text", "text": "hi"}), _model_input(PNG_A)])[0]
        self.assertEqual(prompt["attachments"], [])

    def test_missing_model_copy_leaves_attachment_without_data(self) -> None:
        prompt = _prompts(
            [
                _prompt(
                    {"type": "local_image", "path": "/tmp/a.png"},
                    {"type": "local_image", "path": "/tmp/b.png"},
                ),
                _model_input(PNG_A),
            ]
        )[0]
        self.assertEqual(prompt["attachments"][0]["data_url"], PNG_A)
        self.assertNotIn("data_url", prompt["attachments"][1])

    def test_model_input_text_is_never_a_prompt(self) -> None:
        events = _events([_model_input(PNG_A)])
        self.assertEqual([e for e in events if e["type"] == "user_message"], [])
        self.assertFalse(any(e["type"].startswith("_") for e in events))

    def test_legacy_local_images_get_model_copies(self) -> None:
        prompt = _prompts(
            [
                _event_msg({"type": "user_message", "message": "see", "local_images": ["/tmp/a.png"]}),
                _model_input(PNG_A),
            ]
        )[0]
        self.assertEqual(prompt["attachments"][0]["data_url"], PNG_A)


class PromptImageRenderingTests(unittest.TestCase):
    def _html(self, attachments: list[dict], **kwargs: object) -> str:
        event = {"type": "user_message", "ts": "", "text": "look", "attachments": attachments}
        return build_html({"id": "s"}, [event], **kwargs)

    def test_embeds_thumbnail_with_basename_caption(self) -> None:
        html = self._html([{"kind": "local_image", "path": "/Users/me/Desktop/shot.png",
                            "data_url": PNG_A, "bytes": 12}])
        self.assertIn(f'<img src="{PNG_A}"', html)
        self.assertIn("<figcaption>shot.png</figcaption>", html)
        self.assertNotIn("/Users/me", html)

    def test_no_images_renders_chip(self) -> None:
        html = self._html([{"kind": "local_image", "path": "/tmp/shot.png",
                            "data_url": PNG_A, "bytes": 212_000}], embed_images=False)
        self.assertNotIn("<img", html)
        self.assertIn("[image: shot.png, 212 KB]", html)

    def test_invalid_data_url_is_not_embedded(self) -> None:
        html = self._html([{"kind": "image", "data_url": 'data:image/png;base64,AA" onerror="x', "bytes": 3}])
        self.assertNotIn("onerror", html)
        self.assertIn("[image: pasted image", html)

    def test_hostile_file_name_is_escaped(self) -> None:
        html = self._html([{"kind": "local_image", "path": "/tmp/<script>x.png", "data_url": PNG_A}])
        self.assertNotIn("<script>x.png", html)
        self.assertIn("&lt;script&gt;x.png", html)

    def test_skill_and_mention_chips(self) -> None:
        html = self._html([{"kind": "skill", "name": "git-storytime", "path": "p"},
                           {"kind": "mention", "name": "Computer Use", "path": "p"}])
        self.assertIn("[$git-storytime]", html)
        self.assertIn("[@Computer Use]", html)

    def test_attachment_only_prompt_is_not_blank(self) -> None:
        event = {"type": "user_message", "ts": "", "text": "",
                 "attachments": [{"kind": "local_image", "path": "/tmp/a.png", "data_url": PNG_A}]}
        html = build_html({"id": "s"}, [event])
        self.assertIn("(attachment only)", html)
        self.assertIn("[attachment only]", html)


if __name__ == "__main__":
    unittest.main()
