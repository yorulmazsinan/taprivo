from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.vision.calibration import CalibrationSession
from taprivo.vision.camera import CameraError, CameraSource
from taprivo.vision.frames import Frame, HandFrame
from taprivo.vision.squeeze import SqueezeDetector, SqueezeParams
from taprivo.vision.worker import StatsWindow, VisionWorker
from tests.vision_helpers import squeeze_cycle, steady


class ScriptedSource(CameraSource):
    """Yields frames from a script at a fixed pace; each entry is (delay_ms, frame_or_None)."""

    def __init__(self, script: list[tuple[int, Frame | None]], fail_open: bool = False) -> None:
        super().__init__(index=9, factory=lambda i: None)
        self._script = list(script)
        self._fail_open = fail_open
        self.closed = False
        self.opened = False

    def open(self) -> None:
        if self._fail_open:
            raise CameraError("camera 9 could not be opened (permission denied or device missing)")
        self.opened = True

    @property
    def is_open(self) -> bool:
        return self.opened and not self.closed

    def read(self) -> Frame | None:
        if not self._script:
            time.sleep(0.01)
            return None
        delay, frame = self._script.pop(0)
        time.sleep(delay / 1000)
        if frame is not None:
            self.last_frame_ts = frame.ts_ms
        return frame

    def close(self) -> None:
        self.closed = True


class ScriptedTracker:
    """Maps frame ts → hands tuple from a dict; () for frames without a hand."""

    def __init__(self, hands: dict[int, tuple[HandFrame, ...]]) -> None:
        self._hands = hands
        self.closed = False

    def process(self, frame: Frame) -> tuple[HandFrame, ...]:
        return self._hands.get(frame.ts_ms, ())

    def close(self) -> None:
        self.closed = True


def blank(ts: int) -> Frame:
    return Frame(ts_ms=ts, image=np.zeros((4, 4, 3), dtype=np.uint8))


def hand_script(
    frames: list[tuple[HandFrame, ...]],
) -> tuple[list[tuple[int, Frame | None]], dict[int, tuple[HandFrame, ...]]]:
    ts_list = [hands[0].ts_ms for hands in frames if hands]
    return [(5, blank(ts)) for ts in ts_list], {hands[0].ts_ms: hands for hands in frames if hands}


@pytest.fixture
def engine() -> EnergyEngine:
    return EnergyEngine(Config())


def run_worker(  # type: ignore[no-untyped-def]
    engine: EnergyEngine,
    source: ScriptedSource,
    tracker: ScriptedTracker,
    seconds: float = 1.5,
    **kw,
) -> VisionWorker:
    detector = SqueezeDetector(SqueezeParams(), lambda: engine.session_id)
    worker = VisionWorker(engine, detector, lambda: source, lambda: tracker, **kw)
    worker.start()
    assert worker.wait_started(2.0)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and (source._script):
        time.sleep(0.02)
    time.sleep(0.3)
    worker.stop()
    return worker


def test_taps_reach_engine_and_tracking_becomes_active(engine: EnergyEngine) -> None:
    frames = steady(0, 600) + squeeze_cycle(600) + steady(1440, 600)
    script, hands = hand_script(frames)
    source = ScriptedSource(script)
    tracker = ScriptedTracker(hands)
    worker = run_worker(engine, source, tracker)
    snap = engine.snapshot()
    assert snap.available == 50  # one squeeze cycle == five finger events == 50 energy
    assert source.closed
    assert tracker.closed  # run()'s finally closes the tracker alongside the source
    assert worker.error is None
    assert snap.camera_fps > 0 and 0 < snap.detection_ratio <= 1
    assert snap.tracking == "inactive"  # stop() resets tracking


def test_tracking_status_transitions(engine: EnergyEngine) -> None:
    seen: list[str] = []
    engine.subscribe(lambda s: seen.append(s.tracking))
    frames = steady(0, 800)
    script, hands = hand_script(frames)
    script += [(50, blank(900 + i * 40)) for i in range(5)]  # frames without a hand
    run_worker(engine, ScriptedSource(script), ScriptedTracker(hands))
    assert "tracking" in seen
    assert seen[-1] == "inactive"


