"""Timestamp and text formatting utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse(ts: Any) -> datetime | None:
    """An ISO timestamp as a datetime, converted to UTC when it has an offset."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt


def format_ts(ts: Any) -> str:
    """Format an ISO timestamp to HH:MM:SS (UTC) for inline display."""
    dt = _parse(ts)
    if dt is not None:
        return dt.strftime("%H:%M:%S")
    return ts[:19] if isinstance(ts, str) else ""


def format_ts_full(ts: Any) -> str:
    """Format an ISO timestamp as a full date and time.

    Timestamps with an offset are shown in UTC; one without an offset is shown
    as written, since its zone is unknown.
    """
    dt = _parse(ts)
    if dt is None:
        return ts if isinstance(ts, str) else ""
    return dt.strftime("%Y-%m-%d %H:%M:%S") + (" UTC" if dt.tzinfo else "")
