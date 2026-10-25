"""Audit how the viewer parses real Codex sessions.

Usage:
    python scripts/audit_sessions.py [PATH ...]        files or directories of rollout-*.jsonl
                                                       (default: ~/.codex/sessions and
                                                       ~/.codex/archived_sessions)
    python scripts/audit_sessions.py --tar FILE|-      sessions read from a tar stream

Options: --jobs N, --no-html, --examples N, --json FILE
Set AUDIT_SRC to another checkout's src directory to audit that version.

Each session gets these checks. None of them compares against an earlier run
of the viewer, so a bug present from the start still shows up.

duplicates  The same text is visible twice in one turn, more often than any
            single raw stream repeats it. A task_complete or agent_message
            that is a prefix of a visible response message also counts.
truth       Visible prompts, assistant messages and reasoning per turn, compared
            with what the raw records say should be visible. Reports both extra
            and missing entries.
overlaps    Two raw record kinds share text in a turn, and no dedup rule in
            OVERLAP_RULES says how the parser handles that pair. This is how an
            assumption like "agent_message never repeats a final answer" gets
            tested against real data.
finals      A turn whose records name its answer (a final_answer message or a
            task_complete) shows no entry marked as the final answer, so the
            Answers filter would miss it.
unrecognized  Record kinds the parser neither reads nor deliberately skips,
            which usually means Codex changed its format.
errors      The parser, the HTML builder or the audit itself raised.

The script prints session text snippets to the terminal. Never commit its output.
Exit status is 1 when any session has a finding or no session was audited, and
2 when a given path does not exist.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import tarfile
import traceback
from collections import Counter, defaultdict
from pathlib import Path


def _viewer():
    """Import the viewer lazily, so the check functions work on any version's events.

    AUDIT_SRC points the audit at another checkout's src directory.
    """
    src = os.environ.get("AUDIT_SRC") or str(Path(__file__).resolve().parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from codex_transcript_viewer import html_builder, parser

    return parser, html_builder


# Raw record kinds whose text the viewer can show, and the role they show under.
# Assistant responses are "assistant.response.<phase>"; a phase Codex adds later
# still counts as a response.
RESPONSE_PREFIX = "assistant.response."
ASSISTANT_RESPONSE_KINDS = (
    "assistant.response.commentary",
    "assistant.response.final_answer",
    "assistant.response.no_phase",
)
KIND_GROUP = {
    "prompt.legacy": "prompt",
    "prompt.item": "prompt",
    "assistant.event": "assistant",
    "task_complete": "assistant",
    "reasoning.event": "reasoning",
    "reasoning.response": "reasoning",
}


def kind_group(kind: str) -> str:
    return "assistant" if kind.startswith(RESPONSE_PREFIX) else KIND_GROUP[kind]


def response_kinds(kinds) -> list[str]:
    return sorted(k for k in kinds if k.startswith(RESPONSE_PREFIX))

# Pairs of raw kinds known to share text within one role, and the rule that
# keeps the pair from showing twice. Relations: "equal" is identical normalized
# text; "copy" is an event copy of a response that leaves out its proposed-plan
# or memory-citation blocks; "prefix" is a strict prefix of at least
# MIN_PREFIX_CHARS. Any same-role pair seen in real sessions but missing here is
# reported as an unhandled overlap. Kinds in different roles (a prompt of "yo"
# answered with "yo") are shown once in each role, which is correct.
OVERLAP_RULES: dict[tuple[str, str, str], str] = {
    **{
        (relation, event, kind): f"{name} matches any assistant message"
        for relation in ("equal", "copy")
        for event, name in (("assistant.event", "agent_message"), ("task_complete", "task_complete"))
        for kind in ASSISTANT_RESPONSE_KINDS
    },
    ("prefix", "task_complete", "assistant.response.final_answer"):
        "task_complete may cut a final answer short",
    ("equal", "assistant.event", "task_complete"): "both match the same response message",
    ("equal", "reasoning.event", "reasoning.response"): "agent_reasoning matches reasoning",
    ("equal", "prompt.item", "prompt.legacy"): "item prompts replace legacy prompts",
    **{
        ("equal", a, b): "same stream: the model wrote the text twice"
        for a in ASSISTANT_RESPONSE_KINDS
        for b in ASSISTANT_RESPONSE_KINDS
        if a < b
    },
}

# "equal" is symmetric; store it with the kinds in sorted order, as overlaps() reports it.
OVERLAP_RULES = {
    (rel, *sorted((a, b))) if rel == "equal" else (rel, a, b): why
    for (rel, a, b), why in OVERLAP_RULES.items()
}

# Prefix overlaps shorter than this are ignored ("Done." vs "Done. Tests pass.").
MIN_PREFIX_CHARS = 40

# Blocks that agent_message and task_complete copies of a response leave out.
_COPY_OMITS_RE = re.compile(r"<(oai-mem-citation|proposed_plan)>(?:.*?</\1>|.*\Z)", re.S)


def normalize(value) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def event_copy(text: str) -> str:
    return normalize(_COPY_OMITS_RE.sub("", text))


def _payload(entry) -> dict:
    payload = entry.get("payload") if isinstance(entry, dict) else None
    return payload if isinstance(payload, dict) else {}


def raw_texts_by_turn(entries: list) -> dict[int, dict[str, list[str]]]:
    """Normalized text of every visible-source raw record, per turn and kind.

    Turns are counted from task_started records, the same boundary the viewer
    marks with "Turn started".
    """
    turns: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    reasoning_seen: dict[int, set[str]] = defaultdict(set)
    turn = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        payload = _payload(entry)
        kind = payload.get("type")
        texts = turns[turn]
        if entry.get("type") == "event_msg":
            if kind == "task_started":
                turn += 1
            elif kind == "user_message":
                texts["prompt.legacy"].append(normalize(payload.get("message")))
            elif kind == "item_completed":
                item = payload.get("item")
                if isinstance(item, dict) and item.get("type") == "UserMessage":
                    blocks = item.get("content") if isinstance(item.get("content"), list) else []
                    words = [b.get("text") for b in blocks
                             if isinstance(b, dict) and b.get("type") == "text"]
                    texts["prompt.item"].append(
                        normalize(" ".join(w for w in words if isinstance(w, str))))
            elif kind == "agent_message":
                texts["assistant.event"].append(normalize(payload.get("message")))
            elif kind == "task_complete":
                texts["task_complete"].append(normalize(payload.get("last_agent_message")))
            elif kind == "agent_reasoning":
                texts["reasoning.event"].append(normalize(payload.get("text")))
        elif entry.get("type") == "response_item":
            if kind == "message" and payload.get("role") == "assistant":
                phase = payload.get("phase") or "no_phase"
                content = payload.get("content") if isinstance(payload.get("content"), list) else []
                # A message's text blocks are one entry, the way its event copies carry them.
                blocks = [block.get("text") for block in content
                          if isinstance(block, dict) and block.get("type") == "output_text"
                          and isinstance(block.get("text"), str)]
                if blocks:
                    texts[f"assistant.response.{phase}"].append(normalize("\n\n".join(blocks)))
            elif kind == "reasoning":
                summary = payload.get("summary") if isinstance(payload.get("summary"), list) else []
                parts = [normalize(block.get("text")) for block in summary
                         if isinstance(block, dict) and block.get("type") == "summary_text"]
                # Each heading shows once per turn, where it first appeared:
                # newer models restate the turn's summary so far in every item.
                texts["reasoning.response"].extend(p for p in parts if p not in reasoning_seen[turn])
                reasoning_seen[turn].update(parts)
    return {
        turn: {kind: [t for t in values if t] for kind, values in kinds.items()}
        for turn, kinds in turns.items()
    }


VISIBLE_GROUP = {
    "user_message": "prompt",
    "agent_commentary": "assistant",
    "assistant_text": "assistant",
    "task_complete": "assistant",
    "reasoning": "reasoning",
}


def visible_by_turn(events: list[dict]) -> dict[int, list[tuple[str, str]]]:
    """(event type, normalized text) of every visible text entry, per turn."""
    turns: dict[int, list[tuple[str, str]]] = defaultdict(list)
    turn = 0
    for event in events:
        if event.get("type") == "task_started":
            turn += 1
        elif event.get("type") in VISIBLE_GROUP:
            text = normalize(event.get("text"))
            if text:
                label = event["type"]
                if label == "assistant_text":
                    label = f"assistant_text.{event.get('phase') or 'no_phase'}"
                turns[turn].append((label, text))
    return turns


def _group(label: str) -> str:
    return VISIBLE_GROUP[label.split(".")[0]]


def final_turns(events: list[dict]) -> set[int]:
    """Turns with an entry the viewer marks as the final answer."""
    turns, turn = set(), 0
    for event in events:
        kind = event.get("type")
        if kind == "task_started":
            turn += 1
        elif kind == "task_complete" or event.get("final") or (
            kind == "assistant_text" and event.get("phase") == "final_answer"
        ):
            turns.add(turn)
    return turns


def check_finals(raw, events) -> list[str]:
    marked = final_turns(events)
    findings = []
    for turn, kinds in sorted(raw.items()):
        named = kinds.get("task_complete") or kinds.get(RESPONSE_PREFIX + "final_answer")
        if named and turn not in marked:
            findings.append(f"turn {turn}: answer not marked final {_short(named[-1])!r}")
    return findings


def _max_counts(*streams: list[str]) -> Counter:
    result = Counter()
    for stream in streams:
        for text, count in Counter(stream).items():
            result[text] = max(result[text], count)
    return result


def expected_by_turn(raw: dict[int, dict[str, list[str]]]) -> dict[int, dict[str, Counter]]:
    """What each turn should show, from raw records alone.

    A text is expected as many times as the most repetitive raw stream carries
    it. An agent_message or task_complete that copies a response message (see
    event_copy) is that message, not a new one. task_complete may also be the
    start of a final answer.
    """
    expected: dict[int, dict[str, Counter]] = {}
    for turn, kinds in raw.items():
        responses = [t for k in response_kinds(kinds) for t in kinds[k]]
        copies = {event_copy(t) for t in responses} | set(responses)
        finals = kinds.get("assistant.response.final_answer", [])
        events = [t for t in kinds.get("assistant.event", []) if t not in copies]
        assistant = _max_counts(responses, events)
        completes = [
            t for t in kinds.get("task_complete", [])
            if t not in copies and t not in events and not any(f.startswith(t) for f in finals)
        ]
        assistant |= _max_counts(completes)
        # Every distinct typed prompt shows; a legacy copy of an item prompt is the same prompt.
        prompts = Counter(kinds.get("prompt.item", []))
        prompts += Counter(kinds.get("prompt.legacy", [])) - prompts
        expected[turn] = {
            "prompt": prompts,
            "assistant": assistant,
            "reasoning": _max_counts(kinds.get("reasoning.event", []),
                                     kinds.get("reasoning.response", [])),
        }
    return expected


def _short(text: str, limit: int = 70) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def check_duplicates(raw, visible) -> list[str]:
    """Texts shown more often within one role than any single raw stream has them."""
    findings = []
    for turn, items in visible.items():
        streams = raw.get(turn, {})
        allowed: dict[str, Counter] = defaultdict(Counter)
        # All assistant response phases are one stream: the model's output.
        allowed["assistant"] = _max_counts(
            [t for k in response_kinds(streams) for t in streams[k]])
        for kind, texts in streams.items():
            if not kind.startswith(RESPONSE_PREFIX):
                allowed[kind_group(kind)] |= _max_counts(texts)

        by_text: dict[tuple[str, str], list[str]] = defaultdict(list)
        for label, text in items:
            by_text[(_group(label), text)].append(label)
        for (group, text), labels in by_text.items():
            if len(labels) > allowed[group][text]:
                findings.append(
                    f"turn {turn}: shown {len(labels)}x, raw allows {allowed[group][text]}x "
                    f"[{' + '.join(sorted(labels))}] {_short(text)!r}"
                )
        responses = [(label, text) for label, text in items if label.startswith("assistant_text")]
        for label, text in items:
            if label not in ("task_complete", "agent_commentary"):
                continue
            for other_label, other in responses:
                if other != text and (other.startswith(text) or event_copy(other) == text):
                    findings.append(
                        f"turn {turn}: {label} repeats part of {other_label} {_short(text)!r}"
                    )
                    break
    return findings


def check_truth(raw, visible) -> tuple[list[str], Counter]:
    findings = []
    totals = Counter()
    expected = expected_by_turn(raw)
    for turn in sorted(expected.keys() | visible.keys()):
        shown: dict[str, Counter] = defaultdict(Counter)
        for label, text in visible.get(turn, []):
            shown[_group(label)][text] += 1
        want = expected.get(turn, {})
        for group in ("prompt", "assistant", "reasoning"):
            got, exp = shown[group], want.get(group, Counter())
            totals[f"{group}.expected"] += sum(exp.values())
            totals[f"{group}.shown"] += sum(got.values())
            for text, count in (got - exp).items():
                findings.append(f"turn {turn}: extra {group} x{count} {_short(text)!r}")
            for text, count in (exp - got).items():
                findings.append(f"turn {turn}: missing {group} x{count} {_short(text)!r}")
    return findings, totals


def overlaps(raw) -> dict[tuple[str, str, str], list]:
    """Same-role raw kind pairs sharing text in a turn: {(relation, a, b): [turn, text]}."""
    found: dict[tuple[str, str, str], list] = {}
    for turn, kinds in raw.items():
        sets = {kind: set(texts) for kind, texts in kinds.items() if texts}
        for a, a_texts in sets.items():
            for b, b_texts in sets.items():
                if a == b:
                    continue
                if a < b:
                    shared = a_texts & b_texts
                    if shared:
                        found.setdefault(("equal", a, b), [turn, min(shared)])
                copies = {event_copy(t) for t in b_texts} - b_texts
                for text in sorted(a_texts - b_texts):
                    if text in copies:
                        found.setdefault(("copy", a, b), [turn, text])
                    elif len(text) >= MIN_PREFIX_CHARS and any(o.startswith(text) for o in b_texts):
                        found.setdefault(("prefix", a, b), [turn, text])
    return found


def rule_for(key: tuple[str, str, str]) -> str | None:
    relation, a, b = key
    if kind_group(a) != kind_group(b):
        return "different roles: each shows once in its own role"
    return OVERLAP_RULES.get(key)


def session_format(entries: list) -> str:
    prompt = "no prompts"
    phases = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        payload = _payload(entry)
        kind = payload.get("type")
        if entry.get("type") == "event_msg" and kind == "item_completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "UserMessage":
                prompt = "item prompts"
        elif entry.get("type") == "event_msg" and kind == "user_message" and prompt == "no prompts":
            prompt = "legacy prompts"
        elif kind == "message" and payload.get("role") == "assistant" and payload.get("phase"):
            phases = True
    return f"{prompt}, {'phased' if phases else 'unphased'} assistant messages"


def cli_version(entries: list) -> str:
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") == "session_meta":
            return str(_payload(entry).get("cli_version") or "?")
    return "?"


def entries_from_lines(lines) -> list:
    """Records as the CLI reads them, so the audit sees what the viewer sees."""
    parser, _ = _viewer()
    return list(parser.parse_lines(lines))


CHECKS = ("errors", "duplicates", "truth", "finals", "unhandled_overlaps", "unrecognized")


def empty_report(name: str) -> dict:
    return {
        "session": name,
        "format": "?",
        "cli_version": "?",
        "duplicates": [],
        "truth": [],
        "finals": [],
        "totals": {},
        "overlaps": {},
        "unhandled_overlaps": [],
        "unrecognized": [],
        "unrecognized_counts": {},
        "errors": [],
    }


def audit_entries(name: str, entries: list, render_html: bool = True) -> dict:
    """Audit one session; any exception becomes an error finding for it."""
    report = empty_report(name)
    try:
        _audit_entries(report, entries, render_html)
    except Exception:
        report["errors"].append("audit: " + traceback.format_exc(limit=3).strip())
    return report


def _audit_entries(report: dict, entries: list, render_html: bool) -> None:
    report["format"] = session_format(entries)
    report["cli_version"] = cli_version(entries)
    parser, html_builder = _viewer()
    counts = parser.unrecognized_record_kinds(entries)
    report["unrecognized_counts"] = dict(counts)
    report["unrecognized"] = [f"{kind} x{count}" for kind, count in counts.most_common()]
    try:
        meta, events = parser.extract_conversation(entries)
    except Exception:
        report["errors"].append("parser: " + traceback.format_exc(limit=3).strip())
        return
    if render_html:
        try:
            html_builder.build_html(meta, events)
        except Exception:
            report["errors"].append("html: " + traceback.format_exc(limit=3).strip())

    raw = raw_texts_by_turn(entries)
    visible = visible_by_turn(events)
    report["duplicates"] = check_duplicates(raw, visible)
    report["truth"], totals = check_truth(raw, visible)
    report["finals"] = check_finals(raw, events)
    report["totals"] = dict(totals)
    for key, (turn, text) in overlaps(raw).items():
        label = " ".join(key)
        report["overlaps"][label] = f"turn {turn}: {_short(text)!r}"
        if rule_for(key) is None:
            report["unhandled_overlaps"].append(f"{label} (turn {turn}: {_short(text)!r})")


def has_findings(report: dict) -> bool:
    return any(report.get(k) for k in CHECKS)


def _audit_path(path: str, render_html: bool) -> dict:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            entries = entries_from_lines(f)
    except OSError as exc:
        report = empty_report(path)
        report["errors"].append(f"read: {exc}")
        return report
    return audit_entries(path, entries, render_html)


def _audit_bytes(name: str, data: bytes, render_html: bool) -> dict:
    lines = data.decode("utf-8", errors="replace").splitlines()
    return audit_entries(name, entries_from_lines(lines), render_html)


def _session_paths(paths: list[str], required: bool) -> tuple[list[str], list[str]]:
    """Session files under paths, and the paths that don't exist.

    A missing default location is fine; a path someone typed is not.
    """
    found, missing = [], []
    for path in paths:
        p = Path(path).expanduser()
        if p.is_dir():
            found.extend(str(f) for f in sorted(p.rglob("*.jsonl")) if not f.name.startswith("._"))
        elif p.is_file():
            found.append(str(p))
        elif required:
            missing.append(path)
    return found, missing


def _tar_jobs(source: str):
    stream = sys.stdin.buffer if source == "-" else open(source, "rb")
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for member in tar:
            name = Path(member.name).name
            if member.isfile() and name.endswith(".jsonl") and not name.startswith("._"):
                yield member.name, tar.extractfile(member).read()


def _result(future, name: str) -> dict:
    """A worker's report, or an error report if the worker itself failed."""
    try:
        return future.result()
    except Exception as exc:
        report = empty_report(name)
        report["errors"].append(f"worker: {type(exc).__name__}: {exc}")
        return report


