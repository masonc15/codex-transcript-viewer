from __future__ import annotations

import unittest

from codex_transcript_viewer.parser import extract_conversation


class SessionMetaTests(unittest.TestCase):
    def test_first_session_meta_describes_the_session(self) -> None:
        meta, _events = extract_conversation(
            [
                {"type": "session_meta", "payload": {"id": "child", "source": {"subagent": {}}}},
                {"type": "session_meta", "payload": {"id": "parent", "source": "cli"}},
            ]
        )
        self.assertEqual(meta["id"], "child")


if __name__ == "__main__":
    unittest.main()
