from __future__ import annotations

import re
import unittest

from codex_transcript_viewer.html_builder import build_html


def _call(call_id: str, name: str = "exec_command", **extra: object) -> dict:
    return {"type": "tool_call", "ts": "", "name": name, "arguments": "{}", "call_id": call_id, **extra}


def _output(call_id: str, **extra: object) -> dict:
    event = {"type": "tool_output", "ts": "", "call_id": call_id, "output": "x",
             "attachments": [], "exit_codes": [], "failed": None, "duration": None}
    event.update(extra)
    return event


def _classes(html: str) -> list[str]:
    return re.findall(r'<div class="tool-execution ([a-z-]+)"', html)


class ToolStatusTests(unittest.TestCase):
    def test_status_follows_exit_codes(self) -> None:
        html = build_html({"id": "s"}, [
            _call("ok"), _output("ok", exit_codes=[0]),
            _call("bad"), _output("bad", exit_codes=[2]),
            _call("unk"), _output("unk"),
            _call("patch", name="apply_patch"), _output("patch", failed=True),
        ])
        self.assertEqual(_classes(html), ["success", "success", "failure", "failure",
                                          "unknown", "unknown", "failure", "failure"])
        self.assertIn("exit 2", html)
        self.assertIn("status unknown", html)
        self.assertIn("exec_command result", html)

    def test_multi_command_wrapper_summary(self) -> None:
        html = build_html({"id": "s"}, [_call("w", name="exec"), _output("w", exit_codes=[0, 1, 0])])
        self.assertIn("3 commands, 1 failed", html)
        self.assertEqual(_classes(html), ["failure", "failure"])

    def test_missing_and_orphan_results(self) -> None:
        html = build_html({"id": "s"}, [_call("lonely"), _output("stray", exit_codes=[0])])
        self.assertEqual(_classes(html), ["no-result", "success"])
        self.assertIn("no result recorded", html)
        self.assertIn("orphan output", html)

    def test_single_record_calls_have_no_result_state(self) -> None:
        html = build_html({"id": "s"}, [_call("", name="web_search", input_kind="web_search")])
        self.assertEqual(_classes(html), ["single"])
        self.assertNotIn("no result recorded", html)


if __name__ == "__main__":
    unittest.main()
