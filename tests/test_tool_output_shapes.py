from __future__ import annotations

import json
import unittest

from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import normalize_tool_output

PNG = "data:image/png;base64,iVBORw0KGgoAAAA"


class NormalizeToolOutputTests(unittest.TestCase):
    def test_plain_string_is_kept(self) -> None:
        out = normalize_tool_output("Chunk ID: 1\nProcess exited with code 0\nOutput:\nhi")
        self.assertEqual(out["output"], "Chunk ID: 1\nProcess exited with code 0\nOutput:\nhi")
        self.assertEqual(out["exit_codes"], [])

    def test_apply_patch_json_string_yields_text_and_metadata(self) -> None:
        raw = json.dumps({"output": "Success. Updated a.py\n",
                          "metadata": {"exit_code": 0, "duration_seconds": 0.25}})
        out = normalize_tool_output(raw)
        self.assertEqual(out["output"], "Success. Updated a.py\n")
        self.assertEqual(out["exit_codes"], [0])
        self.assertEqual(out["duration"], 0.25)

    def test_code_mode_chunks_collect_every_exit_code(self) -> None:
        chunk = lambda code, text: {"type": "input_text", "text": json.dumps(
            {"chunk_id": "a", "exit_code": code, "output": text, "wall_time_seconds": 0.1})}
        out = normalize_tool_output([
            {"type": "input_text", "text": "Script completed\nWall time 0.2 seconds\nOutput:\n"},
            chunk(0, "ok\n"),
            chunk(2, "grep: nope\n"),
            {"type": "input_text", "text": json.dumps({"chunk_id": "b", "output": "running"})},
        ])
        self.assertEqual(out["exit_codes"], [0, 2])
        self.assertIn("grep: nope", out["output"])
        self.assertNotIn("chunk_id", out["output"])

    def test_exit_code_inside_output_text_is_ignored(self) -> None:
        out = normalize_tool_output('rg found: "exit_code": 1 in a log file')
        self.assertEqual(out["exit_codes"], [])

    def test_image_only_output_becomes_attachment(self) -> None:
        out = normalize_tool_output([{"type": "input_image", "image_url": PNG}])
        self.assertEqual(out["output"], "")
        self.assertEqual(out["attachments"][0]["kind"], "image")
        self.assertEqual(out["attachments"][0]["data_url"], PNG)

    def test_odd_shapes_always_give_a_string(self) -> None:
        for value in (None, 3, {"a": 1}, [None, 7, {"type": "unknown", "x": 1}]):
            with self.subTest(value=value):
                self.assertIsInstance(normalize_tool_output(value)["output"], str)


    def test_exit_code_behind_truncation_notice(self) -> None:
        text = ("Warning: truncated output (original token count: 21450)\nTotal output lines: 7\n\n"
                + json.dumps({"chunk_id": "f4", "exit_code": 0, "output": "tail of output"}))
        out = normalize_tool_output([{"type": "input_text", "text": text}])
        self.assertEqual(out["exit_codes"], [0])
        self.assertIn("Warning: truncated output", out["output"])
        self.assertIn("tail of output", out["output"])

    def test_truncated_invalid_json_stays_text(self) -> None:
        text = "Warning: truncated output (x)\nTotal output lines: 1\n\n{\"exit_code\": 0, \"output\": \"cut"
        out = normalize_tool_output([{"type": "input_text", "text": text}])
        self.assertEqual(out["exit_codes"], [])
        self.assertEqual(out["output"], text)


class ToolOutputRenderTests(unittest.TestCase):
    def _html(self, **fields: object) -> str:
        event = {"type": "tool_output", "ts": "", "call_id": "c", "output": "",
                 "attachments": [], "exit_codes": [], "failed": None, "duration": None}
        event.update(fields)
        return build_html({"id": "s"}, [event])

    def test_image_output_renders_chip_not_base64_text(self) -> None:
        html = self._html(attachments=[{"kind": "image", "data_url": PNG, "bytes": 212_000}])
        self.assertIn("[image: image output, 212 KB]", html)
        self.assertNotIn("iVBORw0KGgo", html)
        self.assertIn("output (1 image)", html)

    def test_output_text_is_escaped(self) -> None:
        html = self._html(output="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
