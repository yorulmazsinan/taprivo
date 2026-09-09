"""`taprivo stats --today` and `--history` read the file, not the running app."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from taprivo import paths
from taprivo.cli import app
from taprivo.core.stats_sink import SessionRow
from taprivo.core.stats_store import StatsStore

runner = CliRunner()

ROW = SessionRow(
    session_id="s1",
    started_utc="2026-09-01T09:00:00+00:00",
    ended_utc="2026-09-01T09:30:00+00:00",
    duration_s=5_400,
    mode="simulator",
    taps_total=120,
    taps_left=60,
    taps_right=60,
    squeezes=4,
    generated=1_200,
    spent=400,
    overflow=0,
    max_combo=18,
)


def populate(*rows: SessionRow) -> Path:
    path = paths.stats_path()
    store = StatsStore(path)
    for row in rows:
        store.upsert_session(row)
    store.close()
    return path


def today_row(**changes: object) -> SessionRow:
    stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    return replace(ROW, session_id="today", started_utc=stamp, ended_utc=None, **changes)


def test_today_reports_the_current_day(taprivo_home: Path) -> None:
    populate(ROW, today_row(taps_total=40, generated=400, spent=100, duration_s=3_660))
    result = runner.invoke(app, ["stats", "--today"])
    assert result.exit_code == 0, result.output
    assert "Today:     1 sessions, 40 taps, +400 / -100" in result.output
    assert "Active:    01:01" in result.output


def test_today_json(taprivo_home: Path) -> None:
    populate(today_row(taps_total=40, generated=400, spent=100, duration_s=60))
    result = runner.invoke(app, ["stats", "--today", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["today"]["taps_total"] == 40
    assert payload["today"]["day"] == datetime.now(UTC).date().isoformat()


def test_today_without_a_row_for_today(taprivo_home: Path) -> None:
    populate(ROW)
    result = runner.invoke(app, ["stats", "--today"])
    assert result.exit_code == 0, result.output
    assert "No statistics for today yet." in result.output
    payload = json.loads(runner.invoke(app, ["stats", "--today", "--json"]).output)
    assert payload["today"] is None


def test_history_table_is_newest_first(taprivo_home: Path) -> None:
    populate(
        ROW,
        replace(ROW, session_id="s2", started_utc="2026-09-03T09:00:00+00:00", duration_s=60),
    )
    result = runner.invoke(app, ["stats", "--history"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Day         Sessions  Taps  Generated  Spent  Active"
    assert lines[1].startswith("2026-09-03") and lines[1].endswith("00:01")
    assert lines[2].startswith("2026-09-01") and lines[2].endswith("01:30")


def test_history_days_limits_the_rows(taprivo_home: Path) -> None:
    populate(
        ROW,
        replace(ROW, session_id="s2", started_utc="2026-09-03T09:00:00+00:00"),
        replace(ROW, session_id="s3", started_utc="2026-09-05T09:00:00+00:00"),
    )
    payload = json.loads(runner.invoke(app, ["stats", "--history", "--days", "2", "--json"]).output)
    assert [row["day"] for row in payload["history"]] == ["2026-09-05", "2026-09-03"]
    assert payload["days"] == 2


def test_history_json_reports_totals(taprivo_home: Path) -> None:
    populate(ROW)
    payload = json.loads(runner.invoke(app, ["stats", "--history", "--json"]).output)
    assert payload["history"] == [
        {
            "day": "2026-09-01",
            "sessions": 1,
            "taps_total": 120,
            "generated": 1_200,
            "spent": 400,
            "active_seconds": 5_400,
        }
    ]


def test_missing_store_exits_1(taprivo_home: Path) -> None:
    for args in (["stats", "--today"], ["stats", "--history"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1, result.output
        assert "No statistics file yet" in result.output


def test_missing_store_json_exits_1(taprivo_home: Path) -> None:
    result = runner.invoke(app, ["stats", "--today", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_today_and_history_are_mutually_exclusive(taprivo_home: Path) -> None:
    populate(ROW)
    result = runner.invoke(app, ["stats", "--today", "--history"])
    assert result.exit_code == 2
    assert "not both" in result.output


def test_empty_store_reports_no_history(taprivo_home: Path) -> None:
    populate()
    result = runner.invoke(app, ["stats", "--history"])
    assert result.exit_code == 0, result.output
    assert "No statistics recorded yet." in result.output
