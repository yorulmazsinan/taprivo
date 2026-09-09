"""Structured tool outputs exposed over MCP."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EnergyOut(BaseModel):
    schema_version: int = 1
    session_id: str
    available: int = Field(description="Energy that can be spent right now.")
    generated: int = Field(description="Gross energy produced by taps this session.")
    overflow: int = Field(description="Energy lost because the balance was at max_energy.")
    spent: int
    max_energy: int
    tracking: str = Field(description="inactive, simulator, tracking or stale.")


class SpendOut(BaseModel):
    success: bool
    session_id: str
    spent: int = 0
    remaining: int = 0
    transaction_id: str | None = None
    error: str | None = Field(
        default=None,
        description=(
            "INSUFFICIENT_ENERGY, INVALID_AMOUNT, INVALID_REASON, SESSION_CHANGED or "
            "IDEMPOTENCY_CONFLICT when success is false."
        ),
    )
    available: int | None = None


class LastSpendOut(BaseModel):
    amount: int
    reason: str
    at_utc: str


class TodayOut(BaseModel):
    """Totals for the current UTC day, from the local statistics file."""

    sessions: int
    taps_total: int
    generated: int
    spent: int
    active_seconds: int


class StatsOut(BaseModel):
    schema_version: int = 1
    session_id: str
    taps_total: int
    taps_per_finger: dict[str, int]
    taps_per_minute: int
    combo: int
    combo_multiplier: float = Field(
        default=1.0, description="Energy multiplier the current combo has reached."
    )
    taps_per_hand: dict[str, int] = Field(
        default_factory=dict, description="Taps counted per hand, e.g. {'left': 12, 'right': 9}."
    )
    spend_count: int
    last_spend: LastSpendOut | None
    session_duration_seconds: int
    today: TodayOut | None = Field(
        default=None,
        description="Totals for today; null when persistent statistics are off or empty.",
    )


class SessionOut(BaseModel):
    schema_version: int = 1
    session_id: str
    started_at_utc: str
    mode: str
    tracking: str
    mcp_uptime_seconds: int
    last_tool_call_utc: str | None
    camera_fps: float = 0.0
    detection_ratio: float = 0.0
