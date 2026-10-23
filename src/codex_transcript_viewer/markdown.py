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
# Input NULs are replaced first, so only real placeholders contain one.
_SLOT = "\x00{}\x00"
_SLOT_RE = re.compile("\x00(\\d+)\x00")

# A link target never holds a placeholder, so no parked markup can end up
# inside an attribute. Targets may hold one level of balanced parentheses, or
# be wrapped in <> to allow spaces.
_LINK_RE = re.compile(
    r"\[([^\[\]\n]+)\]\((?:&lt;([^\n\x00]*?)&gt;|((?:[^()\s\x00]|\([^()\s\x00]*\))+))\)"
)
_AUTOLINK_RE = re.compile(r"&lt;(https?://(?:&amp;|[^\s&\x00])+?)&gt;")
# Text is escaped by now, so "&" only starts an entity; &amp; is the one that
# belongs to a URL, and &quot; &#x27; &gt; end it.
_BARE_URL_RE = re.compile(r"(?<![\w/=])https?://(?:&amp;|[^\s<&\x00])+")
_URL_TRAILING = ".,;:!?]*_"
_WEB_URL_RE = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")

# Emphasis needs a non-space character just inside each delimiter, so
# "2 * 3 * 4" stays arithmetic. Bold may not contain "**" and italics may not
# contain "*", which also keeps both linear on long unmatched runs.
_BOLD_RE = re.compile(r"\*\*(?=[^\s*])((?:[^*\n]|\*(?!\*))+?)(?<=[^\s*])\*\*")
_ITALIC_RE = re.compile(r"(?<![*\w])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![*\w])")

# A fence opens with three or more backticks or tildes at the start of a line
# (after any indent) and closes with a line of at least as many of the same
# character. A backtick fence's info string can't contain a backtick.
_FENCE_OPEN_RE = re.compile(r"^( *)(`{3,}|~{3,})(.*)$")
_LANG_RE = re.compile(r"[\w+#-]*")


def _fence_close(line: str, fence: str) -> bool:
    stripped = line.strip(" \t")
    return (
        len(stripped) >= len(fence)
        and set(stripped) == {fence[0]}
    )


def fenced_blocks(lines: list[str]) -> list[tuple[int, int | None, str, str]]:
    """(open line, close line or None, indent, language) of each fenced code block.

    An unclosed fence runs to the end of the text, as in CommonMark.
    """
    blocks = []
    i = 0
    while i < len(lines):
        match = _FENCE_OPEN_RE.match(lines[i])
        if not match or (match.group(2)[0] == "`" and "`" in match.group(3)):
            i += 1
            continue
        indent, fence, info = match.groups()
        lang = _LANG_RE.match(info.strip()).group()
        close = next((j for j in range(i + 1, len(lines)) if _fence_close(lines[j], fence)), None)
        blocks.append((i, close, indent, lang))
        if close is None:
            break
        i = close + 1
    return blocks


def _park_fences(text: str, park) -> str:
    lines = text.split("\n")
    blocks = fenced_blocks(lines)
    if not blocks:
        return text
    out: list[str] = []
    last = 0
    for start, close, indent, lang in blocks:
        out.extend(lines[last:start])
        end = len(lines) if close is None else close
        body = [line[len(indent):] if line.startswith(indent) else line.lstrip(" ")
                for line in lines[start + 1:end]]
        out.append(indent + park(_code_block(lang, "".join(line + "\n" for line in body))))
        last = end + 1
    out.extend(lines[last:])
    return "\n".join(out)


def _emphasis(text: str) -> str:
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    return _ITALIC_RE.sub(r"<em>\1</em>", text)


