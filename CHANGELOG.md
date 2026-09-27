# Changelog

## 0.2.0

This release makes the viewer work on sessions from current Codex versions, which it had been quietly mangling.

Prompts are back. Codex 0.135 and later record what you type as `item_completed` records, which the viewer ignored, so newer sessions showed no prompts at all. It now reads them, while still skipping the injected context (environment, AGENTS.md, skills) that Codex feeds the model alongside your words. Thanks to joebb97 (#3) and khoi for reporting this and sending fixes.

Tool calls are complete and honest. Code-mode `exec`, `apply_patch`, web searches and tool searches now render; before, most tool activity in newer sessions was missing. Calls and results are paired, and the color reflects the real exit status: green for success, red for failure, neutral when the log doesn't say. Previously everything looked successful. Tool parameters show as a readable name/value grid, from khoi.

Images render. Screenshots returned by tools used to be dumped into the page as raw base64 text, which is most of why some pages ran to 36 MB. They're now proper images, embedded up to a 25 MB budget with a note when some are left out. Images attached to prompts are embedded from the copy saved in the session log. New options: `--max-image-mb`, `--no-images`, and `--max-output-chars`.

Duplicate filtering no longer depends on how many tool calls sit between two copies of the same message, which had let duplicate commentary, token counts and final answers through. The Answers filter now finds every final answer.

Subagent threads show the agent's name and parent, and history copied from a parent session is folded away instead of appearing as the subagent's own work. The header also stopped showing the parent's session id for those threads.

The viewer reports record types it doesn't recognize on stderr, so format changes don't go unnoticed.

## 0.1.0

First release.
