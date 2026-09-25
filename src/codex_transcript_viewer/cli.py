"""Command-line interface for converting Codex CLI JSONL sessions to HTML."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .html_builder import build_html
from .parser import extract_conversation, parse_jsonl


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="codex-transcript-viewer",
        description="Convert a Codex CLI JSONL session into a self-contained HTML viewer.",
    )
    parser.add_argument("session", type=Path, help="path to a rollout-*.jsonl session file")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="output HTML path (default: <session-stem>.html in the current directory)",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="show images as labelled placeholders instead of embedding them",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    inpath = args.session
    if not inpath.exists():
        print(f"error: {inpath} not found", file=sys.stderr)
        sys.exit(1)

    outpath = args.output or Path(inpath.stem + ".html")

    entries = parse_jsonl(inpath)
    meta, events = extract_conversation(entries)
    html_content = build_html(meta, events, embed_images=not args.no_images)

    outpath.write_text(html_content, encoding="utf-8")
    size = outpath.stat().st_size
    print(f"written to {outpath} ({size:,} bytes, {len(events)} events)")


if __name__ == "__main__":
    main()
