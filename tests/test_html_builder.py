from __future__ import annotations

import unittest

from codex_transcript_viewer.html_builder import build_html


class HtmlBuilderTests(unittest.TestCase):
    def test_tool_call_renders_all_parameters(self) -> None:
        html = build_html(
            {"id": "session-1"},
            [
                {
                    "type": "tool_call",
                    "ts": "2026-05-01T14:46:01Z",
                    "name": "exec_command",
                    "arguments": (
                        '{"cmd":"pwd","workdir":"/tmp/project",'
                        '"yield_time_ms":1000,"max_output_tokens":12000}'
                    ),
                    "call_id": "call-1",
                },
            ],
        )

        self.assertIn("tool-param-name\">cmd", html)
        self.assertIn("$ pwd", html)
        self.assertIn("tool-param-name\">workdir", html)
        self.assertIn("/tmp/project", html)
        self.assertIn("tool-param-name\">yield_time_ms", html)
        self.assertIn("1000", html)
        self.assertIn("tool-param-name\">max_output_tokens", html)
        self.assertIn("12000", html)


if __name__ == "__main__":
    unittest.main()
