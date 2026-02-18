# codex-transcript-viewer

Converts Codex CLI JSONL session transcripts into self-contained HTML files you can open in any browser. The output is a single `.html` file with no external dependencies -- sidebar navigation, search, filtering, and all styling are baked in.

## Install

```
git clone https://github.com/masonc15/codex-transcript-viewer.git
uv tool install ./codex-transcript-viewer
```

Or run directly without installing:

```
uv run --directory ./codex-transcript-viewer codex-transcript-viewer <session.jsonl>
```

## Usage

```
codex-transcript-viewer <session.jsonl> [output.html]
```

If you omit the output path it writes `<input-stem>.html` in the current directory.

Codex stores sessions as JSONL files under `~/.codex/sessions/`. Find one and point the tool at it:

```
codex-transcript-viewer ~/.codex/sessions/2026/02/18/rollout-2026-02-18T10-06-22-019c7149.jsonl
open rollout-2026-02-18T10-06-22-019c7149.html
```

## What the viewer shows

The HTML output has a sticky sidebar on the left with a scrollable event tree and a main content area on the right. Each event type gets its own visual treatment:

- User messages with green left border
- Final answers highlighted with a subtle green background
- Commentary (intermediary updates) in italic with a muted border
- Reasoning summaries in gray italic
- Tool calls showing the command or JSON arguments
- Tool outputs with click-to-expand for long content
- System events (turn started, aborted, rolled back) in dim text
- Token usage counters

The sidebar supports text search and preset filters (Default, No tools, User only, Answers, All). On mobile the sidebar collapses behind a hamburger menu.

## Credits

Inspired by the HTML session export in [pi](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent), a coding agent by [@badlogic](https://github.com/badlogic).

## Project structure

```
src/codex_transcript_viewer/
  parser.py       - JSONL parsing and event extraction
  markdown.py     - lightweight markdown-to-HTML conversion
  formatting.py   - timestamp formatting helpers
  html_builder.py - assembles the final HTML from events
  style.css       - all CSS for the viewer
  viewer.js       - sidebar filtering and navigation
  cli.py          - command-line entry point
```