def test_stale_when_no_frames_arrive(engine: EnergyEngine) -> None:
    clock = [0]
    frames = steady(0, 400)
    script, hands = hand_script(frames)
    source = ScriptedSource(script)
    worker = VisionWorker(
        engine,
        SqueezeDetector(SqueezeParams(), lambda: engine.session_id),
        lambda: source,
        lambda: ScriptedTracker(hands),
        now_ms=lambda: clock[0],
    )
    worker.start()
    assert worker.wait_started(2.0)
    time.sleep(0.4)  # script consumed
    clock[0] = 5000  # 5 s "later" with no frames
    time.sleep(0.2)
    assert engine.snapshot().tracking == "stale"
    worker.stop()


def test_open_failure_surfaces_error_and_inactive(engine: EnergyEngine) -> None:
    source = ScriptedSource([], fail_open=True)
    tracker_calls: list[ScriptedTracker] = []

    def tracker_factory() -> ScriptedTracker:
        tracker = ScriptedTracker({})
        tracker_calls.append(tracker)
        return tracker

    worker = VisionWorker(
        engine,
        SqueezeDetector(SqueezeParams(), lambda: engine.session_id),
        lambda: source,
        tracker_factory,
    )
    worker.start()
    worker.join(2.0)
    assert not worker.is_alive()
    assert worker.error is not None and "permission" in worker.error
    assert engine.snapshot().tracking == "inactive"
    # run()'s try block only reaches `tracker = self._tracker_factory()` after
    # `source.open()` succeeds, so a failed open means the tracker is never
    # constructed at all (nothing to close).
    assert tracker_calls == []


def test_no_signal_status(engine: EnergyEngine) -> None:
    seen: list[str] = []
    engine.subscribe(lambda s: seen.append(s.tracking))
    source = ScriptedSource([(5, blank(i * 40)) for i in range(30)])
    source.no_signal = True
    worker = run_worker(engine, source, ScriptedTracker({}), seconds=1.0)
    assert worker.error is None
    assert "no_signal" in seen
    assert seen[-1] == "inactive"


def test_preview_callback_is_throttled(engine: EnergyEngine) -> None:
    calls: list[int] = []
    frames = steady(0, 1000, fps=50)  # 50 frames in 1 s
    script, hands = hand_script(frames)
    run_worker(
        engine,
        ScriptedSource(script),
        ScriptedTracker(hands),
        preview=lambda f, h, s: calls.append(f.ts_ms),
        preview_fps=10,
    )
    assert 5 <= len(calls) <= 15


def test_calibration_session_receives_frames(engine: EnergyEngine) -> None:
    frames = steady(0, 2500)
    script, hands = hand_script(frames)
    source = ScriptedSource(script)
    detector = SqueezeDetector(SqueezeParams(), lambda: engine.session_id)
    worker = VisionWorker(engine, detector, lambda: source, lambda: ScriptedTracker(hands))
    session = CalibrationSession(SqueezeParams(), lambda: engine.session_id)
    session.start(0)
    worker.set_calibration(session)
    worker.start()
    assert worker.wait_started(2.0)
    while source._script:
        time.sleep(0.02)
    worker.stop()
    assert session.prompt().step in ("open", "fist", "squeeze")


def test_stats_window() -> None:
    w = StatsWindow(window_ms=1000)
    for ts in range(0, 1000, 50):
        w.captured(ts)
        w.processed(ts, detected=ts % 100 == 0)
    s = w.stats(1000)
    assert 18 <= s.captured_fps <= 20
    assert 18 <= s.processed_fps <= 20
    assert abs(s.detection_ratio - 0.5) < 0.01
    assert s.last_frame_age_ms == 50


def test_stats_window_thread_safety() -> None:
    """`captured`/`processed` are fed from the worker thread while `stats`/
    `detection_ratio` may be read from another (e.g. the UI thread polling for
    a HUD update); StatsWindow must not corrupt its deques under that race."""
    w = StatsWindow(window_ms=200)
    stop = threading.Event()
    errors: list[BaseException] = []

    def feed() -> None:
        try:
            ts = 0
            while not stop.is_set():
                w.captured(ts)
                w.processed(ts, detected=ts % 2 == 0)
                ts += 1
        except BaseException as exc:
            errors.append(exc)

    def read() -> None:
        try:
            while not stop.is_set():
                w.stats(1_000_000_000)
                w.detection_ratio(1_000_000_000, 200)
        except BaseException as exc:
            errors.append(exc)

    feeder = threading.Thread(target=feed)
    reader = threading.Thread(target=read)
    feeder.start()
    reader.start()
    time.sleep(0.3)
    stop.set()
    feeder.join(2.0)
    reader.join(2.0)
    assert not feeder.is_alive() and not reader.is_alive()
    assert errors == []
