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


    def test_final_answers_are_marked_for_the_answers_filter(self) -> None:
        html = build_html(
            {"id": "session-1"},
            [
                {"type": "user_message", "ts": "", "text": "question"},
                {"type": "assistant_text", "ts": "", "text": "note", "phase": "commentary"},
                {"type": "assistant_text", "ts": "", "text": "answer", "phase": "final_answer"},
                {"type": "task_complete", "ts": "", "text": "fallback answer", "turn_id": "t"},
            ],
        )

        self.assertEqual(html.count('data-kind="final-answer"'), 2)
        self.assertEqual(html.count('data-kind="user"'), 1)
        self.assertEqual(html.count('data-kind="commentary"'), 1)
        self.assertIn('<div class="commentary-message"', html)
        self.assertIn("node.dataset.kind === 'final-answer'", html)

    def test_turns_and_sources_are_marked_for_the_visual_review(self) -> None:
        html = build_html(
            {"id": "s"},
            [
                {"type": "task_started", "ts": "", "turn_id": "t"},
                {"type": "agent_commentary", "ts": "", "text": "Turn started", "source": "event"},
                {"type": "assistant_text", "ts": "", "text": "a", "phase": "", "source": "response"},
            ],
        )
        self.assertEqual(html.count('data-kind="turn"'), 1)
        self.assertIn('data-kind="commentary" data-source="event"', html)
        self.assertIn('data-kind="assistant" data-source="response"', html)


if __name__ == "__main__":
    unittest.main()