def run(jobs_iter, submit, jobs: int, name=str):
    """Run submit(job) for each job with at most 2*jobs in flight."""
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
        pending = {}
        for job in jobs_iter:
            pending[submit(pool, job)] = name(job)
            if len(pending) >= jobs * 2:
                done, _ = concurrent.futures.wait(
                    pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    yield _result(future, pending.pop(future))
        for future in concurrent.futures.as_completed(list(pending)):
            yield _result(future, pending.pop(future))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit the viewer against real Codex sessions.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--tar", help="read sessions from a tar file, or - for stdin")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--no-html", action="store_true", help="skip building HTML")
    ap.add_argument("--examples", type=int, default=3, help="findings shown per check")
    ap.add_argument("--json", type=Path, help="write every session report to this file")
    args = ap.parse_args(argv)
    render_html = not args.no_html

    if args.tar:
        results = run(_tar_jobs(args.tar),
                      lambda pool, job: pool.submit(_audit_bytes, *job, render_html),
                      args.jobs, name=lambda job: job[0])
    else:
        paths, missing = _session_paths(
            args.paths or ["~/.codex/sessions", "~/.codex/archived_sessions"],
            required=bool(args.paths))
        if missing:
            print("error: no such file or directory: " + ", ".join(missing), file=sys.stderr)
            return 2
        results = run(iter(paths),
                      lambda pool, path: pool.submit(_audit_path, path, render_html),
                      args.jobs)

    reports = []
    formats: dict[str, list[str]] = defaultdict(list)
    overlap_seen: Counter = Counter()
    unrecognized: Counter = Counter()
    for report in results:
        reports.append(report)
        formats[report["format"]].append(report["session"])
        overlap_seen.update(report["overlaps"].keys())
        unrecognized.update(report.get("unrecognized_counts", {}))
        if not has_findings(report):
            continue
        print(f"== {report['session']}  ({report['format']}, cli {report['cli_version']})")
        for check in CHECKS:
            items = report[check]
            if not items:
                continue
            print(f"   {check}: {len(items)}")
            for item in items[: args.examples]:
                print(f"     - {item}")

    flagged = [r for r in reports if has_findings(r)]
    print(f"\n{len(reports)} sessions audited, {len(flagged)} with findings")
    for check in CHECKS:
        sessions = sum(1 for r in reports if r[check])
        items = sum(len(r[check]) for r in reports)
        print(f"  {check:20} {items:6} findings in {sessions} sessions")
    print("\nformats (one example each):")
    for fmt, sessions in sorted(formats.items()):
        print(f"  {len(sessions):5}  {fmt}: {sessions[0]}")
    print("\nraw text overlaps (sessions):")
    for label, count in overlap_seen.most_common():
        key = tuple(label.split(" "))
        rule = rule_for(key) or "UNHANDLED"
        print(f"  {count:5}  {label}: {rule}")
    unused = [" ".join(k) for k in OVERLAP_RULES if " ".join(k) not in overlap_seen]
    if unused:
        print("  rules never exercised: " + "; ".join(unused))
    if unrecognized:
        print("\nunrecognized record kinds: " + ", ".join(
            f"{k} x{v}" for k, v in unrecognized.most_common()))
    if args.json:
        args.json.write_text(json.dumps(reports, indent=1))
    if not reports:
        print("error: no sessions were audited", file=sys.stderr)
        return 1
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
