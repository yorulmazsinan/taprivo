"""Rows and the sink protocol the engine writes statistics through.

Kept free of sqlite so the engine never imports the storage layer: the app
passes a concrete sink (``StatsStore``) in, tests pass a recorder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One row of the ``sessions`` table: counters and timestamps only."""

    session_id: str
    started_utc: str
    ended_utc: str | None
    duration_s: int
    mode: str
    taps_total: int
    taps_left: int
    taps_right: int
    squeezes: int
    generated: int
    spent: int
    overflow: int
    max_combo: int


@dataclass(frozen=True, slots=True)
class DailyRow:
    """One row of the ``daily`` table, derived from the sessions of that day."""

    day: str
    sessions: int
    taps_total: int
    generated: int
    spent: int
    active_seconds: int


class StatsSink(Protocol):
    """Where the engine sends session rows. Implementations never raise."""

    def session_started(self, row: SessionRow) -> None: ...

    def session_snapshot(self, row: SessionRow) -> None: ...

    def session_ended(self, row: SessionRow) -> None: ...

    def today(self, day: str) -> DailyRow | None: ...
