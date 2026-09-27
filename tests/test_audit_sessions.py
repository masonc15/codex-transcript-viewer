from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import audit_sessions as audit  # noqa: E402

sys.path.pop(0)


def _event_msg(payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": "2026-01-01T00:00:00Z", "payload": payload}


def _assistant(text: str, phase: str) -> dict:
    return {
        "type": "response_item",
        "timestamp": "2026-01-01T00:00:00Z",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _session(*records: dict) -> list[dict]:
    return [
        {"type": "session_meta", "payload": {"id": "s", "cli_version": "0.1.0"}},
        _event_msg({"type": "task_started", "turn_id": "t1"}),
        _event_msg({"type": "user_message", "message": "go"}),
        *records,
    ]


class CheckTests(unittest.TestCase):
    """The checks, fed raw records and hand-built viewer events."""

    def test_same_text_in_two_kinds_is_a_duplicate(self) -> None:
        raw = audit.raw_texts_by_turn(_session(
            _event_msg({"type": "agent_message", "message": "Done."}),
            _assistant("Done.", "final_answer"),
        ))
        visible = {1: [("user_message", "go"), ("agent_commentary", "Done."),
                       ("assistant_text.final_answer", "Done.")]}
        self.assertEqual(len(audit.check_duplicates(raw, visible)), 1)
        findings, _ = audit.check_truth(raw, visible)
        self.assertEqual(findings, ["turn 1: extra assistant x1 'Done.'"])

    def test_text_the_model_repeated_is_allowed(self) -> None:
        raw = audit.raw_texts_by_turn(_session(
            _assistant("still waiting", "commentary"),
            _assistant("still waiting", "commentary"),
        ))
        visible = {1: [("user_message", "go"), ("assistant_text.commentary", "still waiting"),
                       ("assistant_text.commentary", "still waiting")]}
        self.assertEqual(audit.check_duplicates(raw, visible), [])
        self.assertEqual(audit.check_truth(raw, visible)[0], [])

    def test_same_text_in_different_roles_is_allowed(self) -> None:
        raw = audit.raw_texts_by_turn([
            _event_msg({"type": "task_started", "turn_id": "t1"}),
            _event_msg({"type": "user_message", "message": "yo"}),
            _assistant("yo", "final_answer"),
        ])
        visible = {1: [("user_message", "yo"), ("assistant_text.final_answer", "yo")]}
        self.assertEqual(audit.check_duplicates(raw, visible), [])
        self.assertEqual(audit.overlaps(raw).keys() - {("equal", "assistant.response.final_answer",
                                                         "prompt.legacy")}, set())
        self.assertIsNotNone(audit.rule_for(("equal", "assistant.response.final_answer",
                                             "prompt.legacy")))

    def test_copy_without_plan_block_counts_as_a_duplicate(self) -> None:
        answer = "Locked.\n\n<proposed_plan>\n- step\n</proposed_plan>"
        raw = audit.raw_texts_by_turn(_session(
            _event_msg({"type": "agent_message", "message": "Locked.\n\n"}),
            _assistant(answer, "final_answer"),
        ))
        visible = {1: [("user_message", "go"), ("agent_commentary", "Locked."),
                       ("assistant_text.final_answer", audit.normalize(answer))]}
        self.assertEqual(len(audit.check_duplicates(raw, visible)), 1)
        self.assertEqual(len(audit.check_truth(raw, visible)[0]), 1)
        self.assertIn(("copy", "assistant.event", "assistant.response.final_answer"),
                      audit.overlaps(raw))

    def test_missing_entry_is_reported(self) -> None:
        raw = audit.raw_texts_by_turn(_session(_assistant("Done.", "final_answer")))
        findings, _ = audit.check_truth(raw, {1: [("user_message", "go")]})
        self.assertEqual(findings, ["turn 1: missing assistant x1 'Done.'"])

    def test_overlap_without_a_rule_is_unhandled(self) -> None:
        raw = audit.raw_texts_by_turn(_session(
            _event_msg({"type": "agent_reasoning", "text": "thinking it over"}),
            _event_msg({"type": "agent_message", "message": "thinking it over"}),
        ))
        key = ("equal", "assistant.event", "reasoning.event")
        self.assertIn(key, audit.overlaps(raw))
        # Different roles, so this pair is fine; a same-role pair needs a rule.
        self.assertIsNotNone(audit.rule_for(key))
        self.assertIsNone(audit.rule_for(("prefix", "assistant.event",
                                          "assistant.response.commentary")))


class AuditEntriesTests(unittest.TestCase):
    """End to end through the current parser."""

    def test_legacy_final_answer_copies_leave_no_findings(self) -> None:
        answer = "Done.\n\n<oai-mem-citation>\nMEMORY.md:1\n</oai-mem-citation>"
        report = audit.audit_entries("s", _session(
            _event_msg({"type": "agent_message", "message": "Checking."}),
            _assistant("Checking.", "commentary"),
            _event_msg({"type": "agent_message", "message": "Done."}),
            _assistant(answer, "final_answer"),
            _event_msg({"type": "task_complete", "last_agent_message": "Done."}),
        ))
        self.assertFalse(audit.has_findings(report), report)
        self.assertEqual(report["totals"]["assistant.shown"], 2)

    def test_parser_crash_is_an_error_finding(self) -> None:
        parser, html_builder = audit._viewer()

        def crash(entries):
            raise ValueError("boom")

        with mock.patch.object(parser, "extract_conversation", crash):
            report = audit.audit_entries("s", _session())
        self.assertIn("boom", report["errors"][0])
        self.assertTrue(audit.has_findings(report))


if __name__ == "__main__":
    unittest.main()
