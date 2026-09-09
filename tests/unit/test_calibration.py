from __future__ import annotations

import threading
from collections.abc import Sequence

from taprivo.core.events import Hand
from taprivo.vision.calibration import (
    FIST_MS,
    OPEN_MS,
    SQUEEZE_MS,
    VISIBILITY_MS,
    CalibrationSession,
)
from taprivo.vision.frames import HandFrame
from taprivo.vision.squeeze import SqueezeParams
from tests.vision_helpers import squeeze_cycle, steady


def session() -> CalibrationSession:
    s = CalibrationSession(SqueezeParams(), lambda: "cal")
    s.start(0)
    return s


def feed(
    s: CalibrationSession, frames: Sequence[tuple[HandFrame, ...]], ts_end: int | None = None
) -> None:
    for hands in frames:
        s.process(hands, hands[0].ts_ms)
    if ts_end is not None:
        s.process((), ts_end)


def drive_to_squeeze(
    s: CalibrationSession, open_value: float = 0.9, closed_value: float = 0.3
) -> int:
    """Pass visibility, open and fist so the session is ready for the squeeze step.

    Returns the ts_ms at which the squeeze step starts.
    """
    ts = 0
    feed(s, steady(ts, VISIBILITY_MS, value=open_value), VISIBILITY_MS)
    ts = VISIBILITY_MS
    feed(s, steady(ts, OPEN_MS, value=open_value), ts + OPEN_MS)
    ts += OPEN_MS
    feed(s, steady(ts, FIST_MS, value=closed_value), ts + FIST_MS)
    ts += FIST_MS
    return ts


def test_visibility_repeats_until_hand_is_visible() -> None:
    s = session()
    for ts in range(0, VISIBILITY_MS, 40):  # no hand at all
        s.process((), ts)
    p = s.process((), VISIBILITY_MS)
    assert p.step == "visibility"
    assert "into view" in p.text
    feed(s, steady(VISIBILITY_MS, VISIBILITY_MS), 2 * VISIBILITY_MS)
    assert s.prompt().step == "open"


def test_prompts_follow_the_step_sequence() -> None:
    s = session()
    assert s.prompt().text == "Show your hand to the camera"
    ts = VISIBILITY_MS
    feed(s, steady(0, VISIBILITY_MS), ts)
    assert s.prompt().text == "Open your hand wide"
    feed(s, steady(ts, OPEN_MS), ts + OPEN_MS)
    ts += OPEN_MS
    assert s.prompt().text == "Make a fist"
    feed(s, steady(ts, FIST_MS, value=0.3), ts + FIST_MS)
    ts += FIST_MS
    assert s.prompt().text == "Squeeze your hand 5 times (open, fist, open)"


def test_open_and_fist_steps_advance_after_their_windows() -> None:
    s = session()
    ts = drive_to_squeeze(s, open_value=0.9, closed_value=0.3)
    assert s.prompt().step == "squeeze"
    assert ts == VISIBILITY_MS + OPEN_MS + FIST_MS


def test_five_cycles_yield_ok_status_and_calibrated_levels() -> None:
    s = session()
    ts = drive_to_squeeze(s, open_value=0.9, closed_value=0.3)
    frames: list[tuple[HandFrame, ...]] = []
    for i in range(5):
        frames += squeeze_cycle(ts + i * 1200, open_value=0.9, closed_value=0.3)
    feed(s, frames, ts + SQUEEZE_MS)
    assert s.finished
    result = s.result()
    assert result is not None
    assert result.status == "ok"
    assert result.cycles == 5
    assert abs(result.levels.open_level - 0.9) < 1e-9
    assert abs(result.levels.closed_level - 0.3) < 1e-9


def test_two_hands_five_cycles_each_still_yields_ok() -> None:
    # A user following "Squeeze your hand 5 times" with both hands should not
    # be penalised for the sum (10) exceeding MAX_CYCLES (7); the decision
    # looks at the most active hand (5), which is in range.
    s = session()
    ts = drive_to_squeeze(s, open_value=0.9, closed_value=0.3)
    frames: list[tuple[HandFrame, ...]] = []
    for i in range(5):
        right = squeeze_cycle(ts + i * 1200, hand=Hand.RIGHT, open_value=0.9, closed_value=0.3)
        left = squeeze_cycle(ts + i * 1200, hand=Hand.LEFT, open_value=0.9, closed_value=0.3)
        frames += [r + h for r, h in zip(right, left, strict=True)]
    feed(s, frames, ts + SQUEEZE_MS)
    assert s.finished
    result = s.result()
    assert result is not None
    assert result.status == "ok"
    assert result.cycles == 5


def test_too_few_cycles_marks_uncalibrated_with_default_levels() -> None:
    s = session()
    ts = drive_to_squeeze(s, open_value=0.9, closed_value=0.3)
    frames = squeeze_cycle(ts, open_value=0.9, closed_value=0.3)
    feed(s, frames, ts + SQUEEZE_MS)
    assert s.finished
    result = s.result()
    assert result is not None
    assert result.status == "uncalibrated"
    assert result.cycles == 1
    assert result.levels.open_level == SqueezeParams().open_level
    assert result.levels.closed_level == SqueezeParams().closed_level


def test_narrow_span_marks_uncalibrated() -> None:
    s = session()
    ts = drive_to_squeeze(s, open_value=0.5, closed_value=0.4)  # span 0.1 < MIN_SPAN (0.15)
    feed(s, steady(ts, SQUEEZE_MS, value=0.5), ts + SQUEEZE_MS)
    result = s.result()
    assert result is not None
    assert result.status == "uncalibrated"
    assert result.levels.open_level == SqueezeParams().open_level
    assert result.levels.closed_level == SqueezeParams().closed_level


def test_progress_and_export() -> None:
    s = session()
    assert s.prompt().progress == 0.0
    feed(s, steady(0, VISIBILITY_MS), VISIBILITY_MS)
    assert 0 < s.prompt().progress < 1
    rows = s.export_rows()
    assert rows
    assert set(rows[0]) >= {
        "ts_ms",
        "step",
        "hand",
        "openness",
        "thumb",
        "index",
        "middle",
        "ring",
        "pinky",
    }
    assert all("image" not in k for k in rows[0])


def test_concurrent_process_and_reads_are_thread_safe() -> None:
    # Simulates the real deployment: the worker thread drives process() while
    # the Qt tick timer concurrently reads prompt()/result()/finished/export_rows().
    s = session()
    frames = steady(0, VISIBILITY_MS + OPEN_MS, value=0.9)
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for hands in frames:
                s.process(hands, hands[0].ts_ms)
        except BaseException as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(500):
                s.prompt()
                s.result()
                _ = s.finished
                s.export_rows()
        except BaseException as exc:
            errors.append(exc)

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)
    writer_thread.start()
    reader_thread.start()
    writer_thread.join(timeout=5)
    reader_thread.join(timeout=5)
    assert not writer_thread.is_alive()
    assert not reader_thread.is_alive()
    assert not errors
