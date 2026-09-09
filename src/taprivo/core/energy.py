"""EnergyEngine: the single authority for the Motion Energy balance."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from taprivo.config import Config
from taprivo.core.combo import ComboTracker
from taprivo.core.events import Hand, TapEvent, now_monotonic_ms
from taprivo.core.rhythm import RhythmTracker
from taprivo.core.session import Session
from taprivo.core.state import AgentStatus, AppSnapshot, McpStatus, Mode, TrackingStatus
from taprivo.core.stats_sink import DailyRow, SessionRow, StatsSink

log = logging.getLogger(__name__)

IDEMPOTENCY_LIMIT = 10_000
Listener = Callable[[AppSnapshot], None]
SinkEvent = Literal["started", "snapshot", "ended"]


class SpendError(StrEnum):
    INSUFFICIENT_ENERGY = "INSUFFICIENT_ENERGY"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    INVALID_REASON = "INVALID_REASON"
    SESSION_CHANGED = "SESSION_CHANGED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


@dataclass(frozen=True, slots=True)
class SpendRequest:
    amount: int
    reason: str
    request_id: str
    session_id: str
    project_id: str | None = None

    def payload_hash(self) -> str:
        payload = json.dumps(dataclasses.asdict(self), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SpendResult:
    success: bool
    session_id: str
    spent: int = 0
    remaining: int = 0
    transaction_id: str | None = None
    error: SpendError | None = None
    available: int | None = None


@dataclass(frozen=True, slots=True)
class TapResult:
    accepted: bool
    snapshot: AppSnapshot


class EnergyEngine:
    def __init__(
        self,
        config: Config,
        *,
        mode: Mode = "simulator",
        now_ms: Callable[[], int] = now_monotonic_ms,
        sink: StatsSink | None = None,
    ) -> None:
        self._config = config
        self._mode: Mode = mode
        self._now_ms = now_ms
        self._sink = sink
        self._closed = False
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._tracking: TrackingStatus = "inactive"
        self._mcp: McpStatus = "starting"
        self._mcp_error: str | None = None
        self._last_tool_call_utc: datetime | None = None
        self._camera_fps = 0.0
        self._detection_ratio = 0.0
        # Not session data: what the coding agent reports outlives a reset.
        self._agent: AgentStatus | None = None
        self._start_session_locked()
        self._emit("started", self._session_row_locked())

    # -- session lifecycle -------------------------------------------------

    def _start_session_locked(self) -> None:
        self._session = Session(self._now_ms, self._mode)
        self._combo = ComboTracker(
            self._config.combo.timeout_ms,
            self._config.combo.enabled,
            self._config.combo.tiers,
            self._config.combo.energy_multiplier_enabled,
        )
        self._rhythm = RhythmTracker(
            window=self._config.rhythm.window,
            tolerance=self._config.rhythm.tolerance,
            bpm_min=self._config.rhythm.bpm_min,
            bpm_max=self._config.rhythm.bpm_max,
            enabled=self._config.rhythm.enabled,
            steady_multiplier=self._config.rhythm.steady_multiplier,
        )
        self._gross = 0
        self._overflow = 0
        self._spent = 0
        self._idempotency: OrderedDict[str, tuple[str, SpendResult]] = OrderedDict()

    @property
    def session_id(self) -> str:
        return self._session.session_id

    def _available_locked(self) -> int:
        return self._gross - self._overflow - self._spent

    # -- mutations -----------------------------------------------------------

    def apply_tap(self, event: TapEvent) -> TapResult:
        with self._lock:
            if event.session_id != self._session.session_id:
                return TapResult(False, self._snapshot_locked())
            self._combo.record(event.timestamp_monotonic_ms)
            self._rhythm.record(event.timestamp_monotonic_ms)
            per_tap = round(
                self._config.energy.energy_per_tap
                * self._combo.multiplier
                * self._rhythm.multiplier
            )
            room = max(self._config.energy.max_energy - self._available_locked(), 0)
            credited = min(per_tap, room)
            self._gross += per_tap
            self._overflow += per_tap - credited
            self._session.record_combo(self._combo.count)
            self._session.record_tap(
                event.hand, event.finger, event.timestamp_monotonic_ms, event.source
            )
            snapshot = self._snapshot_locked()
        log.debug(
            "[TAP] %s:%s displacement=%.3f velocity=%.2f +%d",
            event.hand,
            event.finger,
            event.displacement,
            event.velocity,
            credited,
        )
        self._notify(snapshot)
        return TapResult(True, snapshot)

    def spend(self, request: SpendRequest) -> SpendResult:
        with self._lock:
            result, mutated = self._spend_locked(request)
            snapshot = self._snapshot_locked() if mutated else None
        if snapshot is not None:
            log.info("spend %d energy, %d remaining", result.spent, result.remaining)
            self._notify(snapshot)
        return result

    def _spend_locked(self, request: SpendRequest) -> tuple[SpendResult, bool]:
        session_id = self._session.session_id
        available = self._available_locked()

        def fail(error: SpendError) -> tuple[SpendResult, bool]:
            return SpendResult(False, session_id, error=error, available=available), False

        if request.session_id != session_id:
            return fail(SpendError.SESSION_CHANGED)
        if (
            type(request.amount) is not int
            or not 0 < request.amount <= self._config.energy.max_energy
        ):
            return fail(SpendError.INVALID_AMOUNT)
        if (
            not request.reason.strip()
            or len(request.reason) > self._config.server.max_reason_length
        ):
            return fail(SpendError.INVALID_REASON)
        digest = request.payload_hash()
        previous = self._idempotency.get(request.request_id)
        if previous is not None:
            previous_digest, previous_result = previous
            if previous_digest == digest:
                return previous_result, False
            return fail(SpendError.IDEMPOTENCY_CONFLICT)
        if request.amount > available:
            return fail(SpendError.INSUFFICIENT_ENERGY)

        self._spent += request.amount
        result = SpendResult(
            True,
            session_id,
            spent=request.amount,
            remaining=self._available_locked(),
            transaction_id=uuid.uuid4().hex,
        )
        self._idempotency[request.request_id] = (digest, result)
        while len(self._idempotency) > IDEMPOTENCY_LIMIT:
            self._idempotency.popitem(last=False)
        self._session.record_spend(request.amount, request.reason)
        return result, True

    def reset_session(self) -> AppSnapshot:
        with self._lock:
            ended = self._session_row_locked(closing=True)
            self._start_session_locked()
            started = self._session_row_locked()
            snapshot = self._snapshot_locked()
        log.info("session reset")
        self._emit("ended", ended)
        self._emit("started", started)
        self._notify(snapshot)
        return snapshot

    def heartbeat(self) -> None:
        """Persist the current session row. The caller owns the timer."""
        with self._lock:
            row = self._session_row_locked()
        self._emit("snapshot", row)

    def close(self) -> None:
        """End the current session for the statistics store. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            row = self._session_row_locked(closing=True)
        self._emit("ended", row)

    # -- statistics --------------------------------------------------------

    def _session_row_locked(self, *, closing: bool = False) -> SessionRow:
        now = self._now_ms()
        return SessionRow(
            session_id=self._session.session_id,
            started_utc=self._session.started_at_utc.isoformat(),
            ended_utc=datetime.now(UTC).isoformat() if closing else None,
            duration_s=self._session.duration_seconds(now),
            mode=self._mode,
            taps_total=self._session.taps_total,
            taps_left=self._session.taps_per_hand[Hand.LEFT],
            taps_right=self._session.taps_per_hand[Hand.RIGHT],
            squeezes=self._session.squeezes,
            generated=self._gross,
            spent=self._spent,
            overflow=self._overflow,
            max_combo=self._session.max_combo,
        )

    def _emit(self, event: SinkEvent, row: SessionRow) -> None:
        """Hand a row to the sink outside the lock; a failing sink is not fatal."""
        sink = self._sink
        if sink is None:
            return
        try:
            if event == "started":
                sink.session_started(row)
            elif event == "ended":
                sink.session_ended(row)
            else:
                sink.session_snapshot(row)
        except Exception:
            log.exception("statistics sink failed on %s", event)

    def today(self) -> DailyRow | None:
        """Totals for the current UTC day, or None without a statistics store."""
        if self._sink is None:
            return None
        try:
            return self._sink.today(datetime.now(UTC).date().isoformat())
        except Exception:
            log.exception("statistics sink failed on today")
            return None

    # -- status --------------------------------------------------------------

    def set_tracking(self, status: TrackingStatus) -> None:
        with self._lock:
            self._tracking = status
            snapshot = self._snapshot_locked()
        self._notify(snapshot)

    def set_mcp_status(self, status: McpStatus, error: str | None = None) -> None:
        with self._lock:
            self._mcp = status
            self._mcp_error = error
            snapshot = self._snapshot_locked()
        self._notify(snapshot)

    def set_agent_status(self, status: AgentStatus) -> None:
        """Record what the coding agent last reported about itself."""
        with self._lock:
            self._agent = status
            snapshot = self._snapshot_locked()
        self._notify(snapshot)

    def set_camera_stats(self, fps: float, detection_ratio: float) -> None:
        with self._lock:
            self._camera_fps = round(fps, 1)
            self._detection_ratio = round(detection_ratio, 3)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)

    def mark_tool_call(self) -> None:
        with self._lock:
            self._last_tool_call_utc = datetime.now(UTC)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)

    # -- observation -------------------------------------------------------

    def snapshot(self) -> AppSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _snapshot_locked(self) -> AppSnapshot:
        now = self._now_ms()
        return AppSnapshot(
            session_id=self._session.session_id,
            started_at_utc=self._session.started_at_utc,
            mode=self._mode,
            available=self._available_locked(),
            gross_generated=self._gross,
            overflow=self._overflow,
            spent=self._spent,
            max_energy=self._config.energy.max_energy,
            energy_per_tap=self._config.energy.energy_per_tap,
            combo=self._combo.count,
            combo_multiplier=self._combo.multiplier,
            bpm=round(self._rhythm.bpm, 1),
            rhythm_steady=self._rhythm.steady,
            rhythm_multiplier=self._rhythm.multiplier,
            taps_total=self._session.taps_total,
            taps_per_finger=dict(self._session.taps_per_finger),
            taps_per_hand=dict(self._session.taps_per_hand),
            taps_per_hand_finger=dict(self._session.taps_per_hand_finger),
            taps_per_minute=self._session.taps_per_minute(now),
            spend_count=self._session.spend_count,
            last_spend=self._session.last_spend,
            tracking=self._tracking,
            mcp=self._mcp,
            mcp_error=self._mcp_error,
            last_tool_call_utc=self._last_tool_call_utc,
            session_duration_seconds=self._session.duration_seconds(now),
            camera_fps=self._camera_fps,
            detection_ratio=self._detection_ratio,
            agent=self._agent,
        )

    def _notify(self, snapshot: AppSnapshot) -> None:
        # Notifications from concurrent mutations may be delivered out of
        # order; listeners (e.g. the HUD) self-correct on the next snapshot.
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                log.exception("engine listener failed")
