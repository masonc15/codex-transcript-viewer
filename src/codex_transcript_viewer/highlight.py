"""A small built-in syntax highlighter for fenced code blocks.

Pages stay self-contained with no dependencies, so this is a regex tokenizer
for the languages Codex writes most, not a real lexer. It colors comments,
strings, numbers and keywords; diffs get added, removed and hunk lines.
"""

from __future__ import annotations

import html
import re

_PY_KEYWORDS = (
    "False None True and as assert async await break class continue def del elif else "
    "except finally for from global if import in is lambda match nonlocal not or pass "
    "raise return try while with yield case self"
)
_SHELL_KEYWORDS = (
    "if then else elif fi for while until do done case esac function in select return "
    "export local readonly declare set unset source exit break continue"
)
_JS_KEYWORDS = (
    "async await break case catch class const continue debugger default delete do else "
    "enum export extends false finally for from function if implements import in "
    "instanceof interface let new null of private protected public readonly return static "
    "super switch this throw true try type typeof undefined var void while with yield as"
)
_C_LIKE_KEYWORDS = (
    "as async await break case catch class const continue crate default defer do else enum "
    "extension extern false fileprivate final fn for func func go guard if impl import in "
    "init interface internal let loop match mod move mut nil null override package private "
    "protocol pub public return select self Self static struct super switch throw throws "
    "trait true try type typealias unsafe use var void where while"
)
_SQL_KEYWORDS = (
    "select from where and or not insert into values update set delete create table index "
    "drop alter join left right inner outer on group by order having limit offset as "
    "distinct union all null is in like between case when then else end primary key"
)

_STRINGS = r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\''
_NUMBER = r"\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b"


def _spec(comment: str, strings: str, keywords: str, flags: int = 0) -> re.Pattern:
    words = "|".join(sorted(set(keywords.split()), key=len, reverse=True))
    return re.compile(
        rf"(?P<comment>{comment})|(?P<string>{strings})|(?P<number>{_NUMBER})"
        rf"|(?P<keyword>\b(?:{words})\b)",
        flags,
    )


_PYTHON = _spec(r"#[^\n]*", r'[rRbBfFuU]{0,2}(?:"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|' + _STRINGS + ")", _PY_KEYWORDS)
_SHELL = _spec(r"(?<![\w$\\{])#[^\n]*", _STRINGS, _SHELL_KEYWORDS)
_JS = _spec(r"//[^\n]*|/\*[\s\S]*?\*/", _STRINGS + r"|`(?:[^`\\]|\\.)*`", _JS_KEYWORDS)
_C_LIKE = _spec(r"//[^\n]*|/\*[\s\S]*?\*/", _STRINGS, _C_LIKE_KEYWORDS)
_JSON = _spec(r"(?!)", r'"(?:[^"\\\n]|\\.)*"', "true false null")
_CONFIG = _spec(r"#[^\n]*", _STRINGS, "true false yes no null on off")
_SQL = _spec(r"--[^\n]*", _STRINGS, _SQL_KEYWORDS, re.IGNORECASE)

_LANGUAGES = {
    **dict.fromkeys(("python", "py", "python3"), _PYTHON),
    **dict.fromkeys(("bash", "sh", "shell", "zsh", "console", "shellscript"), _SHELL),
    **dict.fromkeys(("javascript", "js", "jsx", "mjs", "cjs", "typescript", "ts", "tsx"), _JS),
    **dict.fromkeys(
        ("rust", "rs", "go", "golang", "swift", "c", "cpp", "h", "java", "kotlin", "kt", "cs", "csharp"),
        _C_LIKE,
    ),
    **dict.fromkeys(("json", "jsonc", "jsonl"), _JSON),
    **dict.fromkeys(("toml", "yaml", "yml", "ini", "conf"), _CONFIG),
    "sql": _SQL,
}


def highlight(code: str, lang: str) -> str | None:
    """Escaped, highlighted HTML for ``code``, or None for an unknown language."""
    lang = lang.lower()
    if lang in ("diff", "patch"):
        return _highlight_diff(code)
    pattern = _LANGUAGES.get(lang)
    if pattern is None:
        return None
    out: list[str] = []
    last = 0
    for match in pattern.finditer(code):
        out.append(html.escape(code[last : match.start()]))
        out.append(f'<span class="tok-{match.lastgroup}">{html.escape(match.group())}</span>')
        last = match.end()
    out.append(html.escape(code[last:]))
    return "".join(out)


def _highlight_diff(code: str) -> str:
    lines = []
    for line in code.split("\n"):
        kind = None
        if line.startswith(("+++", "---")):
            kind = "meta"
        elif line.startswith("+"):
            kind = "added"
        elif line.startswith("-"):
            kind = "removed"
        elif line.startswith("@@"):
            kind = "hunk"
        escaped = html.escape(line)
        lines.append(f'<span class="tok-{kind}">{escaped}</span>' if kind else escaped)
    return "\n".join(lines)
