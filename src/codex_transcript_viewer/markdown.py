"""Lightweight markdown-to-HTML conversion for session transcripts."""

from __future__ import annotations

import html
import re

from .highlight import highlight


def escape(text: str | None) -> str:
    """HTML-escape text, returning empty string for None."""
    return html.escape(str(text)) if text else ""


# Rendered pieces are parked behind placeholders so later passes (emphasis,
# links, tables) never look inside code or inside markup already produced.
_SLOT = "\x00{}\x00"
_SLOT_RE = re.compile("\x00(\\d+)\x00")

_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_AUTOLINK_RE = re.compile(r"&lt;(https?://[^\s&]+)&gt;")
_BARE_URL_RE = re.compile(r"(?<![\w/=\"'])(https?://[^\s<\x00]*[^\s<\x00.,;:!?)\]'\"])")
_WEB_URL_RE = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def render_markdown(text: str) -> str:
    """Convert the markdown Codex writes to HTML.

    Handles fenced code blocks (highlighted for common languages), inline code,
    bold, italic, headers, bullet and numbered lists at any depth, blockquotes,
    links and pipe tables. Intended for session transcript content where full
    CommonMark compliance is unnecessary.
    """
    slots: list[str] = []

    def park(markup: str) -> str:
        slots.append(markup)
        return _SLOT.format(len(slots) - 1)

    escaped = escape(text)

    # Fenced code blocks (```lang ... ```)
    escaped = re.sub(r"```([\w+#-]*)\n(.*?)```", lambda m: park(_code_block(*m.groups())), escaped, flags=re.DOTALL)

    # Inline code
    escaped = re.sub(r"`([^`\n]+)`", lambda m: park(f"<code>{m.group(1)}</code>"), escaped)

    # Links: [text](target), <https://...>, and bare web addresses
    escaped = _LINK_RE.sub(lambda m: park(_link(m.group(1), m.group(2))), escaped)
    escaped = _AUTOLINK_RE.sub(lambda m: park(_link(m.group(1), m.group(1))), escaped)
    escaped = _BARE_URL_RE.sub(lambda m: park(_link(m.group(1), m.group(1))), escaped)

    # List items, before emphasis so a "* " bullet is not read as italics
    escaped = re.sub(r"^( *)[-*+] (?=\S)", _bullet, escaped, flags=re.MULTILINE)
    escaped = re.sub(
        r"^( *)(\d+)[.)] (?=\S)",
        lambda m: park(f'{m.group(1)}<span class="md-list-number">{m.group(2)}.</span> '),
        escaped,
        flags=re.MULTILINE,
    )

    # Bold
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)

    # Italic (single asterisk, not adjacent to another asterisk)
    escaped = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped
    )

    # Headers (h3 before h2 before h1 to avoid prefix conflicts)
    escaped = re.sub(
        r"^### (.+)$", r"<h3>\1</h3>", escaped, flags=re.MULTILINE
    )
    escaped = re.sub(
        r"^## (.+)$", r"<h2>\1</h2>", escaped, flags=re.MULTILINE
    )
    escaped = re.sub(
        r"^# (.+)$", r"<h1>\1</h1>", escaped, flags=re.MULTILINE
    )

    escaped = _render_blockquotes(escaped)
    escaped = _render_tables(escaped)

    # Restore parked markup; link text may itself hold parked inline code.
    while _SLOT_RE.search(escaped):
        escaped = _SLOT_RE.sub(lambda m: slots[int(m.group(1))], escaped)
    # A code block has its own margins, so the line breaks around it only add gaps;
    # a paragraph break after one would otherwise render as an extra blank line.
    return re.sub(r"\n?(<pre><code[^>]*>.*?</code></pre>)\n{0,2}", r"\1", escaped, flags=re.S)


def _code_block(lang: str, escaped_code: str) -> str:
    highlighted = highlight(html.unescape(escaped_code), lang) if lang else None
    return f'<pre><code class="language-{lang}">{highlighted or escaped_code}</code></pre>'


