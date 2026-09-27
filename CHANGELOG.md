# Changelog

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
