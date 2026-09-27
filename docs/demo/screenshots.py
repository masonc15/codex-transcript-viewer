"""Render the demo session and save the screenshots docs/demo.md uses.

Run from the repository root after make_session.py:
    uv run --with playwright python docs/demo/screenshots.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from codex_transcript_viewer.html_builder import build_html  # noqa: E402
from codex_transcript_viewer.parser import extract_conversation, parse_jsonl  # noqa: E402

IMAGES = ROOT / "docs" / "images"

# Union of the bounding boxes of the elements between two selectors, inclusive.
SPAN = """
([first, last]) => {
  const all = [...document.querySelectorAll('#messages > *')];
  const a = all.indexOf(first);
  const b = all.indexOf(last);
  const rects = all.slice(a, b + 1).map(el => el.getBoundingClientRect());
  const top = Math.min(...rects.map(r => r.top)) + scrollY - 8;
  const bottom = Math.max(...rects.map(r => r.bottom)) + scrollY + 8;
  const left = Math.min(...rects.map(r => r.left)) - 8;
  const right = Math.max(...rects.map(r => r.right)) + 8;
  return {x: left, y: top, width: right - left, height: bottom - top};
}
"""


def main() -> None:
    from playwright.sync_api import sync_playwright

    meta, events = extract_conversation(parse_jsonl(ROOT / "docs" / "demo" / "session.jsonl"))
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        page_path = Path(tmp) / "demo.html"
        page_path.write_text(build_html(meta, events), encoding="utf-8")
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
        page.goto(page_path.as_uri())
        page.evaluate("() => document.querySelectorAll('img').forEach(i => i.loading = 'eager')")
        page.wait_for_timeout(500)

        def shot(name: str, **kwargs) -> None:
            page.screenshot(path=str(IMAGES / name), **kwargs)
            print("saved", name)

        def messages(name: str, first: str, last: str) -> None:
            ends = [page.locator(sel).first.element_handle() for sel in (first, last)]
            shot(name, full_page=True, clip=page.evaluate(SPAN, ends))

        def sidebar(name: str, filter_name: str = "default", search: str = "") -> None:
            page.evaluate("([f, q]) => { document.getElementById('tree-search').value = q;"
                          " setFilter(f, document.querySelector(`[data-filter=\"${f}\"]`)); }",
                          [filter_name, search])
            box = page.locator("#sidebar").bounding_box()
            # Crop to the last row the filter leaves visible.
            bottom = page.evaluate("() => Math.max(...[...document.querySelectorAll('.tree-node')]"
                                   ".filter(n => n.offsetParent !== null)"
                                   ".map(n => n.getBoundingClientRect().bottom))")
            shot(name, clip={**box, "height": min(box["height"], bottom + 12)})

        shot("overview.png")

        tool = ".tool-execution:has(.tool-name:text-is('exec_command'))"
        messages("tool-calls.png", f"{tool}:has-text('tests/test_export.py')",
                 ".tool-execution:has(.tool-name:text-is('exec_command result')):has-text('12 passed')")

        page.evaluate("() => document.querySelectorAll('.memory-citations').forEach(d => d.open = true)")
        messages("answer.png", ".final-answer", ".final-answer")

        messages("session-events.png", ".session-event:has-text('Switched')",
                 ".session-event:has-text('Goal complete')")
        messages("generated-image.png",
                 ".tool-execution:has(.tool-name:text-is('image_generation'))",
                 ".tool-execution:has(.tool-name:text-is('image_generation result'))")
        messages("review.png", ".session-event:has-text('Review started')",
                 ".session-event:has-text('Review done')")

        sidebar("no-tools-filter.png", "no-tools")
        sidebar("answers-filter.png", "answers")
        sidebar("search.png", "all", "csv")
        browser.close()


if __name__ == "__main__":
    main()
