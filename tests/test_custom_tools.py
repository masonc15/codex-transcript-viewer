from __future__ import annotations

import json
import unittest

from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import extract_conversation

EXEC_INPUT = (
    'text(await tools.exec_command({cmd:"rg -n x"}));\n'
    'text(await tools.exec_command({cmd:"ls"}));\n'
    'text(await tools.write_stdin({session_id: 1, chars: ""}));'
)
PATCH_INPUT = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+<b>y</b>\n*** End Patch"


def _item(payload: dict) -> dict:
    return {"type": "response_item", "timestamp": "2026-09-26T19:00:00Z", "payload": payload}


def _events() -> list[dict]:
    _meta, events = extract_conversation([
        {"type": "session_meta", "payload": {"id": "s"}},
        _item({"type": "custom_tool_call", "name": "exec", "input": EXEC_INPUT, "call_id": "c1"}),
        _item({"type": "custom_tool_call_output", "call_id": "c1", "output": [
            {"type": "input_text", "text": "Script completed\n"},
            {"type": "input_text", "text": json.dumps({"chunk_id": "a", "exit_code": 0, "output": "hit"})},
        ]}),
        _item({"type": "custom_tool_call", "name": "apply_patch", "input": PATCH_INPUT, "call_id": "c2"}),
        _item({"type": "custom_tool_call_output", "call_id": "c2",
               "output": json.dumps({"output": "Success.", "metadata": {"exit_code": 0}})}),
    ])
    return events


class CustomToolTests(unittest.TestCase):
    def test_custom_calls_and_outputs_are_parsed(self) -> None:
        events = _events()
        calls = [e for e in events if e["type"] == "tool_call"]
        outputs = [e for e in events if e["type"] == "tool_output"]
        self.assertEqual([c["name"] for c in calls], ["exec", "apply_patch"])
        self.assertTrue(all(c["input_kind"] == "custom" for c in calls))
        self.assertEqual(calls[0]["arguments"], EXEC_INPUT)
        self.assertEqual([o["exit_codes"] for o in outputs], [[0], [0]])
        self.assertIn("hit", outputs[0]["output"])

    def test_custom_input_renders_as_escaped_text(self) -> None:
        html = build_html({"id": "s"}, _events())
        self.assertIn("exec: 3 tool calls", html)
        self.assertIn("apply_patch: *** Begin Patch", html)
        self.assertIn("+&lt;b&gt;y&lt;/b&gt;", html)
        self.assertNotIn('class="tool-param"', html)


if __name__ == "__main__":
    unittest.main()
