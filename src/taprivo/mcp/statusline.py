"""Claude Code status line: parse the piped JSON, answer with the energy line.

Claude Code runs a user-configured command on every assistant message and pipes
a JSON object describing the session to its stdin; whatever the command prints
becomes the status line. Taprivo's installed script forwards that JSON here, so
the HUD learns what the agent is doing and the agent's own status line gains an
energy readout. The payload is read, turned into an `AgentStatus` and dropped:
it is never logged and never written to the statistics store.
"""

from __future__ import annotations

import time
from typing import Any

from taprivo.core.state import AgentStatus, AppSnapshot


def _mapping(source: Any, key: str) -> dict[str, Any]:
    value = source.get(key) if isinstance(source, dict) else None
    return value if isinstance(value, dict) else {}


def _number(source: dict[str, Any], key: str) -> float | None:
    value = source.get(key)
    # `bool` is an `int`; a true/false here is a malformed field, not a number.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _text(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _epoch(source: dict[str, Any], key: str) -> int | None:
    value = _number(source, key)
    return int(value) if value is not None else None


def parse_payload(payload: object, *, now_ms: int | None = None) -> AgentStatus:
    """Read the fields Taprivo shows out of a status line payload.

    Every field is optional and anything unexpected is ignored: the payload
    comes from whichever Claude Code version the person happens to run, and a
    missing `rate_limits` (an API-key user, not a subscription) is normal.
    """
    data: dict[str, Any] = payload if isinstance(payload, dict) else {}
    cost = _mapping(data, "cost")
    limits = _mapping(data, "rate_limits")
    five_hour = _mapping(limits, "five_hour")
    seven_day = _mapping(limits, "seven_day")
    duration_ms = _number(cost, "total_duration_ms")
    return AgentStatus(
        model=_text(_mapping(data, "model"), "display_name"),
        context_used=_number(_mapping(data, "context_window"), "used_percentage"),
        cost_usd=_number(cost, "total_cost_usd"),
        duration_s=int(duration_ms // 1000) if duration_ms is not None else None,
        five_hour_used=_number(five_hour, "used_percentage"),
        five_hour_resets_at=_epoch(five_hour, "resets_at"),
        seven_day_used=_number(seven_day, "used_percentage"),
        seven_day_resets_at=_epoch(seven_day, "resets_at"),
        version=_text(data, "version"),
        received_at_ms=now_ms if now_ms is not None else int(time.time() * 1000),
    )


def status_text(snapshot: AppSnapshot, status: AgentStatus) -> str:
    """The segment Claude Code prints in its own status line."""
    segment = f"⚡ {snapshot.available} · x{snapshot.combo}"
    if snapshot.combo_multiplier > 1.0:
        segment += f" {snapshot.combo_multiplier:g}×"
    if status.five_hour_used is not None:
        segment += f" · 5h {round(status.five_hour_used)}%"
    return segment
