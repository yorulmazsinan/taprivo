"""Immutable snapshot of application state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from taprivo.core.events import Finger, Hand

TrackingStatus = Literal["inactive", "simulator", "tracking", "stale", "no_signal"]
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
    camera_fps: float = 0.0
    detection_ratio: float = 0.0
    combo_multiplier: float = 1.0
    taps_per_hand: dict[Hand, int] = field(default_factory=dict)
    taps_per_hand_finger: dict[tuple[Hand, Finger], int] = field(default_factory=dict)
    schema_version: int = field(default=1)
