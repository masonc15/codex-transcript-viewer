from __future__ import annotations

import unittest

from codex_transcript_viewer.parser import extract_conversation


def _entry(kind: str, payload: dict) -> dict:
    return {"type": kind, "timestamp": "2026-04-21T18:00:00Z", "payload": payload}


def _outputs(entries: list[dict]) -> list[dict]:
    _meta, events = extract_conversation([{"type": "session_meta", "payload": {"id": "s"}}, *entries])
    return [e for e in events if e["type"] == "tool_output"]


def _call(call_id: str, output: object) -> list[dict]:
    return [
        _entry("response_item", {"type": "function_call", "name": "exec_command",
                                 "arguments": "{}", "call_id": call_id}),
        _entry("response_item", {"type": "function_call_output", "call_id": call_id,
                                 "output": output}),
    ]


class ExecStatusTests(unittest.TestCase):
    def test_exec_command_end_supplies_exit_code_and_duration(self) -> None:
        out = _outputs([
            *_call("c1", "no header here"),
            _entry("event_msg", {"type": "exec_command_end", "call_id": "c1", "exit_code": 2,
                                 "duration": {"secs": 1, "nanos": 500_000_000}}),
        ])[0]
        self.assertEqual(out["exit_codes"], [2])
        self.assertAlmostEqual(out["duration"], 1.5)

    def test_end_event_wins_over_header(self) -> None:
        out = _outputs([
            *_call("c1", "Chunk ID: x\nWall time: 0\nProcess exited with code 0\nOutput:\n"),
            _entry("event_msg", {"type": "exec_command_end", "call_id": "c1", "exit_code": 1}),
        ])[0]
        self.assertEqual(out["exit_codes"], [1])

    def test_header_fallback_reads_only_first_lines(self) -> None:
        header = _outputs(_call("c1", "Chunk ID: x\nWall time: 0\nProcess exited with code 3\nOutput:\n"))[0]
        self.assertEqual(header["exit_codes"], [3])
        buried = _outputs(_call("c2", "a\nb\nc\nd\nProcess exited with code 9\n"))[0]
        self.assertEqual(buried["exit_codes"], [])

    def test_patch_results(self) -> None:
        ok = _outputs([
            *_call("p1", "Success."),
            _entry("event_msg", {"type": "patch_apply_end", "call_id": "p1", "success": True}),
        ])[0]
        self.assertIs(ok["failed"], False)
        failed = _outputs(_call("p2", "apply_patch verification failed: context not found"))[0]
        self.assertIs(failed["failed"], True)

    def test_unknown_status_stays_unknown(self) -> None:
        out = _outputs(_call("c1", "just text"))[0]
        self.assertEqual(out["exit_codes"], [])
        self.assertIsNone(out["failed"])

    def test_end_events_do_not_become_visible_events(self) -> None:
        _meta, events = extract_conversation([
            _entry("event_msg", {"type": "exec_command_end", "call_id": "orphan", "exit_code": 0}),
        ])
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
