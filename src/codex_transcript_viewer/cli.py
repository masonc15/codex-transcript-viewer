"""Command-line interface for converting Codex CLI JSONL sessions to HTML."""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from .html_builder import DEFAULT_IMAGE_BUDGET_MB, DEFAULT_MAX_OUTPUT_CHARS, build_html
from .parser import extract_conversation, iter_jsonl, unrecognized_kind


def _non_negative(kind):
    """argparse type for a finite number >= 0."""

    def parse(text: str):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected a number, got {text!r}") from None
        if not math.isfinite(value) or value < 0:
            raise argparse.ArgumentTypeError(f"expected 0 or more, got {text!r}")
        return value

    return parse


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
        type=_non_negative(float),
        default=DEFAULT_IMAGE_BUDGET_MB,
        metavar="N",
        help=(
            "embed tool-output images until they total N MB, then show placeholders "
            f"(default {DEFAULT_IMAGE_BUDGET_MB}; 0 means no limit). Prompt images are always embedded."
        ),
    )
    parser.add_argument(
        "--max-output-chars",
        type=_non_negative(int),
        default=DEFAULT_MAX_OUTPUT_CHARS,
        metavar="N",
        help=f"cap each tool output at N characters (default {DEFAULT_MAX_OUTPUT_CHARS:,}; 0 means no cap)",
    )
    return parser.parse_args(argv)


def _counting_unknown(entries: Iterable[dict], unknown: Counter) -> Iterator[dict]:
    """Pass records through while tallying kinds the parser doesn't recognize.

    The file is read once and never held in memory as a whole.
    """
    for entry in entries:
        kind = unrecognized_kind(entry)
        if kind is not None:
            unknown[kind] += 1
        yield entry


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return a.resolve() == b.resolve()


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    inpath = args.session
    if not inpath.exists():
        _fail(f"{inpath} not found")
    if not inpath.is_file():
        _fail(f"{inpath} is not a file")

    outpath = args.output or Path(inpath.stem + ".html")
    if outpath.exists() and _same_file(inpath, outpath):
        _fail(f"{outpath} is the session itself; choose another output path")
    if outpath.is_dir():
        _fail(f"{outpath} is a directory; give a file path")
    if not outpath.parent.is_dir():
        _fail(f"{outpath.parent} does not exist")

    unknown: Counter = Counter()
    meta, events = extract_conversation(_counting_unknown(iter_jsonl(inpath), unknown))
    html_content = build_html(
        meta,
        events,
        embed_images=not args.no_images,
        max_image_mb=args.max_image_mb,
        max_output_chars=args.max_output_chars,
    )

    try:
        outpath.write_text(html_content, encoding="utf-8")
    except OSError as exc:
        _fail(f"could not write {outpath}: {exc.strerror or exc}")
    size = outpath.stat().st_size
    print(f"written to {outpath} ({size:,} bytes, {len(events)} events)")

    if unknown:
        summary = ", ".join(f"{kind} x{count}" for kind, count in unknown.most_common())
        print(f"skipped unrecognized records: {summary}", file=sys.stderr)


if __name__ == "__main__":
    main()
