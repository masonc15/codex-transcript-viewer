from __future__ import annotations

import unittest

from codex_transcript_viewer.parser import extract_conversation


def _events(arguments: object, tools: object) -> list[dict]:
    item = lambda payload: {"type": "response_item", "timestamp": "", "payload": payload}
    _meta, events = extract_conversation([
        {"type": "session_meta", "payload": {"id": "s"}},
        item({"type": "tool_search_call", "call_id": "t1", "arguments": arguments}),
        item({"type": "tool_search_output", "call_id": "t1", "tools": tools}),
    ])
    return events


class ToolSearchTests(unittest.TestCase):
    def test_python_repr_arguments_and_namespaced_tools(self) -> None:
        events = _events(
            "{'query': 'openai docs search', 'limit': 5}",
            [{"type": "namespace", "name": "mcp__docs", "tools": [
                {"type": "function", "name": "search", "description": "long schema"},
                {"type": "function", "name": "fetch"},
            ]}],
        )
        call, output = events
        self.assertEqual((call["name"], call["arguments"], call["call_id"]),
                         ("tool_search", "openai docs search", "t1"))
        self.assertEqual(output["output"], "mcp__docs.search\nmcp__docs.fetch")
        self.assertNotIn("long schema", output["output"])

    def test_json_arguments_and_empty_results(self) -> None:
        call, output = _events('{"query": "x"}', [])
        self.assertEqual(call["arguments"], "x")
        self.assertEqual(output["output"], "(no tools returned)")

    def test_unparseable_arguments_are_kept_verbatim(self) -> None:
        call, _output = _events("__import__('os')", None)
        self.assertEqual(call["arguments"], "__import__('os')")


if __name__ == "__main__":
    unittest.main()
