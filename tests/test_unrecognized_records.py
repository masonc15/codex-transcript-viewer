from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from codex_transcript_viewer import cli
from codex_transcript_viewer.parser import unrecognized_record_kinds


class UnrecognizedRecordTests(unittest.TestCase):
    def test_known_and_ignored_kinds_are_not_reported(self) -> None:
        entries = [
            {"type": "session_meta", "payload": {}},
            {"type": "turn_context", "payload": {}},
            {"type": "event_msg", "payload": {"type": "exec_command_end"}},
            {"type": "event_msg", "payload": {"type": "mcp_tool_call_end"}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution"}}},
            {"type": "response_item", "payload": {"type": "ghost_snapshot"}},
        ]
        self.assertEqual(unrecognized_record_kinds(entries), {})

    def test_new_kinds_are_counted(self) -> None:
        entries = [
            {"type": "event_msg", "payload": {"type": "brand_new_event"}},
            {"type": "event_msg", "payload": {"type": "brand_new_event"}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "Hologram"}}},
            {"type": "response_item", "payload": {"type": "teleport_call"}},
            {"type": "mystery"},
            "not a dict",
        ]
        self.assertEqual(dict(unrecognized_record_kinds(entries)), {
            "event_msg/brand_new_event": 2,
            "item_completed/Hologram": 1,
            "response_item/teleport_call": 1,
            "mystery": 1,
        })

    def test_cli_reports_on_stderr_only_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp, "s.jsonl")
            out = Path(tmp, "s.html")
            session.write_text("\n".join(json.dumps(e) for e in [
                {"type": "session_meta", "payload": {"id": "x"}},
                {"type": "event_msg", "payload": {"type": "brand_new_event"}},
            ]))
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                cli.main([str(session), str(out)])
            self.assertIn("skipped unrecognized records: event_msg/brand_new_event x1", err.getvalue())


if __name__ == "__main__":
    unittest.main()
