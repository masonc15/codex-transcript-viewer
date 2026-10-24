from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from codex_transcript_viewer import cli

RECORDS = [
    {"type": "session_meta", "timestamp": "2026-01-01T00:00:00Z", "payload": {"id": "cli-test"}},
    {"type": "event_msg", "timestamp": "2026-01-01T00:00:01Z",
     "payload": {"type": "user_message", "message": "hello"}},
    {"type": "response_item", "timestamp": "2026-01-01T00:00:02Z",
     "payload": {"type": "additional_tools", "role": "developer", "tools": []}},
    {"type": "event_msg", "timestamp": "2026-01-01T00:00:03Z", "payload": {"type": "brand_new_kind"}},
]


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.session = self.dir / "s.jsonl"
        self.session.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_converts_and_reports_only_unknown_kinds(self) -> None:
        code, out, err = _run(str(self.session), str(self.dir / "out.html"))
        self.assertEqual(code, 0)
        self.assertIn("written to", out)
        self.assertIn("hello", (self.dir / "out.html").read_text())
        self.assertIn("event_msg/brand_new_kind x1", err)
        self.assertNotIn("additional_tools", err)

    def test_refuses_to_overwrite_the_session(self) -> None:
        before = self.session.read_bytes()
        alias = self.dir / "alias.jsonl"
        os.link(self.session, alias)
        for target in (self.session, alias, self.dir / "." / "s.jsonl"):
            with self.subTest(target=target):
                code, _, err = _run(str(self.session), str(target))
                self.assertEqual(code, 1)
                self.assertIn("is the session itself", err)
        self.assertEqual(self.session.read_bytes(), before)

    def test_path_errors_are_messages_not_tracebacks(self) -> None:
        cases = [
            ((str(self.dir / "missing.jsonl"),), "not found"),
            ((str(self.dir),), "is not a file"),
            ((str(self.session), str(self.dir)), "is a directory"),
            ((str(self.session), str(self.dir / "nope" / "out.html")), "does not exist"),
        ]
        for argv, message in cases:
            with self.subTest(argv=argv):
                code, _, err = _run(*argv)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertNotIn("Traceback", err)

    def test_numeric_options_must_be_finite_and_non_negative(self) -> None:
        for flag, value in (("--max-image-mb", "nan"), ("--max-image-mb", "inf"),
                            ("--max-image-mb", "-1"), ("--max-output-chars", "-5"),
                            ("--max-output-chars", "lots")):
            with self.subTest(flag=flag, value=value):
                code, _, err = _run(str(self.session), str(self.dir / "o.html"), flag, value)
                self.assertEqual(code, 2)
                self.assertIn(flag, err)

    def test_zero_still_means_no_limit(self) -> None:
        code, _, _ = _run(str(self.session), str(self.dir / "o.html"),
                          "--max-image-mb", "0", "--max-output-chars", "0")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
