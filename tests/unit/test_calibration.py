from __future__ import annotations

import statistics

from taprivo.core.events import Finger
from taprivo.vision.calibration import (
    COUNTDOWN_MS,
    NOISE_MS,
    ORDER,
    RECORD_MS,
    THRESHOLD_MAX,
    THRESHOLD_MIN,
    VISIBILITY_MS,
    CalibrationSession,
)
from taprivo.vision.detector import DetectorParams
from tests.vision_helpers import flat, pulse


def session() -> CalibrationSession:
    s = CalibrationSession(DetectorParams(), lambda: "cal")
    s.start(0)
    return s


def feed(s: CalibrationSession, frames: list, ts_end: int | None = None) -> None:
    for f in frames:
        s.process(f, f.ts_ms)
    if ts_end is not None:
        s.process(None, ts_end)


def taps_for(finger: Finger, start: int, count: int, amplitude: float = 0.5) -> list:
    """Five (or more) pulses spaced 700ms apart, bridged with flat frames so the
    inter-pulse gap never exceeds the detector's frame_gap_reset_ms and triggers a
    spurious reacquire that would swallow the in-flight tap before it releases."""
    frames: list = []
    for i in range(count):
        pulse_start = start + i * 700
        if frames:
            gap_start = frames[-1].ts_ms + 40
            gap_len = pulse_start - gap_start
            if gap_len > 0:
                frames += flat(gap_start, gap_len)
        frames += pulse(pulse_start, finger, amplitude)
    return frames


def drive_to_record(s: CalibrationSession, finger_index: int = 0, jitter: float = 0.01) -> int:
    """Pass visibility and noise, then countdowns/records for fingers before finger_index.

    Returns ts.
    """
    ts = 0
    feed(s, flat(ts, VISIBILITY_MS, jitter=jitter), VISIBILITY_MS)
    ts = VISIBILITY_MS
    feed(s, flat(ts, NOISE_MS, jitter=jitter), ts + NOISE_MS)
    ts += NOISE_MS
    for _ in range(finger_index):
        feed(s, flat(ts, COUNTDOWN_MS), ts + COUNTDOWN_MS)
        ts += COUNTDOWN_MS
        feed(s, flat(ts, RECORD_MS), ts + RECORD_MS)
        ts += RECORD_MS
    feed(s, flat(ts, COUNTDOWN_MS), ts + COUNTDOWN_MS)
    return ts + COUNTDOWN_MS


def test_visibility_repeats_until_hand_is_visible() -> None:
    s = session()
    for ts in range(0, VISIBILITY_MS, 40):  # no hand at all
        p = s.process(None, ts)
    p = s.process(None, VISIBILITY_MS)
    assert p.step == "visibility"
    assert "into view" in p.text
    feed(s, flat(VISIBILITY_MS, VISIBILITY_MS), 2 * VISIBILITY_MS)
    assert s.prompt().step == "noise"


def test_noise_floor_then_countdown_for_index() -> None:
    s = session()
    feed(s, flat(0, VISIBILITY_MS), VISIBILITY_MS)
    feed(s, flat(VISIBILITY_MS, NOISE_MS, jitter=0.02, seed=1), VISIBILITY_MS + NOISE_MS)
    p = s.prompt()
    assert p.step == "countdown" and p.finger is ORDER[0] is Finger.INDEX
    assert p.remaining_ms == COUNTDOWN_MS


def test_record_step_collects_taps_and_computes_threshold() -> None:
    s = session()
    ts = drive_to_record(s)
    assert s.prompt().step == "record" and s.prompt().finger is Finger.INDEX
    feed(s, flat(ts, 300) + taps_for(Finger.INDEX, ts + 300, 5), ts + RECORD_MS)
    # run remaining fingers with no taps
    ts += RECORD_MS
    for _ in ORDER[1:]:
        feed(s, flat(ts, COUNTDOWN_MS), ts + COUNTDOWN_MS)
        ts += COUNTDOWN_MS
        feed(s, flat(ts, RECORD_MS), ts + RECORD_MS)
        ts += RECORD_MS
    assert s.finished
    result = s.result()
    assert result is not None
    index = result.fingers[Finger.INDEX]
    assert index.status == "ok" and index.taps == 5
    expected = min(
        max(max(3 * index.sigma, 0.5 * index.median_amplitude), THRESHOLD_MIN), THRESHOLD_MAX
    )
    assert abs(index.threshold - expected) < 1e-9
    assert result.thresholds[Finger.INDEX] == index.threshold
    ring = result.fingers[Finger.RING]
    assert ring.status == "uncalibrated" and ring.taps == 0
    assert result.thresholds[Finger.RING] == DetectorParams().threshold


def test_too_many_taps_marks_uncalibrated() -> None:
    s = session()
    ts = drive_to_record(s)
    feed(s, flat(ts, 200) + taps_for(Finger.INDEX, ts + 200, 9, amplitude=0.5), ts + RECORD_MS)
    ts += RECORD_MS
    for _ in ORDER[1:]:
        feed(s, flat(ts, COUNTDOWN_MS), ts + COUNTDOWN_MS)
        ts += COUNTDOWN_MS
        feed(s, flat(ts, RECORD_MS), ts + RECORD_MS)
        ts += RECORD_MS
    result = s.result()
    assert result is not None
    assert result.fingers[Finger.INDEX].taps >= 8
    assert result.fingers[Finger.INDEX].status == "uncalibrated"


def test_threshold_clamps() -> None:
    from taprivo.vision.calibration import compute_threshold

    assert compute_threshold(sigma=0.0, median_amplitude=0.01) == THRESHOLD_MIN
    assert compute_threshold(sigma=0.5, median_amplitude=0.1) == THRESHOLD_MAX
    assert abs(compute_threshold(sigma=0.02, median_amplitude=0.5) - 0.25) < 1e-9


def test_progress_and_export() -> None:
    s = session()
    assert s.prompt().progress == 0.0
    feed(s, flat(0, VISIBILITY_MS), VISIBILITY_MS)
    assert 0 < s.prompt().progress < 1
    rows = s.export_rows()
    assert rows and set(rows[0]) >= {"ts_ms", "step", "finger", "index"}
    assert all("image" not in k for k in rows[0])


def test_sigma_uses_noise_window_only() -> None:
    s = session()
    feed(s, flat(0, VISIBILITY_MS, jitter=0.2, seed=5), VISIBILITY_MS)  # noisy visibility window
    feed(s, flat(VISIBILITY_MS, NOISE_MS, jitter=0.0), VISIBILITY_MS + NOISE_MS)  # perfectly still
    ts = VISIBILITY_MS + NOISE_MS
    for _ in ORDER:
        feed(s, flat(ts, COUNTDOWN_MS), ts + COUNTDOWN_MS)
        ts += COUNTDOWN_MS
        feed(s, flat(ts, RECORD_MS), ts + RECORD_MS)
        ts += RECORD_MS
    result = s.result()
    assert result is not None
    assert all(abs(fc.sigma) < 1e-9 for fc in result.fingers.values())
    assert (
        statistics.mean(fc.threshold for fc in result.fingers.values())
        == DetectorParams().threshold
    )
