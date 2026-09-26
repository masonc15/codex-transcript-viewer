"""Build a self-contained HTML viewer from parsed Codex session events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from importlib import resources

from .formatting import format_ts, format_ts_full
from .markdown import escape, render_markdown


def _load_asset(name: str) -> str:
    """Load a bundled CSS or JS asset from the package."""
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


@dataclass
class RenderContext:
    """Options and running state shared by the per-event renderers."""

    embed_images: bool = True


def build_html(
    meta: dict | None,
    events: list[dict],
    *,
    embed_images: bool = True,
) -> str:
    """Build a self-contained HTML string from session metadata and events."""
    ctx = RenderContext(embed_images=embed_images)
    session_id = meta.get("id", "unknown") if meta else "unknown"
    model = meta.get("model_provider", "") if meta else ""
    cli_version = meta.get("cli_version", "") if meta else ""
    cwd = meta.get("cwd", "") if meta else ""
    branch = meta.get("git", {}).get("branch", "") if meta else ""
    commit = (meta.get("git", {}).get("commit_hash", "") or "")[:12] if meta else ""
    session_ts = meta.get("timestamp", "") if meta else ""

    sidebar_items: list[str] = []
    message_blocks: list[str] = []
    msg_idx = 0

    inherited_run = 0
    for i, evt in enumerate(events):
        etype = evt["type"]
        ts = format_ts(evt["ts"])
        msg_idx += 1
        anchor = f"msg-{msg_idx}"

        inherited = bool(evt.get("inherited"))
        if inherited and not (i and events[i - 1].get("inherited")):
            inherited_run += 1
            run_length = _inherited_run_length(events, i)
            run_anchor = f"inherited-{inherited_run}"
            sidebar_items.append(
                f'<a class="tree-node tree-role-inherited" data-kind="inherited" href="#{run_anchor}">'
                f'<span class="tree-ts">{ts}</span> '
                f'<span class="tree-content">\u21aa Inherited parent history ({run_length})</span></a>'
            )
            message_blocks.append(
                f'<details class="inherited-history" id="{run_anchor}">'
                f"<summary>Inherited from the parent session \u00b7 {run_length} entries</summary>"
            )

        handler = _EVENT_HANDLERS.get(etype)
        if handler:
            # Inherited entries stay out of the sidebar; one node links the block.
            handler(evt, ts, anchor, [] if inherited else sidebar_items, message_blocks, ctx)

        if inherited and not (i + 1 < len(events) and events[i + 1].get("inherited")):
            message_blocks.append("</details>")

    css = _load_asset("style.css")
    js = _load_asset("viewer.js")

    sidebar_html = "\n".join(sidebar_items)
    messages_html = "\n".join(message_blocks)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return _HTML_TEMPLATE.format(
        title=escape(session_id[:12]),
        css=css,
        js=js,
        sidebar_html=sidebar_html,
        messages_html=messages_html,
        session_id_short=escape(session_id[:12]),
        session_ts_short=escape(format_ts_full(session_ts)),
        session_id=escape(session_id),
        session_ts=escape(format_ts_full(session_ts)),
        model=escape(model),
        cli_version=escape(cli_version),
        cwd=escape(cwd),
        git_info=escape(branch) + ((" @ " + escape(commit)) if commit else ""),
        subagent_info=_subagent_info_html(meta, events),
        generated=generated,
    )


def _subagent_info_html(meta: dict | None, events: list[dict]) -> str:
    """Header rows that identify a subagent thread and its parent."""
    source = meta.get("source") if isinstance(meta, dict) else None
    if not isinstance(source, dict) or "subagent" not in source:
        return ""
    sub = source["subagent"]
    spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
    if isinstance(spawn, dict):
        name = spawn.get("agent_nickname") or "subagent"
        details = [str(spawn.get(k)) for k in ("agent_path", "agent_role") if spawn.get(k)]
        if spawn.get("depth") is not None:
            details.append(f"depth {spawn['depth']}")
        label = name + (f" ({', '.join(details)})" if details else "")
        parent = spawn.get("parent_thread_id") or ""
    else:
        kind = sub.get("other") if isinstance(sub, dict) else sub
        label = str(kind or "subagent")
        parent = ""
    rows = [
        '<div class="info-item"><span class="info-label">Subagent</span>'
        f'<span class="info-value">{escape(label)}</span></div>'
    ]
    if parent:
        rows.append(
            '<div class="info-item"><span class="info-label">Parent Thread</span>'
            f'<span class="info-value">{escape(parent)}</span></div>'
        )
    if any(evt.get("inherited") for evt in events):
        rows.append(
            '<div class="info-item"><span class="info-label">History</span>'
            '<span class="info-value">Forked from the parent; its earlier turns are '
            "collapsed below</span></div>"
        )
    return "\n          " + "\n          ".join(rows)


def _inherited_run_length(events: list[dict], start: int) -> int:
    end = start
    while end < len(events) and events[end].get("inherited"):
        end += 1
    return end - start


# ---------------------------------------------------------------------------
# Per-event-type rendering functions
# ---------------------------------------------------------------------------

_DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=]+$")


def _basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _format_bytes(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    return f"{max(1, round(n / 1000))} KB"


def _image_label(attachment: dict, default: str) -> str:
    if attachment.get("kind") == "local_image" and attachment.get("path"):
        return _basename(attachment["path"])
    return default


def _render_image(
    attachment: dict, ctx: RenderContext, *, embed: bool, default_label: str
) -> str:
    """Render an image attachment as a thumbnail, or as a chip when not embedded."""
    label = _image_label(attachment, default_label)
    url = attachment.get("data_url", "")
    size = attachment.get("bytes")
    if embed and ctx.embed_images and isinstance(url, str) and _DATA_URL_RE.match(url):
        return (
            '<figure class="attachment-image">'
            f'<img src="{url}" alt="{escape(label)}" loading="lazy" decoding="async" '
            "onclick=\"this.classList.toggle('full')\">"
            f"<figcaption>{escape(label)}</figcaption></figure>"
        )
    detail = f", {_format_bytes(size)}" if isinstance(size, int) and size > 0 else ""
    return f'<span class="attachment-chip">[image: {escape(label)}{detail}]</span>'


def _render_attachments(
    attachments: list[dict],
    ctx: RenderContext,
    *,
    embed: bool = True,
    default_label: str = "pasted image",
) -> str:
    parts = []
    for attachment in attachments:
        kind = attachment.get("kind")
        if kind in ("local_image", "image"):
            parts.append(
                _render_image(attachment, ctx, embed=embed, default_label=default_label)
            )
        elif kind == "skill":
            parts.append(f'<span class="attachment-chip">[${escape(attachment.get("name"))}]</span>')
        elif kind == "mention":
            parts.append(f'<span class="attachment-chip">[@{escape(attachment.get("name"))}]</span>')
    if not parts:
        return ""
    return f'<div class="attachments">{"".join(parts)}</div>'


def _render_user_message(evt, ts, anchor, sidebar, messages, ctx):
    attachments = evt.get("attachments") or []
    text = evt["text"]
    text_preview = text[:80].replace("\n", " ")
    if not text_preview and attachments:
        text_preview = "[attachment only]"
    sidebar.append(
        f'<a class="tree-node tree-role-user" data-kind="user" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f464 {escape(text_preview)}</span></a>'
    )
    body = (
        f'<div class="markdown-content">{render_markdown(text)}</div>'
        if text
        else '<div class="attachment-note">(attachment only)</div>'
    )
    messages.append(
        f'<div class="user-message" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f"{body}"
        f"{_render_attachments(attachments, ctx)}"
        f"</div>"
    )


def _render_reasoning(evt, ts, anchor, sidebar, messages, ctx):
    sidebar.append(
        f'<a class="tree-node tree-role-thinking" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f4ad {escape(evt["text"][:60])}</span></a>'
    )
    messages.append(
        f'<div class="thinking-block" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<div class="thinking-text">{escape(evt["text"])}</div>'
        f"</div>"
    )


def _render_agent_commentary(evt, ts, anchor, sidebar, messages, ctx):
    sidebar.append(
        f'<a class="tree-node tree-role-assistant" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f4ac {escape(evt["text"][:60])}</span></a>'
    )
    messages.append(
        f'<div class="commentary-message" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<div class="markdown-content">{render_markdown(evt["text"])}</div>'
        f"</div>"
    )


def _render_assistant_text(evt, ts, anchor, sidebar, messages, ctx):
    if evt.get("phase") == "final_answer":
        _render_task_complete(evt, ts, anchor, sidebar, messages, ctx)
        return
    phase_label = f' ({evt["phase"]})' if evt.get("phase") else ""
    preview = evt["text"][:60].replace("\n", " ")
    sidebar.append(
        f'<a class="tree-node tree-role-assistant" data-kind="assistant" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f916 {escape(preview)}</span></a>'
    )
    messages.append(
        f'<div class="assistant-message" id="{anchor}">'
        f'<div class="message-timestamp">{ts}{escape(phase_label)}</div>'
        f'<div class="assistant-text markdown-content">{render_markdown(evt["text"])}</div>'
        f"</div>"
    )


def _compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _format_param_value(value, key=""):
    if key == "cmd" and isinstance(value, str):
        return f'<span class="tool-command">$ {escape(value)}</span>'
    if isinstance(value, str):
        return f"<pre>{escape(value)}</pre>"
    return f"<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre>"


def _format_tool_args(arguments):
    try:
        args_obj = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return f"<pre>{escape(arguments)}</pre>"

    if not isinstance(args_obj, dict):
        return f"<pre>{escape(json.dumps(args_obj, ensure_ascii=False, indent=2))}</pre>"

    rows = []
    for key, value in args_obj.items():
        rows.append(
            f'<div class="tool-param">'
            f'<div class="tool-param-name">{escape(key)}</div>'
            f'<div class="tool-param-value">{_format_param_value(value, key)}</div>'
            f"</div>"
        )
    return "".join(rows)


_CODE_MODE_CALL_RE = re.compile(r"\btools\.[A-Za-z_][A-Za-z0-9_]*\(")


def _custom_input_preview(name: str, text: str) -> str:
    """Sidebar summary for free-form tool input (code-mode exec, apply_patch)."""
    calls = len(_CODE_MODE_CALL_RE.findall(text))
    if calls > 1:
        return f"{calls} tool calls"
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:120]


def _render_tool_call(evt, ts, anchor, sidebar, messages, ctx):
    name = evt["name"]
    custom = evt.get("input_kind") in ("custom", "web_search")
    if custom:
        args_preview = _custom_input_preview(name, evt["arguments"])
    else:
        try:
            args = json.loads(evt["arguments"])
            args_preview = _compact_json(args)[:120]
        except (json.JSONDecodeError, TypeError):
            args_preview = evt["arguments"][:120]

    sidebar.append(
        f'<a class="tree-node tree-role-tool" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\u26a1 {escape(name)}: {escape(args_preview)}</span></a>'
    )

    # Custom tool input is raw text (JavaScript, patches), not JSON parameters.
    args_html = (
        f"<pre>{escape(evt['arguments'])}</pre>"
        if custom
        else _format_tool_args(evt["arguments"])
    )
    messages.append(
        f'<div class="tool-execution pending" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<div class="tool-header"><span class="tool-name">{escape(name)}</span></div>'
        f'<div class="tool-args">{args_html}</div>'
        f"</div>"
    )


def _render_tool_output(evt, ts, anchor, sidebar, messages, ctx):
    output = evt["output"]
    truncated = len(output) > 2000
    preview = output[:2000]

    attachments = evt.get("attachments") or []
    size_label = f"{len(output)} chars"
    if attachments:
        count = len(attachments)
        images = f"{count} image" + ("s" if count != 1 else "")
        size_label = f"{size_label}, {images}" if output else images
    sidebar.append(
        f'<a class="tree-node tree-role-tool" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f4e4 output ({size_label})</span></a>'
    )

    expandable_class = " expandable" if truncated else ""
    expand_hint = (
        f'\n<span class="expand-hint">[click to expand {len(output)} chars]</span>'
        if truncated
        else ""
    )

    output_images = _render_attachments(
        attachments, ctx, embed=False, default_label="image output"
    )
    messages.append(
        f'<div class="tool-execution success" id="{anchor}">'
        f'<div class="tool-output{expandable_class}" onclick="this.classList.toggle(\'expanded\')">'
        f'<div class="output-preview"><pre>{escape(preview)}{expand_hint}</pre></div>'
        f'<div class="output-full"><pre>{escape(output)}</pre></div>'
        f"</div>{output_images}</div>"
    )


def _render_task_complete(evt, ts, anchor, sidebar, messages, ctx):
    preview = evt["text"][:60].replace("\n", " ")
    sidebar.append(
        f'<a class="tree-node tree-role-assistant" data-kind="final-answer" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\u2705 {escape(preview)}</span></a>'
    )
    messages.append(
        f'<div class="assistant-message final-answer" id="{anchor}">'
        f'<div class="message-timestamp">{ts} \u2014 final answer</div>'
        f'<div class="assistant-text markdown-content">{render_markdown(evt["text"])}</div>'
        f"</div>"
    )


def _render_task_started(evt, ts, anchor, sidebar, messages, ctx):
    sidebar.append(
        f'<a class="tree-node tree-role-system" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\u25b6 Turn started</span></a>'
    )
    messages.append(
        f'<div class="system-event" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<span class="event-label">\u25b6 Turn started</span>'
        f"</div>"
    )


def _render_turn_aborted(evt, ts, anchor, sidebar, messages, ctx):
    reason = escape(evt["reason"])
    sidebar.append(
        f'<a class="tree-node tree-role-error" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\u26d4 Turn aborted: {reason}</span></a>'
    )
    messages.append(
        f'<div class="system-event error-event" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<span class="event-label error-text">\u26d4 Turn aborted: {reason}</span>'
        f"</div>"
    )


def _render_thread_rolled_back(evt, ts, anchor, sidebar, messages, ctx):
    n = evt["num_turns"]
    sidebar.append(
        f'<a class="tree-node tree-role-system" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\u21a9 Rolled back {n} turn(s)</span></a>'
    )
    messages.append(
        f'<div class="system-event" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<span class="event-label">\u21a9 Rolled back {n} turn(s)</span>'
        f"</div>"
    )


def _render_token_count(evt, ts, anchor, sidebar, messages, ctx):
    total = evt["total"]
    if total.get("input_tokens", 0) <= 0:
        return
    tok_str = (
        f"in:{total.get('input_tokens',0):,} "
        f"out:{total.get('output_tokens',0):,} "
        f"reasoning:{total.get('reasoning_output_tokens',0):,}"
    )
    sidebar.append(
        f'<a class="tree-node tree-role-system" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f4ca {tok_str}</span></a>'
    )
    messages.append(
        f'<div class="token-count" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<span class="event-label">\U0001f4ca Tokens \u2014 {tok_str}</span>'
        f"</div>"
    )


_EVENT_HANDLERS = {
    "user_message": _render_user_message,
    "reasoning": _render_reasoning,
    "agent_commentary": _render_agent_commentary,
    "assistant_text": _render_assistant_text,
    "tool_call": _render_tool_call,
    "tool_output": _render_tool_output,
    "task_complete": _render_task_complete,
    "task_started": _render_task_started,
    "turn_aborted": _render_turn_aborted,
    "thread_rolled_back": _render_thread_rolled_back,
    "token_count": _render_token_count,
}


# ---------------------------------------------------------------------------
# HTML shell template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Codex CLI Session \u2014 {title}</title>
  <style>{css}</style>
</head>
<body>
  <button id="hamburger" onclick="document.getElementById('sidebar').classList.toggle('open'); document.getElementById('sidebar-overlay').classList.toggle('open')">\u2630</button>
  <div id="sidebar-overlay" onclick="document.getElementById('sidebar').classList.remove('open'); this.classList.remove('open')"></div>
  <div id="app">
    <aside id="sidebar">
      <div class="sidebar-header">
        <h2>CODEX CLI SESSION</h2>
        <div class="sidebar-meta">{session_id_short} \u00b7 {session_ts_short}</div>
        <input type="text" class="sidebar-search" id="tree-search" placeholder="Filter entries..." oninput="filterTree(this.value)">
        <div class="sidebar-filters">
          <button class="filter-btn active" data-filter="default" onclick="setFilter('default', this)">Default</button>
          <button class="filter-btn" data-filter="no-tools" onclick="setFilter('no-tools', this)">No tools</button>
          <button class="filter-btn" data-filter="user-only" onclick="setFilter('user-only', this)">User</button>
          <button class="filter-btn" data-filter="answers" onclick="setFilter('answers', this)">Answers</button>
          <button class="filter-btn" data-filter="all" onclick="setFilter('all', this)">All</button>
        </div>
      </div>
      <div class="tree-container" id="tree-container">{sidebar_html}</div>
    </aside>
    <main id="content">
      <div class="header">
        <h1><span class="codex-logo">CODEX</span> Session Transcript</h1>
        <div class="header-info">
          <div class="info-item"><span class="info-label">Session ID</span><span class="info-value">{session_id}</span></div>
          <div class="info-item"><span class="info-label">Timestamp</span><span class="info-value">{session_ts}</span></div>
          <div class="info-item"><span class="info-label">Model</span><span class="info-value">{model}</span></div>
          <div class="info-item"><span class="info-label">CLI Version</span><span class="info-value">{cli_version}</span></div>
          <div class="info-item"><span class="info-label">Working Dir</span><span class="info-value">{cwd}</span></div>
          <div class="info-item"><span class="info-label">Git Branch</span><span class="info-value">{git_info}</span></div>{subagent_info}
        </div>
      </div>
      <div id="messages">{messages_html}</div>
      <div class="footer">Codex CLI session transcript \u00b7 Generated {generated}</div>
    </main>
  </div>
  <script>{js}</script>
</body>
</html>"""
