from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from taprivo.core.stats_sink import SessionRow
from taprivo.core.stats_store import SCHEMA_VERSION, StatsStore

ROW = SessionRow(
    session_id="s1",
    started_utc="2026-09-09T10:00:00+00:00",
    ended_utc=None,
    duration_s=120,
    mode="simulator",
    taps_total=40,
    taps_left=25,
    taps_right=15,
    squeezes=3,
    generated=400,
    spent=100,
    overflow=0,
    max_combo=12,
)


@pytest.fixture
def store(tmp_path: Path) -> StatsStore:
    return StatsStore(tmp_path / "stats.sqlite")


def test_round_trip_session_and_daily(store: StatsStore) -> None:
    store.upsert_session(ROW)
    assert store.session("s1") == ROW
    day = store.today("2026-09-09")
    assert day is not None
    assert (day.sessions, day.taps_total, day.generated, day.spent) == (1, 40, 400, 100)
    assert day.active_seconds == 120


def test_upsert_replaces_instead_of_duplicating(store: StatsStore) -> None:
    store.upsert_session(ROW)
    store.upsert_session(replace(ROW, taps_total=90, duration_s=300))
    day = store.today("2026-09-09")
    assert day is not None
    assert (day.sessions, day.taps_total, day.active_seconds) == (1, 90, 300)


def test_daily_sums_two_sessions_on_one_day(store: StatsStore) -> None:
    store.upsert_session(ROW)
    store.upsert_session(
        replace(
            ROW,
            session_id="s2",
            started_utc="2026-09-09T14:00:00+00:00",
            duration_s=60,
            taps_total=10,
            generated=100,
            spent=20,
        )
    )
    day = store.today("2026-09-09")
    assert day is not None
    assert (day.sessions, day.taps_total, day.generated, day.spent, day.active_seconds) == (
        2,
        50,
        500,
        120,
        180,
    )


def test_end_session_sets_end_and_recomputes_active_seconds(store: StatsStore) -> None:
    store.upsert_session(ROW)
    store.end_session("s1", "2026-09-09T10:30:00+00:00", 1800)
    row = store.session("s1")
    assert row is not None and row.ended_utc == "2026-09-09T10:30:00+00:00"
    day = store.today("2026-09-09")
    assert day is not None and day.active_seconds == 1800


def test_end_session_ignores_an_unknown_id(store: StatsStore) -> None:
    store.end_session("nope", "2026-09-09T10:30:00+00:00", 10)
    assert store.today("2026-09-09") is None


def test_history_is_newest_first_and_omits_missing_days(store: StatsStore) -> None:
    for index, day in enumerate(("2026-09-01", "2026-09-05", "2026-09-09")):
        store.upsert_session(
            replace(ROW, session_id=f"s{index}", started_utc=f"{day}T08:00:00+00:00")
        )
    assert [row.day for row in store.history(30)] == ["2026-09-09", "2026-09-05", "2026-09-01"]
    assert [row.day for row in store.history(2)] == ["2026-09-09", "2026-09-05"]
    assert store.history(0) == []


def test_today_is_none_for_a_day_without_sessions(store: StatsStore) -> None:
    store.upsert_session(ROW)
    assert store.today("2026-09-08") is None


def test_wal_and_user_version_are_set(tmp_path: Path) -> None:
    path = tmp_path / "stats.sqlite"
    store = StatsStore(path)
    store.upsert_session(ROW)
    store.close()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_a_foreign_schema_version_is_moved_aside(tmp_path: Path) -> None:
    path = tmp_path / "stats.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 7")
    conn.execute("CREATE TABLE leftovers (x INTEGER)")
    conn.commit()
    conn.close()

    store = StatsStore(path)
    store.upsert_session(ROW)
    assert store.today("2026-09-09") is not None
    store.close()

    backup = tmp_path / "stats.sqlite.v7.bak"
    assert backup.exists()
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 7
    old.close()


def test_a_matching_version_keeps_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "stats.sqlite"
    first = StatsStore(path)
    first.upsert_session(ROW)
    first.close()
    second = StatsStore(path)
    assert second.session("s1") == ROW
    assert not list(tmp_path.glob("*.bak"))
    second.close()


def test_an_unreadable_file_disables_the_store_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "stats.sqlite"
    path.write_text("not a database at all")
    store = StatsStore(path)
    assert store.enabled is False
    store.upsert_session(ROW)
    store.end_session("s1", "2026-09-09T10:30:00+00:00", 10)
    assert store.today("2026-09-09") is None
    assert store.history(30) == []
    assert store.session("s1") is None
    store.close()


def test_a_write_error_disables_further_writes_without_raising(
    store: StatsStore, caplog: pytest.LogCaptureFixture
) -> None:
    store.upsert_session(ROW)
    conn = store._conn
    assert conn is not None
    conn.execute("DROP TABLE sessions")
    conn.commit()

    with caplog.at_level("WARNING", logger="taprivo.core.stats_store"):
        store.upsert_session(replace(ROW, session_id="s2"))
    assert store.enabled is False
    assert "statistics disabled" in caplog.text

    caplog.clear()
    with caplog.at_level("WARNING", logger="taprivo.core.stats_store"):
        store.upsert_session(replace(ROW, session_id="s3"))
    assert caplog.text == ""


def test_a_missing_parent_directory_is_created(tmp_path: Path) -> None:
    store = StatsStore(tmp_path / "nested" / "deeper" / "stats.sqlite")
    store.upsert_session(ROW)
    assert store.today("2026-09-09") is not None
    store.close()
