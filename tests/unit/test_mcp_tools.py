from __future__ import annotations

from typing import Any

import pytest
from mcp.client import Client

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger
from taprivo.mcp.server import TOOL_NAMES, build_server
from taprivo.simulator import Simulator


@pytest.fixture
def engine() -> EnergyEngine:
    return EnergyEngine(Config())


@pytest.fixture
def simulator(engine: EnergyEngine) -> Simulator:
    sim = Simulator(engine, Config())
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
    simulator.tap(Finger.INDEX)
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
        simulator.tap(Finger.MIDDLE)
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
    simulator.tap(Finger.RING)
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
    simulator.tap(Finger.THUMB)
    simulator.tap(Finger.THUMB)
    await call(
        engine,
        "spend_energy",
        {"amount": 10, "reason": "tiny fix", "request_id": "s", "session_id": engine.session_id},
    )
    stats = await call(engine, "get_stats", {"scope": "session"})
    assert stats["taps_total"] == 2
    assert stats["taps_per_finger"]["thumb"] == 2
    assert stats["spend_count"] == 1
    assert stats["last_spend"]["amount"] == 10
    assert stats["combo"] >= 1
    session = await call(engine, "get_session")
    assert session["session_id"] == engine.session_id
    assert session["mode"] == "simulator"
    assert session["tracking"] == "simulator"
    assert session["mcp_uptime_seconds"] >= 0


async def test_get_session_reports_camera_stats(engine: EnergyEngine) -> None:
    engine.set_camera_stats(21.4, 0.9)
    session = await call(engine, "get_session")
    assert session["camera_fps"] == 21.4
    assert session["detection_ratio"] == 0.9
