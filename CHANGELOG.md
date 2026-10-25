# Changelog

## 0.6.0

A full review of 0.5.0 found 28 bugs. This release fixes all of them.

- A crafted session file could run JavaScript when you opened its page. Three things reached the page unescaped: a timestamp the viewer couldn't parse, a rollback's turn count, and text that imitated the markdown renderer's internal placeholders. That last kind of text could also hang or crash the renderer. The viewer now escapes all three.
- The Answers filter works on older sessions. Codex didn't always label its final answer, and those sessions showed no answers. Now a `task_complete` record marks the message it repeats. Without one, the last message of an exchange counts as the answer when no tool call follows it. Turns logged only as events no longer show the answer twice.
- Review findings stay on the page unless the assistant's reply repeats every one of them. Even then, the viewer folds them into a collapsed section. Before, any reply hid them, even one that only said it would fix them.
- A code block ends only at a real closing fence, so backticks inside the code no longer end it. Longer fences and `~~~` fences work too. A memory-citation example inside a code block stays in the answer. Before, the viewer cut it out along with everything after it.
- Links stop at a closing quote, angle-bracket links keep their query string, and link targets can contain balanced parentheses or sit inside `<>`. `2 * 3 * 4` no longer turns into italics. Headings from `####` to `######` render, and bold works inside link labels.
- Long tool outputs have a Show all button, and selecting text in an output no longer collapses it. You can expand outputs and enlarge images from the keyboard. On a phone, the sidebar fits the screen and closes after you pick an entry.
- Commentary from newer sessions looks like commentary. Each reasoning heading shows once per turn, even when the model restates it out of order.
- A tool result with an `output` field keeps its other fields. A message that arrives in several text blocks shows as one entry. A legacy prompt stays when its text differs from the item prompt in the same turn, and a prompt's image stays with the copy the viewer shows. A forked subagent's header shows the subagent's own starting model.
- Malformed session files no longer crash the viewer. That covers invalid UTF-8, lines that aren't JSON objects, null or odd fields, and infinite numbers. Timestamps with an offset now convert to UTC. Codex started writing `response_item/additional_tools` records in October, and the viewer now skips them instead of reporting them as unknown.
- The CLI won't overwrite the session it reads. It rejects negative or non-finite limits and bad paths with an error message instead of a traceback. It streams the file, so a 371 MB session peaks at 550 MB of memory instead of 1.3 GB. A turn with thousands of messages no longer takes seconds to process.
- `scripts/audit_sessions.py` fails on unrecognized record kinds, on paths that don't exist, and when it audited nothing. A crash in one session becomes a finding for that session instead of stopping the run. A new check reports turns whose log names an answer that the page doesn't mark as final. `scripts/visual_review.py` finds turns by a page marker instead of by text, and only fails a repeat when one side is an event copy.

## 0.5.0

- Short tool outputs no longer appear twice. Every output under 2,000 characters has shown its text twice since the first release. Only long outputs were meant to have a preview and a full copy.
- Memory citations at the end of an answer fold into a small "Memory citations" section, listing each cited note and the sessions it came from, instead of printing Codex's raw citation block.
- The header shows the model and reasoning effort the session started with, not just "openai", and a row marks each turn that switches model or effort.
- Code blocks get syntax highlighting for Python, shell, JavaScript and TypeScript, JSON, TOML and YAML, SQL, diffs, and C-like languages. It's built in, so pages stay self-contained.
- Nested and numbered lists and blockquotes render, and reasoning summaries render as markdown instead of showing literal `**`.
- The demo page now uses a made-up session that shows every kind of entry, and a script regenerates its screenshots.
- Dependabot keeps the workflow actions current, and the README has PyPI, Python version and test badges.

## 0.4.0

- The viewer is on PyPI: `uv tool install codex-transcript-viewer`, `pipx install codex-transcript-viewer`, or `uvx codex-transcript-viewer <session.jsonl>` to run it once.
- Markdown tables render as tables, and links work. Web links open in a new tab. Links to local files show their label, with the full path on hover. Bare `https://` addresses become links too.
- Formatting no longer leaks into code, so `**` inside a code block stays as written.
- Sessions with malformed records no longer crash the parser. A fuzz of every record type turned up ten such spots.
- Tests run on GitHub Actions for Python 3.11 through 3.14.

## 0.3.0

- Goals set with `/goal` show up, with their objective and each status change to complete, paused, blocked or out of budget. Codex logs the goal again after nearly every step, so the viewer keeps only real changes.
- Generated images render, along with the prompt the model used. They count toward the `--max-image-mb` budget.
- Hook messages, review start and result markers, and errors like a usage limit now appear. The viewer lists a review's findings when no reply repeats them.
- Reasoning headings no longer repeat. Newer models restate a turn's earlier headings every time they add one, which made about a quarter of the headings in recent sessions duplicates.
- The viewer skips record types that carry nothing to show, like guardian approvals, subagent status, voice sessions and undo, without reporting them as unrecognized.

## 0.2.2

- 0.2.1 only fixed some of the doubled final answers. Older sessions also log a final answer without its memory citations or proposed plan, and those copies still showed as commentary. They're gone now, along with a doubled last message in older and plan-mode turns.
- `scripts/audit_sessions.py` checks the viewer against real sessions for duplicated or missing entries, and `scripts/visual_review.py` screenshots one session per format. Run over about 8,500 sessions, both come back clean.

## 0.2.1

- Older sessions no longer show each final answer twice. Codex also logged final answers as commentary, and the viewer kept that copy.
- The README screenshot shows the current viewer.

## 0.2.0

It's been a while since I updated this, and Codex has changed a lot in the meantime, so these fixes are long overdue. Newer sessions were missing most of what actually happened.

- Prompts show up again. Codex 0.135 and later store them as `item_completed` records, which the viewer never read. Injected context like the environment block, AGENTS.md and skill text stays out. Thanks to joebb97, who opened #3, and to khoi for reporting it.
- Tool calls now render in full, including code-mode `exec`, `apply_patch`, web searches and tool searches. The viewer colors each result by its real exit status. khoi contributed the name/value grid for tool parameters.
- Tool screenshots render as images. The viewer used to dump them into the page as raw base64, which is why some pages hit 36 MB. It now embeds up to 25 MB of images per page. New flags: `--max-image-mb`, `--no-images`, `--max-output-chars`.
- Duplicate messages no longer slip through when tool calls sit between the two copies, and the Answers filter finds every final answer.
- Subagent threads show the agent and its parent thread. History copied from the parent sits in a collapsed block.
- The viewer prints record types it doesn't recognize to stderr, so the next Codex format change shows up right away.

## 0.1.0

First release.
