"""Local SQLite store for session and daily statistics.

Counters and timestamps only: no spend reasons, no camera data. The store is
deliberately defensive -- a statistics file is never worth crashing the app
for, so every sqlite failure disables further writes instead of propagating.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from pathlib import Path

from taprivo.core.stats_sink import DailyRow, SessionRow

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    started_utc TEXT NOT NULL,
    ended_utc   TEXT,
    duration_s  INTEGER NOT NULL,
    mode        TEXT NOT NULL,
    taps_total  INTEGER NOT NULL,
    taps_left   INTEGER NOT NULL,
    taps_right  INTEGER NOT NULL,
    squeezes    INTEGER NOT NULL,
    generated   INTEGER NOT NULL,
    spent       INTEGER NOT NULL,
    overflow    INTEGER NOT NULL,
    max_combo   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_day ON sessions (substr(started_utc, 1, 10));
CREATE TABLE IF NOT EXISTS daily (
    day            TEXT PRIMARY KEY,
    sessions       INTEGER NOT NULL,
    taps_total     INTEGER NOT NULL,
    generated      INTEGER NOT NULL,
    spent          INTEGER NOT NULL,
    active_seconds INTEGER NOT NULL
);
"""

_RECOMPUTE_DAY = """
INSERT OR REPLACE INTO daily (day, sessions, taps_total, generated, spent, active_seconds)
SELECT ?,
       COUNT(*),
       COALESCE(SUM(taps_total), 0),
       COALESCE(SUM(generated), 0),
       COALESCE(SUM(spent), 0),
       COALESCE(SUM(duration_s), 0)
  FROM sessions
 WHERE substr(started_utc, 1, 10) = ?
"""

_UPSERT_SESSION = """
INSERT OR REPLACE INTO sessions (
    session_id, started_utc, ended_utc, duration_s, mode, taps_total, taps_left,
    taps_right, squeezes, generated, spent, overflow, max_combo
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_DAILY_COLUMNS = "day, sessions, taps_total, generated, spent, active_seconds"


def day_of(started_utc: str) -> str:
    """The UTC calendar day a session belongs to, as ``YYYY-MM-DD``."""
    return started_utc[:10]


class StatsStore:
    """Session and daily counters in a single SQLite file.

    Safe to call from any thread: one connection guarded by a lock. Daily rows
    are derived, never written directly -- each session write recomputes the
    day it belongs to.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._disabled = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = self._open()
        except (sqlite3.Error, OSError) as exc:
            self._disable(exc)

    # -- setup ---------------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, SCHEMA_VERSION):
            conn.close()
            self._archive(version)
            conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL")
        with conn:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return conn

    def _archive(self, version: int) -> None:
        """Move a file written by another schema version aside and start fresh."""
        backup = self._path.with_name(f"{self._path.name}.v{version}.bak")
        self._path.replace(backup)
        for suffix in ("-wal", "-shm"):
            sidecar = self._path.with_name(self._path.name + suffix)
            sidecar.unlink(missing_ok=True)
        log.warning(
            "statistics file has schema version %d, expected %d; moved it to %s and started fresh",
            version,
            SCHEMA_VERSION,
            backup.name,
        )

    def _disable(self, exc: BaseException) -> None:
        if not self._disabled:
            self._disabled = True
            log.warning("statistics disabled for this run: %s", exc)
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    @property
    def enabled(self) -> bool:
        return not self._disabled

    # -- writes --------------------------------------------------------------

    def upsert_session(self, row: SessionRow) -> None:
        """Write a session row and recompute the day it belongs to."""
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            try:
                with conn:
                    conn.execute(
                        _UPSERT_SESSION,
                        (
                            row.session_id,
                            row.started_utc,
                            row.ended_utc,
                            row.duration_s,
                            row.mode,
                            row.taps_total,
                            row.taps_left,
                            row.taps_right,
                            row.squeezes,
                            row.generated,
                            row.spent,
                            row.overflow,
                            row.max_combo,
                        ),
                    )
                    day = day_of(row.started_utc)
                    conn.execute(_RECOMPUTE_DAY, (day, day))
            except sqlite3.Error as exc:
                self._disable(exc)

    def end_session(self, session_id: str, ended_utc: str, duration_s: int) -> None:
        """Close an existing session row without touching its counters."""
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            try:
                with conn:
                    conn.execute(
                        "UPDATE sessions SET ended_utc = ?, duration_s = ? WHERE session_id = ?",
                        (ended_utc, duration_s, session_id),
                    )
                    found = conn.execute(
                        "SELECT started_utc FROM sessions WHERE session_id = ?", (session_id,)
                    ).fetchone()
                    if found is not None:
                        day = day_of(str(found[0]))
                        conn.execute(_RECOMPUTE_DAY, (day, day))
            except sqlite3.Error as exc:
                self._disable(exc)

    # -- reads ---------------------------------------------------------------

    def today(self, day: str) -> DailyRow | None:
        """Totals for one calendar day, or None when that day has no sessions."""
        rows = self._select(
            f"SELECT {_DAILY_COLUMNS} FROM daily WHERE day = ?",
            (day,),
        )
        return rows[0] if rows else None

    def history(self, days: int) -> list[DailyRow]:
        """The most recent ``days`` days that have sessions, newest first."""
        if days <= 0:
            return []
        return self._select(
            f"SELECT {_DAILY_COLUMNS} FROM daily ORDER BY day DESC LIMIT ?",
            (days,),
        )

    def session(self, session_id: str) -> SessionRow | None:
        """One session row, mainly for inspection and tests."""
        with self._lock:
            conn = self._conn
            if conn is None:
                return None
            try:
                found = conn.execute(
                    "SELECT session_id, started_utc, ended_utc, duration_s, mode, taps_total, "
                    "taps_left, taps_right, squeezes, generated, spent, overflow, max_combo "
                    "FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            except sqlite3.Error as exc:
                self._disable(exc)
                return None
        return SessionRow(*found) if found is not None else None

    def _select(self, sql: str, args: tuple[object, ...]) -> list[DailyRow]:
        with self._lock:
            conn = self._conn
            if conn is None:
                return []
            try:
                found = conn.execute(sql, args).fetchall()
            except sqlite3.Error as exc:
                self._disable(exc)
                return []
        return [DailyRow(*row) for row in found]

    # -- sink protocol -------------------------------------------------------

    def session_started(self, row: SessionRow) -> None:
        self.upsert_session(row)

    def session_snapshot(self, row: SessionRow) -> None:
        self.upsert_session(row)

    def session_ended(self, row: SessionRow) -> None:
        self.upsert_session(row)

    # -- teardown ------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
            if conn is None:
                return
            try:
                conn.close()
            except sqlite3.Error as exc:
                log.warning("closing the statistics file failed: %s", exc)
