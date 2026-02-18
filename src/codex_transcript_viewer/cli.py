"""Command-line interface for converting Codex CLI JSONL sessions to HTML."""

from __future__ import annotations

import sys
from pathlib import Path

from .html_builder import build_html
from .parser import extract_conversation, parse_jsonl


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: codex-transcript-viewer <session.jsonl> [output.html]")
        sys.exit(1)

    inpath = Path(sys.argv[1])
    if not inpath.exists():
        print(f"error: {inpath} not found", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) >= 3:
        outpath = Path(sys.argv[2])
    else:
        outpath = Path(inpath.stem + ".html")

    entries = parse_jsonl(inpath)
    meta, events = extract_conversation(entries)
    html_content = build_html(meta, events)

    outpath.write_text(html_content, encoding="utf-8")
    size = outpath.stat().st_size
    print(f"written to {outpath} ({size:,} bytes, {len(events)} events)")


if __name__ == "__main__":
    main()
