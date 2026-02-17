"""Timestamp and text formatting utilities."""

from __future__ import annotations

from datetime import datetime


def format_ts(ts_str: str) -> str:
    """Format an ISO timestamp to HH:MM:SS for inline display."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ts_str[:19] if ts_str else ""


def format_ts_full(ts_str: str) -> str:
    """Format an ISO timestamp to a full human-readable UTC string."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return ts_str or ""
