from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from taprivo.config import Config, RhythmConfig
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger, Hand
from taprivo.core.stats_store import StatsStore
from taprivo.mcp.server import TOOL_NAMES, build_server
from taprivo.mcp.statusline import parse_payload
from taprivo.simulator import Simulator


@pytest.fixture
def engine() -> EnergyEngine:
    """An engine with the rhythm bonus off, so every tap credits exactly 10."""
    return EnergyEngine(Config(rhythm=RhythmConfig(enabled=False)))


class DrummingClock:
    """A clock that walks 100 ms per tap: fast enough to build a combo."""

    def __init__(self) -> None:
        self._now = 0

    def __call__(self) -> int:
        self._now += 100
        return self._now


class SpacedClock:
    """A clock that walks a second per tap, clear of the cap and the combo window."""

    def __init__(self) -> None:
        self._now = 0

    def __call__(self) -> int:
        self._now += 1_000
        return self._now


@pytest.fixture
def simulator(engine: EnergyEngine) -> Simulator:
    sim = Simulator(engine, Config(), now_ms=SpacedClock())
    sim.start()
    return sim


async def call(
    engine: EnergyEngine, name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    server = build_server(engine, Config())
    async with Client(server) as client:
        result = await client.call_tool(name, args or {})
    assert result.is_error is False, result
    assert result.structured_content is not None
    return dict(result.structured_content)


async def test_tools_listed_with_annotations(engine: EnergyEngine) -> None:
    server = build_server(engine, Config())
    async with Client(server) as client:
        listed = await client.list_tools()
    by_name = {tool.name: tool for tool in listed.tools}
    assert tuple(by_name) == TOOL_NAMES
    assert by_name["get_energy"].annotations is not None
    assert by_name["get_energy"].annotations.read_only_hint is True
    spend = by_name["spend_energy"].annotations
    assert spend is not None
    assert spend.read_only_hint is False
    assert spend.idempotent_hint is True
    assert spend.destructive_hint is False


async def test_get_energy_shape(engine: EnergyEngine, simulator: Simulator) -> None:
    simulator.tap(Hand.RIGHT, Finger.INDEX)
    data = await call(engine, "get_energy")
    assert data == {
        "schema_version": 1,
        "session_id": engine.session_id,
        "available": 10,
        "generated": 10,
        "overflow": 0,
        "spent": 0,
        "max_energy": 10000,
        "tracking": "simulator",
    }
    assert engine.snapshot().last_tool_call_utc is not None


async def test_spend_round_trip(engine: EnergyEngine, simulator: Simulator) -> None:
    for _ in range(30):
        simulator.tap(Hand.LEFT, Finger.MIDDLE)
    args = {
        "amount": 250,
        "reason": "Implement validation",
        "request_id": "req-1",
        "session_id": engine.session_id,
    }
    first = await call(engine, "spend_energy", args)
    assert first["success"] is True
    assert first["spent"] == 250 and first["remaining"] == 50
    assert first["transaction_id"]
    again = await call(engine, "spend_energy", args)
    assert again == first


async def test_spend_structured_errors(engine: EnergyEngine, simulator: Simulator) -> None:
    simulator.tap(Hand.RIGHT, Finger.RING)
    base = {"reason": "r", "request_id": "x", "session_id": engine.session_id}
    insufficient = await call(engine, "spend_energy", {**base, "amount": 999})
    assert insufficient == {
        "success": False,
        "session_id": engine.session_id,
        "spent": 0,
        "remaining": 0,
        "transaction_id": None,
        "error": "INSUFFICIENT_ENERGY",
        "available": 10,
    }
    stale = await call(engine, "spend_energy", {**base, "amount": 5, "session_id": "old"})
    assert stale["error"] == "SESSION_CHANGED"
    bad = await call(engine, "spend_energy", {**base, "amount": 0})
    assert bad["error"] == "INVALID_AMOUNT"


async def test_get_stats_and_session(engine: EnergyEngine, simulator: Simulator) -> None:
    simulator.tap(Hand.LEFT, Finger.INDEX)
    simulator.tap(Hand.RIGHT, Finger.INDEX)
    await call(
        engine,
        "spend_energy",
        {"amount": 10, "reason": "tiny fix", "request_id": "s", "session_id": engine.session_id},
    )
    stats = await call(engine, "get_stats", {"scope": "session"})
    assert stats["taps_total"] == 2
    assert stats["taps_per_finger"]["index"] == 2
    assert stats["taps_per_hand"] == {"left": 1, "right": 1}
    assert stats["combo_multiplier"] == 1.0
    assert stats["spend_count"] == 1
    assert stats["last_spend"]["amount"] == 10
    assert stats["combo"] >= 1
    session = await call(engine, "get_session")
    assert session["session_id"] == engine.session_id
    assert session["mode"] == "simulator"
    assert session["tracking"] == "simulator"
    assert session["mcp_uptime_seconds"] >= 0


async def test_get_session_reports_the_agent(engine: EnergyEngine) -> None:
    engine.set_agent_status(
        parse_payload(
            {
                "model": {"display_name": "Opus"},
                "context_window": {"used_percentage": 63.0},
                "rate_limits": {"five_hour": {"used_percentage": 42.0, "resets_at": 1_757_000_000}},
                "version": "2.1.0",
            }
        )
    )
    agent = (await call(engine, "get_session"))["agent"]
    assert agent["model"] == "Opus"
    assert agent["context_used"] == 63.0
    assert agent["five_hour_used"] == 42.0
    assert agent["five_hour_resets_at"] == 1_757_000_000
    assert agent["seven_day_used"] is None
    assert agent["version"] == "2.1.0"
    assert agent["age_s"] >= 0


async def test_get_session_omits_the_agent_until_it_reports(engine: EnergyEngine) -> None:
    assert (await call(engine, "get_session"))["agent"] is None


async def test_get_session_reports_camera_stats(engine: EnergyEngine) -> None:
    engine.set_camera_stats(21.4, 0.9)
    session = await call(engine, "get_session")
    assert session["camera_fps"] == 21.4
    assert session["detection_ratio"] == 0.9


async def test_get_stats_reports_the_combo_multiplier(engine: EnergyEngine) -> None:
    sim = Simulator(engine, Config(), now_ms=DrummingClock())
    sim.start()
    for _ in range(12):
        sim.tap(Hand.LEFT, Finger.INDEX)
    stats = await call(engine, "get_stats", {"scope": "session"})
    assert stats["combo"] == 12
    assert stats["combo_multiplier"] == 1.5
    assert stats["taps_per_hand"] == {"left": 12, "right": 0}


async def test_get_stats_today_is_null_without_a_statistics_store(engine: EnergyEngine) -> None:
    stats = await call(engine, "get_stats", {"scope": "session"})
    assert stats["today"] is None


async def test_get_stats_reports_todays_totals_from_the_store(tmp_path: Path) -> None:
    store = StatsStore(tmp_path / "stats.sqlite")
    engine = EnergyEngine(Config(), sink=store, now_ms=SpacedClock())
    sim = Simulator(engine, Config(), now_ms=SpacedClock())
    sim.start()
    sim.tap(Hand.LEFT, Finger.INDEX)
    sim.tap(Hand.RIGHT, Finger.INDEX)
    engine.heartbeat()

    stats = await call(engine, "get_stats", {"scope": "session"})
    assert stats["today"] == {
        "sessions": 1,
        "taps_total": 2,
        "generated": 20,
        "spent": 0,
        "active_seconds": stats["today"]["active_seconds"],
    }
    store.close()


async def test_get_stats_reports_the_rhythm(engine: EnergyEngine) -> None:
    sim = Simulator(engine, Config(), now_ms=SpacedClock())  # a 60 BPM metronome
    sim.start()
    for _ in range(8):
        sim.tap(Hand.RIGHT, Finger.INDEX)
    stats = await call(engine, "get_stats", {"scope": "session"})
    assert stats["bpm"] == 60.0
    assert stats["rhythm_steady"] is True
    # The fixture engine has the bonus disabled, so the beat is reported but not paid.
    assert stats["rhythm_multiplier"] == 1.0


async def test_get_stats_reports_the_rhythm_multiplier() -> None:
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config(), now_ms=SpacedClock())
    sim.start()
    for _ in range(8):
        sim.tap(Hand.LEFT, Finger.INDEX)
    stats = await call(engine, "get_stats", {"scope": "session"})
    assert stats["bpm"] == 60.0
    assert stats["rhythm_steady"] is True
    assert stats["rhythm_multiplier"] == 1.25


async def test_get_stats_reports_no_rhythm_before_a_beat(engine: EnergyEngine) -> None:
    stats = await call(engine, "get_stats", {"scope": "session"})
    assert stats["bpm"] == 0.0
    assert stats["rhythm_steady"] is False
    assert stats["rhythm_multiplier"] == 1.0
