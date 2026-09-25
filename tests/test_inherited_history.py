from __future__ import annotations

import unittest

from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import extract_conversation

# UUIDv7 ids: the first 48 bits are a millisecond timestamp.
CHILD = "01990000-1000-7000-8000-000000000000"
PARENT_TURN = "01980000-0000-7000-8000-000000000001"  # created well before CHILD
CHILD_TURN = "01990000-2000-7000-8000-000000000002"  # created after CHILD


def _event_msg(payload: dict) -> dict:
    return {"type": "event_msg", "timestamp": "2026-09-01T22:07:06Z", "payload": payload}


def _answer(text: str) -> dict:
    return {
        "type": "response_item",
        "timestamp": "2026-09-01T22:07:06Z",
        "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                    "content": [{"type": "output_text", "text": text}]},
    }


def _session(source: object) -> list[dict]:
    return [
        {"type": "session_meta", "payload": {"id": CHILD, "source": source}},
        {"type": "session_meta", "payload": {"id": "01980000-0000-7000-8000-00000000000a", "source": "cli"}},
        _event_msg({"type": "task_started", "turn_id": PARENT_TURN}),
        _answer("parent answer"),
        _event_msg({"type": "task_started", "turn_id": CHILD_TURN}),
        _answer("child answer"),
    ]


SUBAGENT = {"subagent": {"thread_spawn": {"parent_thread_id": "p", "depth": 1}}}


class InheritedHistoryTests(unittest.TestCase):
    def test_parent_turns_are_marked_inherited(self) -> None:
        _meta, events = extract_conversation(_session(SUBAGENT))
        marks = {e["text"]: e.get("inherited", False) for e in events if e["type"] == "assistant_text"}
        self.assertEqual(marks, {"parent answer": True, "child answer": False})

    def test_non_subagent_sessions_are_never_inherited(self) -> None:
        _meta, events = extract_conversation(_session("cli"))
        self.assertFalse(any(e.get("inherited") for e in events))

    def test_inherited_run_is_collapsed_with_one_sidebar_node(self) -> None:
        meta, events = extract_conversation(_session(SUBAGENT))
        html = build_html(meta, events)
        self.assertEqual(html.count('<details class="inherited-history"'), 1)
        self.assertIn("Inherited parent history (2)", html)
        sidebar = html.split('id="tree-container">', 1)[1].split("</aside>", 1)[0]
        self.assertNotIn("parent answer", sidebar)
        self.assertIn("child answer", sidebar)
        body = html.split('<div id="messages">', 1)[1]
        self.assertLess(body.index("parent answer"), body.index("</details>"))
        self.assertGreater(body.index("child answer"), body.index("</details>"))


if __name__ == "__main__":
    unittest.main()
