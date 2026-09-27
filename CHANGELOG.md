# Changelog

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
