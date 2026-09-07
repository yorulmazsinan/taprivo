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

from taprivo.config import Config
from taprivo.core.combo import ComboTracker
from taprivo.core.events import TapEvent, now_monotonic_ms
from taprivo.core.session import Session
from taprivo.core.state import AppSnapshot, McpStatus, Mode, TrackingStatus

log = logging.getLogger(__name__)

IDEMPOTENCY_LIMIT = 10_000
Listener = Callable[[AppSnapshot], None]


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
    ) -> None:
        self._config = config
        self._mode: Mode = mode
        self._now_ms = now_ms
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._tracking: TrackingStatus = "inactive"
        self._mcp: McpStatus = "starting"
        self._mcp_error: str | None = None
        self._last_tool_call_utc: datetime | None = None
        self._start_session_locked()

    # -- session lifecycle -------------------------------------------------

    def _start_session_locked(self) -> None:
        self._session = Session(self._now_ms, self._mode)
        self._combo = ComboTracker(self._config.combo.timeout_ms, self._config.combo.enabled)
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
            per_tap = self._config.energy.energy_per_tap
            room = max(self._config.energy.max_energy - self._available_locked(), 0)
            credited = min(per_tap, room)
            self._gross += per_tap
            self._overflow += per_tap - credited
            self._combo.record(event.timestamp_monotonic_ms)
            self._session.record_tap(event.finger, event.timestamp_monotonic_ms)
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
            self._start_session_locked()
            snapshot = self._snapshot_locked()
        log.info("session reset")
        self._notify(snapshot)
        return snapshot

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
            taps_total=self._session.taps_total,
            taps_per_finger=dict(self._session.taps_per_finger),
            taps_per_minute=self._session.taps_per_minute(now),
            spend_count=self._session.spend_count,
            last_spend=self._session.last_spend,
            tracking=self._tracking,
            mcp=self._mcp,
            mcp_error=self._mcp_error,
            last_tool_call_utc=self._last_tool_call_utc,
            session_duration_seconds=self._session.duration_seconds(now),
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
