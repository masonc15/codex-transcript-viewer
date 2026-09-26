from __future__ import annotations

import unittest

from codex_transcript_viewer.html_builder import build_html


def _png(n: int) -> str:
    return "data:image/png;base64," + "A" * n


def _output(call_id: str, url: str) -> dict:
    return {"type": "tool_output", "ts": "", "call_id": call_id, "output": "",
            "attachments": [{"kind": "image", "data_url": url, "bytes": len(url) * 3 // 4}],
            "exit_codes": [], "failed": None, "duration": None}


class ImageBudgetTests(unittest.TestCase):
    def test_images_embed_until_budget_then_become_chips(self) -> None:
        events = [_output(f"c{i}", _png(400_000)) for i in range(3)]
        html = build_html({"id": "s"}, events, max_image_mb=1)
        self.assertEqual(html.count('<img src="data:image/png'), 2)
        self.assertIn("[image: image output", html)
        self.assertIn("1 tool image (300 KB) not embedded", html)
        self.assertIn("--max-image-mb 0", html)

    def test_zero_means_no_limit(self) -> None:
        events = [_output(f"c{i}", _png(400_000)) for i in range(3)]
        html = build_html({"id": "s"}, events, max_image_mb=0)
        self.assertEqual(html.count('<img src="data:image/png'), 3)
        self.assertNotIn('class="image-notice"', html)

    def test_prompt_images_ignore_the_budget(self) -> None:
        prompt = {"type": "user_message", "ts": "", "text": "see",
                  "attachments": [{"kind": "image", "data_url": _png(2_000_000), "bytes": 1}]}
        html = build_html({"id": "s"}, [prompt, _output("c", _png(10))], max_image_mb=1)
        self.assertEqual(html.count('<img src="data:image/png'), 2)

    def test_no_images_overrides_budget(self) -> None:
        html = build_html({"id": "s"}, [_output("c", _png(10))], embed_images=False, max_image_mb=0)
        self.assertNotIn("<img", html)


if __name__ == "__main__":
    unittest.main()
