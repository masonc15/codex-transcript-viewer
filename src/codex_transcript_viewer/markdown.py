"""Lightweight markdown-to-HTML conversion for session transcripts."""

from __future__ import annotations

import html
import re


def escape(text: str | None) -> str:
    """HTML-escape text, returning empty string for None."""
    return html.escape(str(text)) if text else ""


def render_markdown(text: str) -> str:
    """Convert basic markdown to HTML.

    Handles fenced code blocks, inline code, bold, italic, headers, and
    unordered lists. Intended for session transcript content where full
    CommonMark compliance is unnecessary.
    """
    escaped = escape(text)

    # Fenced code blocks (```lang ... ```)
    def _replace_code_block(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = m.group(2)
        return f'<pre><code class="language-{lang}">{code}</code></pre>'

    escaped = re.sub(
        r"```(\w*)\n(.*?)```", _replace_code_block, escaped, flags=re.DOTALL
    )

    # Inline code
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

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

    # Unordered list items
    escaped = re.sub(r"^- (.+)$", r"• \1", escaped, flags=re.MULTILINE)

    return escaped
