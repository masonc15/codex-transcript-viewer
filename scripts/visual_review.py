"""Render sessions, check the page for repeated entries, and screenshot the sidebar.

Usage:
    uv run --with playwright python scripts/visual_review.py SESSION.jsonl ... [--out DIR]
    uv run --with playwright python scripts/visual_review.py --from-audit report.json [--out DIR]

--from-audit takes the JSON written by audit_sessions.py --json and picks one
local session per format (the one with the most assistant entries), so every
format the archive contains gets looked at before a release.

For each session this writes <name>-sidebar-N.png tiles covering the first
--rows sidebar entries, and prints any turn where two message entries carry the
same text in the rendered page. A repeat between two kinds of entry in the
same role (commentary and a final answer, say) is how an event_msg copy
slipping past dedup looks, and fails the run. Other repeats are listed for the
reviewer: within one kind it is usually the model repeating itself, and across
roles a prompt quoting a reply. audit_sessions.py checks both against the raw
records. Look at the tiles too: a repeated line in the sidebar is the
quickest duplicate to spot by eye. The screenshots show private session text;
keep them out of the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from codex_transcript_viewer.html_builder import build_html  # noqa: E402
from codex_transcript_viewer.parser import extract_conversation, parse_jsonl  # noqa: E402

# Runs in the page. Splits the sidebar into turns at each "Turn started" row and
# compares the full rendered text of message entries, not the 60-character
# previews. A long entry that starts another one (40+ characters) also counts,
# since that is how a copy with a dropped trailing block looks.
FIND_REPEATS = """
([maxRows, filter]) => {
  setFilter(filter, document.querySelector(`[data-filter="${filter}"]`));
  const norm = s => s.replace(/\\s+/g, ' ').trim();
  const repeats = [];
  let turn = 0, seen = new Map();
  const nodes = [...document.querySelectorAll('.tree-container .tree-node')];
  for (const node of nodes) {
    const label = node.textContent;
    if (label.includes('Turn started')) { turn += 1; seen = new Map(); continue; }
    const role = [...node.classList].find(c => c.startsWith('tree-role-'));
    if (!['tree-role-user', 'tree-role-assistant', 'tree-role-thinking'].includes(role)) continue;
    const kind = role.replace('tree-role-', '') + ':' + (node.dataset.kind || node.querySelector('.tree-content').textContent.trim().slice(0, 2));
    const target = document.getElementById((node.getAttribute('href') || '').slice(1));
    if (!target) continue;
    const body = target.querySelector('.markdown-content, .thinking-text') || target;
    const text = norm(body.innerText || '');
    if (!text) continue;
    for (const [other, otherKind] of seen) {
      const [shorter, longer] = other.length <= text.length ? [other, text] : [text, other];
      if (shorter === longer || (shorter.length >= 40 && longer.startsWith(shorter))) {
        const sameRole = otherKind.split(':')[0] === kind.split(':')[0];
        repeats.push({turn, kinds: [otherKind, kind], cross: sameRole && otherKind !== kind,
                      text: text.slice(0, 80)});
        break;
      }
    }
    seen.set(text, kind);
  }
  // Let the entry list grow to its full height so it can be screenshotted.
  const sidebar = document.getElementById('sidebar');
  sidebar.style.position = 'static';
  sidebar.style.height = 'auto';
  sidebar.style.alignSelf = 'flex-start';
  const tree = sidebar.querySelector('.tree-container');
  tree.style.overflow = 'visible';
  tree.style.flex = 'none';
  nodes.filter(n => n.style.display !== 'none').slice(maxRows).forEach(n => n.style.display = 'none');
  return {rows: nodes.length, repeats};
}
"""

TILE_HEIGHT = 2400


def pick_from_audit(report_path: Path) -> list[Path]:
    best: dict[str, tuple[int, str]] = {}
    for report in json.loads(report_path.read_text()):
        path = report["session"]
        if not Path(path).is_file():
            continue
        shown = report.get("totals", {}).get("assistant.shown", 0)
        if shown > best.get(report["format"], (-1, ""))[0]:
            best[report["format"]] = (shown, path)
    for fmt, (_, path) in sorted(best.items()):
        print(f"{fmt}: {path}")
    return [Path(path) for _, path in best.values()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sessions", nargs="*", type=Path)
    ap.add_argument("--from-audit", type=Path, help="audit_sessions.py --json output")
    ap.add_argument("--out", type=Path, default=Path("visual-review"))
    ap.add_argument("--rows", type=int, default=200, help="sidebar rows to screenshot")
    ap.add_argument("--filter", default="no-tools",
                    help="sidebar filter for the screenshots: default, no-tools, user-only, answers, all")
    args = ap.parse_args(argv)

    sessions = list(args.sessions)
    if args.from_audit:
        sessions += pick_from_audit(args.from_audit)
    if not sessions:
        ap.error("give session files or --from-audit")

    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    found = 0
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for session in sessions:
            meta, events = extract_conversation(parse_jsonl(session))
            html_path = Path(tmp) / f"{session.stem}.html"
            html_path.write_text(build_html(meta, events), encoding="utf-8")
            page.goto(html_path.as_uri(), wait_until="load", timeout=120_000)
            result = page.evaluate(FIND_REPEATS, [args.rows, args.filter])

            box = page.locator(".tree-container").bounding_box()
            tiles = []
            if box:
                page.set_viewport_size({"width": 1440, "height": TILE_HEIGHT})
                top = 0
                while top < box["height"]:
                    height = min(TILE_HEIGHT, box["height"] - top)
                    tile = args.out / f"{session.stem}-sidebar-{len(tiles) + 1}.png"
                    page.screenshot(path=str(tile), full_page=True, clip={
                        "x": box["x"], "y": box["y"] + top, "width": box["width"], "height": height})
                    tiles.append(tile)
                    top += TILE_HEIGHT
                page.set_viewport_size({"width": 1440, "height": 900})

            cross = [r for r in result["repeats"] if r["cross"]]
            found += len(cross)
            print(f"== {session.name}: {result['rows']} sidebar rows, {len(tiles)} tiles in {args.out}")
            print(f"   {len(cross)} repeats between entry kinds in one role, "
                  f"{len(result['repeats']) - len(cross)} other repeats")
            for repeat in (cross + [r for r in result["repeats"] if not r["cross"]])[:10]:
                print(f"   turn {repeat['turn']}: {' + '.join(repeat['kinds'])} {repeat['text']!r}")
        browser.close()
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
