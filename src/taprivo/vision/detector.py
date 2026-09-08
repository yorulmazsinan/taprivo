"""Per-finger air-tap detector: pure and deterministic, driven by frame timestamps."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from taprivo.config import DetectorConfig
from taprivo.core.events import Finger, TapEvent, TapSource
from taprivo.vision.frames import HandFrame

Phase = Literal["idle", "active"]
FINGER_ORDER = {finger: index for index, finger in enumerate(Finger)}


@dataclass(frozen=True, slots=True)
class DetectorParams:
    smoothing_alpha: float = 0.4
    baseline_alpha: float = 0.03
    threshold: float = 0.22
    release_ratio: float = 0.4
    max_cycle_ms: int = 600
    cooldown_ms: int = 140
    attribution_window_ms: int = 120
    reacquire_guard_ms: int = 300
    frame_gap_reset_ms: int = 250

    @classmethod
    def from_config(cls, cfg: DetectorConfig) -> DetectorParams:
        return cls(**cfg.model_dump())


@dataclass(frozen=True, slots=True)
class FingerState:
    phase: Phase
    ema: float
    baseline: float
    deviation: float
    threshold: float


@dataclass(frozen=True, slots=True)
class TapCandidate:
    finger: Finger
    ts_ms: int
    start_ts_ms: int
    peak: float
    strength: float  # peak / threshold


@dataclass(frozen=True, slots=True)
class DetectorState:
    hand_id: str | None
    guarded: bool
    fingers: dict[Finger, FingerState]


@dataclass
class _Track:
    threshold: float
    phase: Phase = "idle"
    ema: float = 0.0
    baseline: float = 0.0
    start_ts: int = 0
    peak: float = 0.0
    seeded: bool = False


class TapDetector:
    def __init__(
        self,
        params: DetectorParams,
        session_id_provider: Callable[[], str],
        thresholds: dict[Finger, float] | None = None,
    ) -> None:
        self._p = params
        self._session_id = session_id_provider
        given = thresholds or {}
        self._tracks = {f: _Track(threshold=given.get(f, params.threshold)) for f in Finger}
        self._hand_id: str | None = None
        self._last_frame_ts: int | None = None
        self._last_seen_ts: int | None = None
        self._guard_until = -1
        self._pending: list[TapCandidate] = []
        self._last_event_ts: dict[Finger, int] = {f: -(10**9) for f in Finger}
        self._hand_for_events: HandFrame | None = None

    # -- configuration -------------------------------------------------------

    def set_thresholds(self, thresholds: dict[Finger, float]) -> None:
        for finger, value in thresholds.items():
            self._tracks[finger].threshold = value

    def thresholds(self) -> dict[Finger, float]:
        return {f: t.threshold for f, t in self._tracks.items()}

    def reset(self) -> None:
        for track in self._tracks.values():
            track.phase = "idle"
            track.seeded = False
            track.peak = 0.0
        self._pending.clear()
        self._hand_id = None
        self._last_frame_ts = None

    # -- processing ----------------------------------------------------------

    def process(self, frame: HandFrame | None, ts_ms: int) -> list[TapEvent]:
        if frame is None:
            return self._flush(ts_ms)
        if (
            self._last_frame_ts is None
            or frame.hand_id != self._hand_id
            or frame.ts_ms - self._last_frame_ts > self._p.frame_gap_reset_ms
        ):
            self._reacquire(frame)
        self._last_frame_ts = frame.ts_ms
        self._hand_for_events = frame
        guarded = frame.ts_ms < self._guard_until
        for finger, track in self._tracks.items():
            value = frame.features.c2[finger]
            candidate = self._advance(track, finger, value, frame.ts_ms, guarded)
            if candidate is not None:
                self._pending.append(candidate)
        return self._flush(frame.ts_ms)

    def state(self) -> DetectorState:
        return DetectorState(
            hand_id=self._hand_id,
            guarded=self._last_frame_ts is not None and self._last_frame_ts < self._guard_until,
            fingers={
                f: FingerState(t.phase, t.ema, t.baseline, t.ema - t.baseline, t.threshold)
                for f, t in self._tracks.items()
            },
        )

    # -- internals -----------------------------------------------------------

    def _reacquire(self, frame: HandFrame) -> None:
        self._hand_id = frame.hand_id
        self._guard_until = frame.ts_ms + self._p.reacquire_guard_ms
        self._pending.clear()
        for finger, track in self._tracks.items():
            track.phase = "idle"
            track.ema = frame.features.c2[finger]
            track.baseline = track.ema
            track.seeded = True
            track.peak = 0.0

    def _advance(
        self, track: _Track, finger: Finger, value: float, ts: int, guarded: bool
    ) -> TapCandidate | None:
        p = self._p
        if not track.seeded:
            track.ema = track.baseline = value
            track.seeded = True
            return None
        track.ema = p.smoothing_alpha * value + (1 - p.smoothing_alpha) * track.ema
        deviation = abs(track.ema - track.baseline)
        if track.phase == "idle":
            if deviation > track.threshold and not guarded:
                track.phase = "active"
                track.start_ts = ts
                track.peak = deviation
            else:
                track.baseline += p.baseline_alpha * (track.ema - track.baseline)
            return None
        track.peak = max(track.peak, deviation)
        if ts - track.start_ts > p.max_cycle_ms:
            track.phase = "idle"
            track.baseline = track.ema
            return None
        if deviation < p.release_ratio * track.threshold:
            track.phase = "idle"
            track.baseline = track.ema
            if ts - self._last_event_ts[finger] >= p.cooldown_ms:
                strength = track.peak / track.threshold
                return TapCandidate(finger, ts, track.start_ts, track.peak, strength)
        return None

    def _flush(self, now_ts: int) -> list[TapEvent]:
        if not self._pending:
            return []
        window = self._p.attribution_window_ms

        def sort_key(c: TapCandidate) -> tuple[float, int, int]:
            return (-c.strength, c.ts_ms, FINGER_ORDER[c.finger])

        ordered = sorted(self._pending, key=sort_key)
        accepted: list[TapCandidate] = []
        for candidate in ordered:
            if any(abs(candidate.ts_ms - a.ts_ms) <= window for a in accepted):
                continue
            accepted.append(candidate)
        mature = [c for c in accepted if now_ts - c.ts_ms >= window]
        self._pending = [c for c in accepted if c not in mature]
        events = [self._event(c) for c in sorted(mature, key=lambda c: c.ts_ms)]
        for candidate in mature:
            self._last_event_ts[candidate.finger] = candidate.ts_ms
        return events

    def _event(self, c: TapCandidate) -> TapEvent:
        frame = self._hand_for_events
        assert frame is not None
        seconds = max((c.ts_ms - c.start_ts_ms) / 1000.0, 1e-3)
        return TapEvent(
            event_id=uuid.uuid4().hex,
            session_id=self._session_id(),
            hand=frame.hand,
            hand_id=frame.hand_id,
            finger=c.finger,
            timestamp_monotonic_ms=c.ts_ms,
            displacement=round(c.peak, 4),
            velocity=round(c.peak / seconds, 4),
            confidence=frame.score,
            source=TapSource.CAMERA,
        )
