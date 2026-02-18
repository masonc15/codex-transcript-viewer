"""Build a self-contained HTML viewer from parsed Codex session events."""

from __future__ import annotations

import json
from datetime import datetime
from importlib import resources

from .formatting import format_ts, format_ts_full
from .markdown import escape, render_markdown


def _load_asset(name: str) -> str:
    """Load a bundled CSS or JS asset from the package."""
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


def build_html(meta: dict | None, events: list[dict]) -> str:
    """Build a self-contained HTML string from session metadata and events."""
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

    for evt in events:
        etype = evt["type"]
        ts = format_ts(evt["ts"])
        msg_idx += 1
        anchor = f"msg-{msg_idx}"

        handler = _EVENT_HANDLERS.get(etype)
        if handler:
            handler(evt, ts, anchor, sidebar_items, message_blocks)

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
        generated=generated,
    )


# ---------------------------------------------------------------------------
# Per-event-type rendering functions
# ---------------------------------------------------------------------------

def _render_user_message(evt, ts, anchor, sidebar, messages):
    text_preview = evt["text"][:80].replace("\n", " ")
    sidebar.append(
        f'<a class="tree-node tree-role-user" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f464 {escape(text_preview)}</span></a>'
    )
    messages.append(
        f'<div class="user-message" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<div class="markdown-content">{render_markdown(evt["text"])}</div>'
        f"</div>"
    )


def _render_reasoning(evt, ts, anchor, sidebar, messages):
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


def _render_agent_commentary(evt, ts, anchor, sidebar, messages):
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


def _render_assistant_text(evt, ts, anchor, sidebar, messages):
    phase_label = f' ({evt["phase"]})' if evt.get("phase") else ""
    preview = evt["text"][:60].replace("\n", " ")
    sidebar.append(
        f'<a class="tree-node tree-role-assistant" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f916 {escape(preview)}</span></a>'
    )
    messages.append(
        f'<div class="assistant-message" id="{anchor}">'
        f'<div class="message-timestamp">{ts}{escape(phase_label)}</div>'
        f'<div class="assistant-text markdown-content">{render_markdown(evt["text"])}</div>'
        f"</div>"
    )


def _render_tool_call(evt, ts, anchor, sidebar, messages):
    name = evt["name"]
    try:
        args = json.loads(evt["arguments"])
        args_preview = args.get("cmd", "")[:80] if name == "exec_command" else json.dumps(args, indent=None)[:80]
    except (json.JSONDecodeError, TypeError):
        args_preview = evt["arguments"][:80]

    sidebar.append(
        f'<a class="tree-node tree-role-tool" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\u26a1 {escape(name)}: {escape(args_preview)}</span></a>'
    )

    try:
        args_obj = json.loads(evt["arguments"])
        if name == "exec_command":
            args_display = f'<span class="tool-command">$ {escape(args_obj.get("cmd", ""))}</span>'
        else:
            args_display = f"<pre>{escape(json.dumps(args_obj, indent=2))}</pre>"
    except (json.JSONDecodeError, TypeError):
        args_display = f"<pre>{escape(evt['arguments'])}</pre>"

    messages.append(
        f'<div class="tool-execution pending" id="{anchor}">'
        f'<div class="message-timestamp">{ts}</div>'
        f'<div class="tool-header"><span class="tool-name">{escape(name)}</span></div>'
        f'<div class="tool-args">{args_display}</div>'
        f"</div>"
    )


def _render_tool_output(evt, ts, anchor, sidebar, messages):
    output = evt["output"]
    truncated = len(output) > 2000
    preview = output[:2000]

    sidebar.append(
        f'<a class="tree-node tree-role-tool" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\U0001f4e4 output ({len(output)} chars)</span></a>'
    )

    expandable_class = " expandable" if truncated else ""
    expand_hint = (
        f'\n<span class="expand-hint">[click to expand {len(output)} chars]</span>'
        if truncated
        else ""
    )

    messages.append(
        f'<div class="tool-execution success" id="{anchor}">'
        f'<div class="tool-output{expandable_class}" onclick="this.classList.toggle(\'expanded\')">'
        f'<div class="output-preview"><pre>{escape(preview)}{expand_hint}</pre></div>'
        f'<div class="output-full"><pre>{escape(output)}</pre></div>'
        f"</div></div>"
    )


def _render_task_complete(evt, ts, anchor, sidebar, messages):
    preview = evt["text"][:60].replace("\n", " ")
    sidebar.append(
        f'<a class="tree-node tree-role-assistant" href="#{anchor}">'
        f'<span class="tree-ts">{ts}</span> '
        f'<span class="tree-content">\u2705 {escape(preview)}</span></a>'
    )
    messages.append(
        f'<div class="assistant-message final-answer" id="{anchor}">'
        f'<div class="message-timestamp">{ts} \u2014 final answer</div>'
        f'<div class="assistant-text markdown-content">{render_markdown(evt["text"])}</div>'
        f"</div>"
    )


def _render_task_started(evt, ts, anchor, sidebar, messages):
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


def _render_turn_aborted(evt, ts, anchor, sidebar, messages):
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


def _render_thread_rolled_back(evt, ts, anchor, sidebar, messages):
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


def _render_token_count(evt, ts, anchor, sidebar, messages):
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
          <div class="info-item"><span class="info-label">Git Branch</span><span class="info-value">{git_info}</span></div>
        </div>
      </div>
      <div id="messages">{messages_html}</div>
      <div class="footer">Codex CLI session transcript \u00b7 Generated {generated}</div>
    </main>
  </div>
  <script>{js}</script>
</body>
</html>"""