def render_markdown(text: str) -> str:
    """Convert the markdown Codex writes to HTML.

    Handles fenced code blocks (highlighted for common languages), inline code,
    bold, italic, headers, bullet and numbered lists at any depth, blockquotes,
    links and pipe tables. Intended for session transcript content where full
    CommonMark compliance is unnecessary.
    """
    slots: list[str] = []

    def park(markup: str) -> str:
        # Placeholders inside this markup point at earlier slots; resolving
        # them now means the final restore is a single, bounded pass.
        slots.append(_SLOT_RE.sub(lambda m: slots[int(m.group(1))], markup))
        return _SLOT.format(len(slots) - 1)

    text = (text if isinstance(text, str) else str(text or "")).replace("\x00", "\ufffd")
    escaped = escape(_park_fences(text, park))

    # Inline code
    escaped = re.sub(r"`([^`\n]+)`", lambda m: park(f"<code>{m.group(1)}</code>"), escaped)

    # Links: [text](target), <https://...>, and bare web addresses
    escaped = _LINK_RE.sub(
        lambda m: park(_link(m.group(1), m.group(2) if m.group(2) is not None else m.group(3))),
        escaped,
    )
    escaped = _AUTOLINK_RE.sub(lambda m: park(_link(m.group(1), m.group(1))), escaped)
    escaped = _BARE_URL_RE.sub(lambda m: _bare_url(m.group(), park), escaped)

    # List items, before emphasis so a "* " bullet is not read as italics
    escaped = re.sub(r"^( *)[-*+] (?=\S)", _bullet, escaped, flags=re.MULTILINE)
    escaped = re.sub(
        r"^( *)(\d+)[.)] (?=\S)",
        lambda m: park(f'{m.group(1)}<span class="md-list-number">{m.group(2)}.</span> '),
        escaped,
        flags=re.MULTILINE,
    )

    escaped = _emphasis(escaped)

    escaped = re.sub(
        r"^(#{1,6}) (.+)$",
        lambda m: f"<h{len(m.group(1))}>{m.group(2)}</h{len(m.group(1))}>",
        escaped,
        flags=re.MULTILINE,
    )

    escaped = _render_blockquotes(escaped)
    escaped = _render_tables(escaped)

    escaped = _SLOT_RE.sub(lambda m: slots[int(m.group(1))], escaped)
    # A code block has its own margins, so the line breaks around it only add gaps;
    # a paragraph break after one would otherwise render as an extra blank line.
    return re.sub(r"\n?(<pre><code[^>]*>.*?</code></pre>)\n{0,2}", r"\1", escaped, flags=re.S)


def _bare_url(url: str, park) -> str:
    """Link a bare address, leaving trailing punctuation and unbalanced ")" outside."""
    tail = ""
    while url:
        if url[-1] in _URL_TRAILING or (url[-1] == ")" and url.count("(") < url.count(")")):
            url, tail = url[:-1], url[-1] + tail
        else:
            break
    if len(url) <= len("https://") or url.endswith("://"):
        return url + tail
    return park(_link(url, url)) + tail


def _code_block(lang: str, code: str) -> str:
    highlighted = highlight(code, lang) if lang else None
    return f'<pre><code class="language-{lang}">{highlighted or html.escape(code)}</code></pre>'


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


_CITATION_TAG = "<oai-mem-citation>"
_CITATION_BLOCK_RE = re.compile(r"<oai-mem-citation>(.*?)(?:</oai-mem-citation>|\Z)", re.S)
_CITATION_SECTION_RE = re.compile(r"<(citation_entries|rollout_ids)>(.*?)(?:</\w+>|\Z)", re.S)


def _fence_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of the fenced code blocks in raw text."""
    lines = text.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    return [
        (offsets[start], offsets[len(lines) if close is None else close + 1])
        for start, close, _, _ in fenced_blocks(lines)
    ]


def split_memory_citations(text: str) -> tuple[str, list[dict], list[str]]:
    """Take Codex's memory-citation blocks out of an answer.

    Returns the answer without them, the cited entries as
    {"location", "note"} dicts, and the ids of the sessions they came from.
    A block quoted inside a fenced code block is an example and stays put.
    """
    if not isinstance(text, str) or _CITATION_TAG not in text:
        return text, [], []
    fences = _fence_spans(text)
    entries: list[dict] = []
    rollouts: list[str] = []
    kept: list[str] = []
    last = 0
    for tag in re.finditer(re.escape(_CITATION_TAG), text):
        if tag.start() < last or any(start <= tag.start() < end for start, end in fences):
            continue
        block = _CITATION_BLOCK_RE.match(text, tag.start())
        kept.append(text[last:tag.start()].rstrip())
        last = block.end()
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
    kept.append(text[last:])
    return "".join(kept).rstrip(), entries, rollouts


def _link(label: str, target: str) -> str:
    """Web links open in a new tab; file paths can't be followed from a saved
    page, so they show their label with the full path on hover."""
    label = _emphasis(label)
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
