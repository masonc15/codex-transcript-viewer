from __future__ import annotations

import unittest

from codex_transcript_viewer.html_builder import build_html


def _html(output: str, **kwargs: object) -> str:
    event = {"type": "tool_output", "ts": "", "call_id": "c", "output": output,
             "attachments": [], "exit_codes": [], "failed": None, "duration": None}
    return build_html({"id": "s"}, [event], **kwargs)


class OutputCapTests(unittest.TestCase):
    def test_output_under_cap_is_complete(self) -> None:
        html = _html("x" * 5000, max_output_chars=10_000)
        self.assertIn("x" * 5000, html)
        self.assertNotIn("characters omitted", html)

    def test_cap_cuts_raw_text_and_counts_exactly(self) -> None:
        output = "a" * 9 + "<&>" + "b" * 88  # 100 chars; HTML specials straddle the cut
        html = _html(output, max_output_chars=10)
        self.assertIn("aaaaaaaaa&lt;</pre>".replace("</pre>", ""), html)
        self.assertIn("[90 characters omitted]", html)
        self.assertNotIn("&amp;lt", html)

    def test_zero_disables_cap(self) -> None:
        html = _html("y" * 300_000, max_output_chars=0)
        self.assertNotIn("characters omitted", html)

    def test_default_cap_is_a_guard(self) -> None:
        self.assertNotIn("characters omitted", _html("z" * 200_664))
        self.assertIn("[50,000 characters omitted]", _html("z" * 300_000))


if __name__ == "__main__":
    unittest.main()
