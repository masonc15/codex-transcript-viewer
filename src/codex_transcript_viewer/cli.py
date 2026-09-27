"""Command-line interface for converting Codex CLI JSONL sessions to HTML."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .html_builder import DEFAULT_IMAGE_BUDGET_MB, DEFAULT_MAX_OUTPUT_CHARS, build_html
from .parser import extract_conversation, parse_jsonl, unrecognized_record_kinds


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
    parser.add_argument(
        "--max-image-mb",
        type=float,
        default=DEFAULT_IMAGE_BUDGET_MB,
        metavar="N",
        help=(
            "embed tool-output images until they total N MB, then show placeholders "
            f"(default {DEFAULT_IMAGE_BUDGET_MB}; 0 means no limit). Prompt images are always embedded."
        ),
    )
    parser.add_argument(
        "--max-output-chars",
        type=int,
        default=DEFAULT_MAX_OUTPUT_CHARS,
        metavar="N",
        help=f"cap each tool output at N characters (default {DEFAULT_MAX_OUTPUT_CHARS:,}; 0 means no cap)",
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
    html_content = build_html(
        meta,
        events,
        embed_images=not args.no_images,
        max_image_mb=args.max_image_mb,
        max_output_chars=args.max_output_chars,
    )

    outpath.write_text(html_content, encoding="utf-8")
    size = outpath.stat().st_size
    print(f"written to {outpath} ({size:,} bytes, {len(events)} events)")

    unknown = unrecognized_record_kinds(entries)
    if unknown:
        summary = ", ".join(f"{kind} x{count}" for kind, count in unknown.most_common())
        print(f"skipped unrecognized records: {summary}", file=sys.stderr)


if __name__ == "__main__":
    main()
