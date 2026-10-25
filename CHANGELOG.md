# Changelog

## Unreleased

A full review of 0.5.0, mine plus an independent one, turned up 28 problems. This fixes all of them.

- Opening a crafted session could run script: a timestamp that didn't parse, a rollback count, or text imitating the markdown renderer's internal placeholders was written into the page unescaped. That text could also hang the renderer or crash it.
- Older sessions show their final answers in the Answers filter. Sessions from before Codex labelled the answer showed none; now a `task_complete` marks the message it repeats, and otherwise an exchange's last message is its answer when no tool call follows it. Event-only turns no longer show the answer twice.
- A review's findings stay on the page unless the reply really repeats them, and even then they're folded away rather than gone. Before, any reply hid them, including one that only said it would fix them.
- Code fences close only on a real closing line, so backticks inside code no longer end the block; longer fences and `~~~` work too. A memory-citation example inside a code block stays in the answer instead of being cut out along with everything after it.
- Links stop at a closing quote, angle links keep their query string, and targets can hold balanced parentheses or be wrapped in `<>`. `2 * 3 * 4` stays arithmetic, `####` to `######` headings render, and bold works inside link labels.
- Long tool outputs have a Show all button, selecting text in an output no longer collapses it, and outputs and images can be toggled from the keyboard. On a phone the sidebar fits the screen and closes after you pick an entry.
- Commentary from newer sessions is styled as commentary, and each reasoning heading shows once per turn, even when the model restates it out of order.
- A tool result with an `output` field keeps its other fields. A message's text blocks are one entry. A legacy prompt with different text from the item prompt in its turn is kept, and a prompt's image stays with the copy that's shown. A forked subagent's header shows its own starting model.
- Malformed sessions no longer crash the viewer: invalid UTF-8, lines that aren't objects, null or odd fields, infinite numbers. Timestamps with an offset are converted to UTC. `response_item/additional_tools` records, which Codex started writing in October, are skipped instead of reported as unknown.
- The CLI won't overwrite the session it reads, and rejects negative or non-finite limits and bad paths with a message instead of a traceback. It streams the file, so a 371 MB session peaks at 550 MB of memory instead of 1.3 GB, and a turn with thousands of messages no longer takes seconds.
- `scripts/audit_sessions.py` fails on unrecognized record kinds, on paths that don't exist, and when nothing was audited. It reports a crash in one session instead of stopping, and it checks that every turn whose log names an answer shows one. `scripts/visual_review.py` finds turns by a page marker instead of by text, and only fails a repeat that involves an event copy.

## 0.5.0

- Short tool outputs no longer appear twice. Every output under 2,000 characters has shown its text twice since the first release; only long outputs were meant to have a preview and a full copy.
- Memory citations at the end of an answer fold into a small "Memory citations" section, listing each cited note and the sessions it came from, instead of printing Codex's raw citation block.
- The header shows the model and reasoning effort the session started with, not just "openai", and a row marks each turn that switches model or effort.
- Code blocks get syntax highlighting for Python, shell, JavaScript and TypeScript, JSON, TOML and YAML, SQL, diffs, and C-like languages. It's built in, so pages stay self-contained.
- Nested and numbered lists and blockquotes render, and reasoning summaries render as markdown instead of showing literal `**`.
- The demo page is rebuilt around a made-up session that shows every kind of entry, with a script to regenerate its screenshots.
- Dependabot keeps the workflow actions current, and the README has PyPI, Python version and test badges.

## 0.4.0

- The viewer is on PyPI: `uv tool install codex-transcript-viewer`, `pipx install codex-transcript-viewer`, or `uvx codex-transcript-viewer <session.jsonl>` to run it once.
- Markdown tables render as tables, and links work. Web links open in a new tab; links to local files show their label, with the full path on hover. Bare `https://` addresses become links too.
- Formatting no longer leaks into code: `**` inside a code block stays as written.
- Sessions with malformed records no longer crash the parser. A fuzz of every record type turned up ten such spots.
- Tests run on GitHub Actions for Python 3.11 through 3.14.

## 0.3.0

- Goals set with `/goal` show up, with their objective and each status change (complete, paused, blocked, out of budget). Codex logs the goal again after nearly every step, so only real changes are kept.
- Generated images render, along with the prompt the model used. They count toward the `--max-image-mb` budget.
- Hook messages, review start and result markers, and errors like a usage limit now appear. A review's findings are listed when no reply repeats them.
- Reasoning headings no longer repeat. Newer models restate a turn's earlier headings every time they add one, which made about a quarter of the headings in recent sessions duplicates.
- Record types that carry nothing to show (guardian approvals, subagent status, voice sessions, undo) are skipped quietly instead of being reported as unrecognized.

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