_BULLETS = ("\u2022", "\u25e6", "\u25aa")


def _bullet(match: re.Match) -> str:
    """Bullets change shape with depth: two spaces of indent per level."""
    indent = match.group(1)
    return indent + _BULLETS[min(len(indent) // 2, len(_BULLETS) - 1)] + " "


def _render_blockquotes(text: str) -> str:
    """Group consecutive "> " lines into one blockquote."""
    out: list[str] = []
    quote: list[str] = []

    def flush() -> None:
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()

    for line in text.split("\n"):
        if line.startswith("&gt;"):
            quote.append(line[4:][1:] if line[4:5] == " " else line[4:])
        else:
            flush()
            out.append(line)
    flush()
    return re.sub("(</blockquote>)\n", r"\1", "\n".join(out))


_CITATION_BLOCK_RE = re.compile(r"\s*<oai-mem-citation>(.*?)(?:</oai-mem-citation>|\Z)", re.S)
_CITATION_SECTION_RE = re.compile(r"<(citation_entries|rollout_ids)>(.*?)(?:</\w+>|\Z)", re.S)


def split_memory_citations(text: str) -> tuple[str, list[dict], list[str]]:
    """Take Codex's memory-citation blocks out of an answer.

    Returns the answer without them, the cited entries as
    {"location", "note"} dicts, and the ids of the sessions they came from.
    """
    entries: list[dict] = []
    rollouts: list[str] = []
    for block in _CITATION_BLOCK_RE.finditer(text):
        for section, body in _CITATION_SECTION_RE.findall(block.group(1)):
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("<"):
                    continue
                if section == "rollout_ids":
                    rollouts.append(line)
                    continue
                location, _, note = line.partition("|note=")
                entries.append({"location": location.strip(), "note": note.strip().strip("[]")})
    if not entries and not rollouts:
        return text, [], []
    return _CITATION_BLOCK_RE.sub("", text).rstrip(), entries, rollouts


def _link(label: str, target: str) -> str:
    """Web links open in a new tab; file paths can't be followed from a saved
    page, so they show their label with the full path on hover."""
    if _WEB_URL_RE.match(target):
        return f'<a href="{target}" target="_blank" rel="noopener noreferrer">{label}</a>'
    return f'<span class="md-path" title="{target}">{label}</span>'


def _split_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells = re.split(r"(?<!\\)\|", row)
    return [cell.strip().replace("\\|", "|") for cell in cells]


def _alignment(spec: str) -> str:
    spec = spec.strip()
    if spec.startswith(":") and spec.endswith(":"):
        return "center"
    if spec.endswith(":"):
        return "right"
    return ""


def _render_tables(text: str) -> str:
    """Turn pipe tables (a header row, a --- separator, then rows) into HTML."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        header = lines[i]
        if (
            "|" in header
            and i + 1 < len(lines)
            and "-" in lines[i + 1]
            and _TABLE_SEPARATOR_RE.match(lines[i + 1])
            and len(_split_row(lines[i + 1])) == len(_split_row(header))
        ):
            heads = _split_row(header)
            aligns = [_alignment(spec) for spec in _split_row(lines[i + 1])]
            rows = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = _split_row(lines[i])
                cells = (cells + [""] * len(heads))[: len(heads)]
                rows.append(cells)
                i += 1
            out.append(_table_html(heads, aligns, rows))
            continue
        out.append(header)
        i += 1
    # A table is a block, so drop the line break that would follow it.
    return re.sub("(</table>)\n", r"\1", "\n".join(out))


def _table_html(heads: list[str], aligns: list[str], rows: list[list[str]]) -> str:
    def cell(tag: str, content: str, align: str) -> str:
        style = f' style="text-align:{align}"' if align else ""
        return f"<{tag}{style}>{content}</{tag}>"

    head = "".join(cell("th", h, a) for h, a in zip(heads, aligns))
    body = "".join(
        "<tr>" + "".join(cell("td", c, a) for c, a in zip(row, aligns)) + "</tr>"
        for row in rows
    )
    return f'<table class="md-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
