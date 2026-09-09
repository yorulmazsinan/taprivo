"""Whole-hand squeeze detector: open → fist → open cycles per hand, deterministic."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from taprivo.config import SqueezeConfig
from taprivo.core.events import Finger, Hand, TapEvent, TapSource
from taprivo.vision.frames import HandFrame

Phase = Literal["unknown", "open", "closed"]


@dataclass(frozen=True, slots=True)
class SqueezeParams:
    smoothing_alpha: float = 0.5
    open_level: float = 0.80
    closed_level: float = 0.45
    band_ratio: float = 0.25
    min_closed_ms: int = 120
    max_cycle_ms: int = 2500
    cooldown_ms: int = 300
    frame_gap_reset_ms: int = 250

    @classmethod
    def from_config(cls, cfg: SqueezeConfig) -> SqueezeParams:
        return cls(**cfg.model_dump())


@dataclass(frozen=True, slots=True)
class Levels:
    open_level: float
    closed_level: float

    def bands(self, ratio: float) -> tuple[float, float]:
        span = self.open_level - self.closed_level
        return self.closed_level + ratio * span, self.open_level - ratio * span


@dataclass(frozen=True, slots=True)
class HandState:
    phase: Phase
    ema: float
    levels: Levels
    seen: bool


@dataclass(frozen=True, slots=True)
class SqueezeState:
    hands: dict[Hand, HandState]


def hand_in_frame(frame: HandFrame) -> bool:
    """Whether every landmark of `frame` lies inside the camera frame.

    MediaPipe extrapolates landmark positions for fingers it can no longer see,
    such as when a hand slides out past the bottom edge of the frame. The
    extrapolated positions can push the openness feature well above the real
    "open" level, so a hand leaving the frame can look to the detector like it
    just opened again and register a false squeeze cycle. Landmarks are
    normalised to `[0.0, 1.0]`, so any landmark outside that range on `x` or
    `y` means the hand is (at least partly) out of frame and should be
    treated as not visible.
    """
    return all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y, _ in frame.landmarks)


@dataclass
class _Track:
    phase: Phase = "unknown"
    ema: float = 0.0
    seeded: bool = False
    start_ts: int = 0
    min_seen: float = 1.0
    last_cycle_ts: int = -(10**9)
    last_frame_ts: int | None = None
    hand_id: str | None = None


class SqueezeDetector:
    def __init__(
        self,
        params: SqueezeParams,
        session_id_provider: Callable[[], str],
        levels: Levels | None = None,
    ) -> None:
        self._p = params
        self._session_id = session_id_provider
        self._levels = levels or Levels(params.open_level, params.closed_level)
        self._tracks: dict[Hand, _Track] = {hand: _Track() for hand in Hand}

    def set_levels(self, levels: Levels) -> None:
        self._levels = levels

    def levels(self) -> Levels:
        return self._levels

    def reset(self) -> None:
        self._tracks = {hand: _Track() for hand in Hand}

    def state(self) -> SqueezeState:
        return SqueezeState(
            {h: HandState(t.phase, t.ema, self._levels, t.seeded) for h, t in self._tracks.items()}
        )

    def process(self, hands: tuple[HandFrame, ...], ts_ms: int) -> list[TapEvent]:
        present = {frame.hand: frame for frame in hands if hand_in_frame(frame)}
        events: list[TapEvent] = []
        for hand, track in self._tracks.items():
            frame = present.get(hand)
            if frame is None:
                if (
                    track.last_frame_ts is not None
                    and ts_ms - track.last_frame_ts > self._p.frame_gap_reset_ms
                ):
                    self._tracks[hand] = _Track()
                continue
            gap = (
                track.last_frame_ts is not None
                and frame.ts_ms - track.last_frame_ts > self._p.frame_gap_reset_ms
            )
            if track.hand_id != frame.hand_id or gap:
                track = self._tracks[hand] = _Track(hand_id=frame.hand_id)
            track.last_frame_ts = frame.ts_ms
            events.extend(self._advance(track, frame))
        return events

    def _advance(self, track: _Track, frame: HandFrame) -> list[TapEvent]:
        p = self._p
        x = frame.features.openness
        ts = frame.ts_ms
        if not track.seeded:
            track.ema = x
            track.seeded = True
        else:
            track.ema = p.smoothing_alpha * x + (1 - p.smoothing_alpha) * track.ema
        close_at, open_at = self._levels.bands(p.band_ratio)
        if track.phase == "unknown":
            if track.ema > open_at:
                track.phase = "open"
            return []
        if track.phase == "open":
            if track.ema < close_at:
                track.phase = "closed"
                track.start_ts = ts
                track.min_seen = track.ema
            return []
        track.min_seen = min(track.min_seen, track.ema)
        if ts - track.start_ts > p.max_cycle_ms:
            track.phase = "unknown"
            return []
        if track.ema > open_at:
            track.phase = "open"
            if ts - track.start_ts >= p.min_closed_ms and ts - track.last_cycle_ts >= p.cooldown_ms:
                track.last_cycle_ts = ts
                return self._events(
                    frame, ts, track.start_ts, self._levels.open_level - track.min_seen
                )
        return []

    def _events(self, frame: HandFrame, ts: int, start_ts: int, depth: float) -> list[TapEvent]:
        depth = max(depth, 0.0)
        seconds = max((ts - start_ts) / 1000.0, 1e-3)
        return [
            TapEvent(
                event_id=uuid.uuid4().hex,
                session_id=self._session_id(),
                hand=frame.hand,
                hand_id=frame.hand_id,
                finger=finger,
                timestamp_monotonic_ms=ts,
                displacement=round(depth, 4),
                velocity=round(depth / seconds, 4),
                confidence=frame.score,
                source=TapSource.CAMERA,
            )
            for finger in Finger
        ]
