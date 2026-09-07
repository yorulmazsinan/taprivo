"""Immutable snapshot of application state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from taprivo.core.events import Finger

TrackingStatus = Literal["inactive", "simulator", "tracking", "stale"]
McpStatus = Literal["starting", "ready", "error"]
Mode = Literal["simulator", "camera"]


@dataclass(frozen=True, slots=True)
class LastSpend:
    amount: int
    reason: str
    at_utc: datetime


@dataclass(frozen=True, slots=True)
class AppSnapshot:
    session_id: str
    started_at_utc: datetime
    mode: Mode
    available: int
    gross_generated: int
    overflow: int
    spent: int
    max_energy: int
    energy_per_tap: int
    combo: int
    taps_total: int
    taps_per_finger: dict[Finger, int]
    taps_per_minute: int
    spend_count: int
    last_spend: LastSpend | None
    tracking: TrackingStatus
    mcp: McpStatus
    mcp_error: str | None
    last_tool_call_utc: datetime | None
    session_duration_seconds: int
    schema_version: int = field(default=1)
