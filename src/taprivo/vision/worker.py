"""Capture thread: camera → tracker → detector → engine, with stats and status."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger, TapEvent, now_monotonic_ms
from taprivo.core.state import TrackingStatus
from taprivo.vision.calibration import CalibrationSession
from taprivo.vision.camera import STALE_MS, CameraSource
from taprivo.vision.detector import DetectorState, TapDetector
from taprivo.vision.frames import Frame, FrameStats, HandFrame
from taprivo.vision.tracker import HandTracker

log = logging.getLogger(__name__)

PreviewCallback = Callable[[Frame, HandFrame | None, DetectorState], None]
TRACKING_WINDOW_MS = 1000
TRACKING_MIN_RATIO = 0.5


class StatsWindow:
    """Thread-safe: `captured`/`processed` are fed from the worker loop while
    `stats`/`detection_ratio` may be read concurrently from another thread."""

    def __init__(self, window_ms: int = 2000) -> None:
        self._window = window_ms
        self._captured: deque[int] = deque()
        self._processed: deque[tuple[int, bool]] = deque()
        self._lock = threading.Lock()

    def captured(self, ts_ms: int) -> None:
        with self._lock:
            self._captured.append(ts_ms)
            self._trim_locked(ts_ms)

    def processed(self, ts_ms: int, detected: bool) -> None:
        with self._lock:
            self._processed.append((ts_ms, detected))
            self._trim_locked(ts_ms)

    def _trim_locked(self, now_ms: int) -> None:
        cutoff = now_ms - self._window
        while self._captured and self._captured[0] < cutoff:
            self._captured.popleft()
        while self._processed and self._processed[0][0] < cutoff:
            self._processed.popleft()

    def detection_ratio(self, now_ms: int, window_ms: int) -> float:
        with self._lock:
            recent = [d for ts, d in self._processed if ts >= now_ms - window_ms]
        return sum(recent) / len(recent) if recent else 0.0

    def stats(self, now_ms: int) -> FrameStats:
        with self._lock:
            self._trim_locked(now_ms)
            seconds = self._window / 1000
            last = self._captured[-1] if self._captured else None
            recent = [d for ts, d in self._processed if ts >= now_ms - self._window]
            captured_fps = round(len(self._captured) / seconds, 1)
            processed_fps = round(len(self._processed) / seconds, 1)
        detection_ratio = sum(recent) / len(recent) if recent else 0.0
        return FrameStats(
            captured_fps=captured_fps,
            processed_fps=processed_fps,
            detection_ratio=round(detection_ratio, 3),
            last_frame_age_ms=(now_ms - last) if last is not None else 0,
        )


class VisionWorker(threading.Thread):
    def __init__(
        self,
        engine: EnergyEngine,
        detector: TapDetector,
        source_factory: Callable[[], CameraSource],
        tracker_factory: Callable[[], HandTracker],
        *,
        preview: PreviewCallback | None = None,
        preview_fps: int = 15,
        now_ms: Callable[[], int] = now_monotonic_ms,
        stats_every_ms: int = 500,
    ) -> None:
        super().__init__(name="taprivo-vision", daemon=True)
        self._engine = engine
        self._detector = detector
        self._source_factory = source_factory
        self._tracker_factory = tracker_factory
        self._preview = preview
        self._preview_interval = 1000 // max(preview_fps, 1)
        self._now_ms = now_ms
        self._stats_every = stats_every_ms
        self._stop_event = threading.Event()
        self._started = threading.Event()
        self._lock = threading.Lock()
        self._stats = StatsWindow()
        self._calibration: CalibrationSession | None = None
        self._status: TrackingStatus = "inactive"
        self._last_frame_ts = 0
        self.error: str | None = None

    # -- control -------------------------------------------------------------

    def wait_started(self, timeout: float = 5.0) -> bool:
        return self._started.wait(timeout)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self.join(timeout)

    def stats(self) -> FrameStats:
        # StatsWindow guards its own state; no need for the worker lock here.
        return self._stats.stats(self._last_frame_ts)

    def set_calibration(self, session: CalibrationSession | None) -> None:
        with self._lock:
            self._calibration = session

    @property
    def calibration(self) -> CalibrationSession | None:
        with self._lock:
            return self._calibration

    def apply_thresholds(self, thresholds: dict[Finger, float]) -> None:
        with self._lock:
            self._detector.set_thresholds(thresholds)

    def thresholds(self) -> dict[Finger, float]:
        with self._lock:
            return self._detector.thresholds()

    # -- loop ----------------------------------------------------------------

    def run(self) -> None:
        source = self._source_factory()
        tracker: HandTracker | None = None
        try:
            source.open()
            tracker = self._tracker_factory()
            self._started.set()
            self._loop(source, tracker)
        except Exception as exc:
            self.error = str(exc)
            log.warning("vision worker stopped: %s", exc)
            self._started.set()
        finally:
            if tracker is not None:
                tracker.close()
            source.close()
            self._set_status("inactive")

    def _loop(self, source: CameraSource, tracker: HandTracker) -> None:
        last_preview = -(10**9)
        last_stats = -(10**9)
        last_frame_ts: int | None = None
        while not self._stop_event.is_set():
            frame = source.read()
            now = self._now_ms()
            if frame is None:
                if last_frame_ts is not None and now - last_frame_ts > STALE_MS:
                    self._set_status("stale")
                with self._lock:
                    self._tick_calibration_locked(None, now)
                    events = self._detector.process(None, now)
                self._emit(events)
                time.sleep(0.005)
                continue
            last_frame_ts = frame.ts_ms
            self._last_frame_ts = frame.ts_ms
            self._stats.captured(frame.ts_ms)
            if source.no_signal:
                self._set_status("no_signal")
                self._stats.processed(frame.ts_ms, False)
                continue
            hand = tracker.process(frame)
            self._stats.processed(frame.ts_ms, hand is not None)
            with self._lock:
                self._tick_calibration_locked(hand, frame.ts_ms)
                events = self._detector.process(hand, frame.ts_ms)
            self._emit(events)
            ratio = self._stats.detection_ratio(frame.ts_ms, TRACKING_WINDOW_MS)
            self._set_status("tracking" if ratio >= TRACKING_MIN_RATIO else "stale")
            # Pacing below is measured in the camera's own timestamp domain
            # (frame.ts_ms), not the injected wall clock: `now` only tracks
            # staleness (frames stopping), while frame.ts_ms is what the
            # StatsWindow entries and preview cadence are keyed by.
            if frame.ts_ms - last_stats >= self._stats_every:
                s = self._stats.stats(frame.ts_ms)
                self._engine.set_camera_stats(s.processed_fps, s.detection_ratio)
                last_stats = frame.ts_ms
            if self._preview is not None and frame.ts_ms - last_preview >= self._preview_interval:
                last_preview = frame.ts_ms
                with self._lock:
                    state = self._detector.state()
                try:
                    self._preview(frame, hand, state)
                except Exception:
                    log.exception("preview callback failed")

    def _tick_calibration_locked(self, hand: HandFrame | None, ts_ms: int) -> None:
        if self._calibration is not None and not self._calibration.finished:
            self._calibration.process(hand, ts_ms)

    def _emit(self, events: list[TapEvent]) -> None:
        for event in events:
            self._engine.apply_tap(event)

    def _set_status(self, status: TrackingStatus) -> None:
        if status != self._status:
            self._status = status
            self._engine.set_tracking(status)
