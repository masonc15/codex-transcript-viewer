"""Write docs/demo/session.jsonl, the synthetic session behind docs/demo.md.

The demo uses a made-up session so no real prompts, paths or ids end up in the
repository, and so one short session can show every kind of entry the viewer
renders. Run it from the repository root: python3 docs/demo/make_session.py
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SESSION_ID = "019f8a10-6c2e-7d41-9b3a-5e2f10c4a7d8"
CWD = "/home/dev/todo-cli"

records: list[dict] = []
clock = datetime(2026, 9, 26, 14, 2, 11, tzinfo=timezone.utc)
turn = 0
tokens = 0


def add(kind: str, payload: dict, seconds: float = 2.0) -> None:
    global clock
    clock += timedelta(seconds=seconds)
    stamp = clock.strftime("%Y-%m-%dT%H:%M:%S.") + f"{clock.microsecond // 1000:03d}Z"
    records.append({"timestamp": stamp, "type": kind, "payload": payload})


def start_turn(prompt: str, model: str = "gpt-5.6-sol", effort: str = "high") -> str:
    global turn
    turn += 1
    turn_id = f"019f8a10-{turn:04x}-7000-8000-00000000000{turn}"
    add("event_msg", {"type": "task_started", "turn_id": turn_id, "model_context_window": 400000})
    add("turn_context", {"turn_id": turn_id, "cwd": CWD, "model": model, "effort": effort,
                         "summary": "auto", "approval_policy": "on-request"}, 0.1)
    add("event_msg", {"type": "item_completed", "turn_id": turn_id, "item": {
        "type": "UserMessage", "id": f"item-{turn}", "content": [{"type": "text", "text": prompt}]}}, 0.2)
    return turn_id


def reasoning(*parts: str) -> None:
    add("response_item", {"type": "reasoning", "summary": [{"type": "summary_text", "text": p} for p in parts],
                          "encrypted_content": "gAAAA-demo"}, 3)


def say(text: str, phase: str = "commentary") -> None:
    add("response_item", {"type": "message", "role": "assistant", "phase": phase,
                          "content": [{"type": "output_text", "text": text}]}, 2)


def count_tokens(used: int) -> None:
    global tokens
    tokens += used
    add("event_msg", {"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": tokens, "cached_input_tokens": tokens // 2,
        "output_tokens": tokens // 40, "reasoning_output_tokens": tokens // 90,
        "total_tokens": tokens + tokens // 40}}}, 0.3)


def run(call_id: str, cmd: str, output: str, exit_code: int = 0, seconds: float = 1.4) -> None:
    add("response_item", {"type": "function_call", "name": "exec_command", "call_id": call_id,
                          "arguments": json.dumps({"cmd": cmd, "workdir": CWD})}, 1)
    add("event_msg", {"type": "exec_command_end", "call_id": call_id, "exit_code": exit_code,
                      "duration": {"secs": int(seconds), "nanos": int(seconds % 1 * 1e9)}}, seconds)
    add("response_item", {"type": "function_call_output", "call_id": call_id, "output": output}, 0.1)


def patch(call_id: str, body: str, files: str) -> None:
    add("response_item", {"type": "custom_tool_call", "name": "apply_patch", "call_id": call_id,
                          "input": body}, 2)
    add("response_item", {"type": "custom_tool_call_output", "call_id": call_id, "output": json.dumps(
        {"output": f"Success. Updated the following files:\n{files}\n",
         "metadata": {"exit_code": 0, "duration_seconds": 0.1}})}, 0.2)


def finish(text: str, turn_id: str) -> None:
    say(text, "final_answer")
    add("event_msg", {"type": "task_complete", "turn_id": turn_id,
                      "last_agent_message": text.split("<oai-mem-citation>")[0].rstrip()}, 0.2)


add("session_meta", {"id": SESSION_ID, "timestamp": clock.isoformat(), "cwd": CWD, "originator": "codex_cli_rs",
                     "cli_version": "0.155.0", "model_provider": "openai",
                     "git": {"branch": "csv-export", "commit_hash": "4c1d9e07b2a85f3e6d10"}}, 0)

# Turn 1: a feature with a failing test on the way.
t = start_turn("Add a `--format csv` option to `todo export`, and cover it with tests.")
reasoning("**Inspecting the export command**")
say("I'll look at how `export` builds its output before adding a CSV path.")
run("call_rg", 'rg -n "def export|--format" src tests',
    "src/todo/export.py:18:def export(tasks, fmt=\"json\"):\n"
    "src/todo/cli.py:41:    parser.add_argument(\"--format\", choices=[\"json\", \"md\"], default=\"json\")\n")
run("call_sed", "sed -n '1,60p' src/todo/export.py",
    "import json\n\n\ndef export(tasks, fmt=\"json\"):\n    if fmt == \"md\":\n        return \"\\n\".join(\n"
    "            f\"- [{'x' if t.done else ' '}] {t.title}\" for t in tasks\n        )\n"
    "    return json.dumps([t.as_dict() for t in tasks], indent=2)\n")
reasoning("**Inspecting the export command**", "**Planning the CSV writer**")
patch("call_patch1",
      "*** Begin Patch\n*** Update File: src/todo/export.py\n@@\n-import json\n+import csv\n+import io\n+import json\n"
      "@@ def export(tasks, fmt=\"json\"):\n+    if fmt == \"csv\":\n+        buffer = io.StringIO()\n"
      "+        writer = csv.writer(buffer)\n+        writer.writerow([\"id\", \"title\", \"done\", \"due\"])\n"
      "+        for t in tasks:\n+            writer.writerow([t.id, t.title, t.done, t.due])\n"
      "+        return buffer.getvalue()\n*** Update File: src/todo/cli.py\n@@\n-    parser.add_argument(\"--format\", "
      "choices=[\"json\", \"md\"], default=\"json\")\n+    parser.add_argument(\"--format\", choices=[\"json\", \"md\", "
      "\"csv\"], default=\"json\")\n*** End Patch\n",
      "M src/todo/export.py\nM src/todo/cli.py")
count_tokens(18240)
run("call_test1", "pytest -q tests/test_export.py",
    "..F\n=================================== FAILURES ===================================\n"
    "__________________________ test_csv_escapes_commas ___________________________\n"
    "    assert rows[1][3] == \"\"\nE   AssertionError: assert 'None' == ''\n"
    "1 failed, 2 passed in 0.21s\n", exit_code=1, seconds=2.1)
say("One failure: tasks without a due date export `None` instead of an empty cell. Fixing that.")
patch("call_patch2",
      "*** Begin Patch\n*** Update File: src/todo/export.py\n@@\n-            writer.writerow([t.id, t.title, t.done, t.due])\n"
      "+            writer.writerow([t.id, t.title, t.done, t.due or \"\"])\n*** End Patch\n",
      "M src/todo/export.py")
run("call_test2", "pytest -q", "............\n12 passed in 0.34s\n", seconds=2.3)
count_tokens(9310)
finish(
    "Added `--format csv` to `todo export`. Every format now shares the same task list:\n\n"
    "| Format | Header row | Notes |\n|:--|:--:|:--|\n"
    "| `json` | no | unchanged default |\n| `md` | no | checklist |\n"
    "| `csv` | **yes** | empty cell when there's no due date |\n\n"
    "The writer lives in [export.py](/home/dev/todo-cli/src/todo/export.py:18) and uses Python's "
    "[csv module](https://docs.python.org/3/library/csv.html), so commas and quotes in titles are escaped:\n\n"
    "```python\nwriter = csv.writer(buffer)\nwriter.writerow([\"id\", \"title\", \"done\", \"due\"])\n"
    "for t in tasks:\n    writer.writerow([t.id, t.title, t.done, t.due or \"\"])  # no due date -> empty\n```\n\n"
    "- Tests: `pytest -q` passes (12 tests)\n  - new: header row, commas in titles, missing due date\n"
    "- Not changed: the `md` and `json` output\n\n"
    "<oai-mem-citation>\n<citation_entries>\nMEMORY.md:88-94|note=[todo-cli test conventions]\n"
    "</citation_entries>\n<rollout_ids>\n019f7c2e-41aa-7b10-a3f2-6d0e5b9c1f22\n</rollout_ids>\n</oai-mem-citation>",
    t,
)

# Turn 2: a goal, a bigger model, a hook, and a generated image.
t = start_turn("/goal Ship CSV export: README section, changelog entry, and an icon for the README.",
               model="gpt-6-astra", effort="xhigh")
goal = {"threadId": SESSION_ID, "objective": "Ship CSV export: README section, changelog entry, and an icon "
        "for the README.", "status": "active", "tokensUsed": 0, "timeUsedSeconds": 0}
add("event_msg", {"type": "thread_goal_updated", "threadId": SESSION_ID, "turnId": t, "goal": goal}, 0.2)
reasoning("**Planning the README changes**")
say("I'll document the new format first, then make the icon.")
patch("call_patch3", "*** Begin Patch\n*** Update File: README.md\n@@\n+## Export\n+\n+`todo export --format csv` "
      "writes one row per task.\n*** Update File: CHANGELOG.md\n@@\n+- `todo export --format csv`\n*** End Patch\n",
      "M README.md\nM CHANGELOG.md")
add("event_msg", {"type": "thread_goal_updated", "threadId": SESSION_ID, "turnId": t,
                  "goal": {**goal, "tokensUsed": 21040, "timeUsedSeconds": 64}}, 0.2)
icon = base64.b64encode((HERE / "generated-icon.png").read_bytes()).decode()
add("response_item", {"type": "image_generation_call", "id": "ig_demo_icon", "status": "completed",
                      "revised_prompt": "Flat app icon for a command-line todo tool: a dark rounded square with a "
                      "green border and a three-item checklist, two items checked.", "result": icon}, 9)
add("event_msg", {"type": "item_completed", "turn_id": t, "item": {"type": "HookPrompt", "id": "hook-1", "fragments": [
    {"text": "Run `make lint` before you finish. It failed on the last commit.",
     "hookRunId": "stop:1:/home/dev/todo-cli/.codex/hooks.json"}]}}, 1)
run("call_lint", "make lint", "ruff check src tests\nAll checks passed!\n", seconds=1.8)
add("event_msg", {"type": "thread_goal_updated", "threadId": SESSION_ID, "turnId": t,
                  "goal": {**goal, "status": "complete", "tokensUsed": 38770, "timeUsedSeconds": 131}}, 0.2)
count_tokens(24410)
finish("Done: the README has an Export section, the changelog has an entry, and the icon is above. "
       "`make lint` passes.", t)

# Turn 3: a code review whose findings nobody repeats.
t = start_turn("/review", model="gpt-6-astra", effort="xhigh")
add("event_msg", {"type": "item_completed", "turn_id": t, "item": {
    "type": "EnteredReviewMode", "id": "review-1", "user_facing_hint": "changes against 'main'"}}, 1)
add("event_msg", {"type": "item_completed", "turn_id": t, "item": {"type": "ExitedReviewMode", "id": "review-2",
    "review_output": {
        "findings": [{
            "title": "[P2] Write CSV with a Unicode byte-order mark for Excel",
            "body": "Excel on Windows reads the file as ANSI without a BOM, so accented titles come out garbled. "
                    "Use `encoding=\"utf-8-sig\"` when the export goes to a file.",
            "confidence_score": 0.72, "priority": 2,
            "code_location": {"absolute_file_path": "/home/dev/todo-cli/src/todo/export.py",
                              "line_range": {"start": 24, "end": 27}}}],
        "overall_correctness": "patch is correct",
        "overall_explanation": "The CSV path is correct and tested. One portability issue is worth fixing.",
        "overall_confidence_score": 0.8}}}, 40)
count_tokens(12020)

out = HERE / "session.jsonl"
out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
print(f"wrote {out.relative_to(HERE.parent.parent)} ({len(records)} records)")
