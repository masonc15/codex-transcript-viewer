from __future__ import annotations

import unittest

from codex_transcript_viewer.html_builder import build_html
from codex_transcript_viewer.parser import extract_conversation


def _calls(*actions: object) -> list[dict]:
    entries = [{"type": "session_meta", "payload": {"id": "s"}}]
    for action in actions:
        payload = {"type": "web_search_call", "status": "completed"}
        if action is not None:
            payload["action"] = action
        entries.append({"type": "response_item", "timestamp": "", "payload": payload})
    _meta, events = extract_conversation(entries)
    return [e for e in events if e["type"] == "tool_call"]


class WebSearchTests(unittest.TestCase):
    def test_action_kinds(self) -> None:
        calls = _calls(
            {"type": "search", "query": "codex notify", "queries": ["codex notify", "site:openai.com notify"]},
            {"type": "open_page", "url": "https://example.com/"},
            {"type": "find_in_page", "url": "https://example.com/doc", "pattern": "notify"},
            None,
            {"type": "scroll", "offset": 3},
        )
        self.assertEqual([c["arguments"] for c in calls], [
            "search: codex notify\n  also: site:openai.com notify",
            "open_page: https://example.com/",
            'find_in_page: "notify" in https://example.com/doc',
            "(no details recorded)",
            'scroll: {"type": "scroll", "offset": 3}',
        ])
        self.assertTrue(all(c["name"] == "web_search" and c["call_id"] == "" for c in calls))

    def test_hostile_query_is_escaped(self) -> None:
        html = build_html({"id": "s"}, _calls({"type": "search", "query": "<img src=x onerror=1>"}))
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x onerror=1&gt;", html)


if __name__ == "__main__":
    unittest.main()
