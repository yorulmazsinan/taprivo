# Landmark fixtures

`spike-2026-09-08.csv`: 628 frames of MediaPipe Hand Landmarker output (21 normalised
landmarks per frame, ~23 fps) recorded on a MacBook FaceTime camera while a right hand
performed air-taps in front of the camera. Columns: `ts_ms`, `hand`, `score`, `x0..z20`.
No image data. The recording predates the squeeze pivot and contains per-finger air-taps,
not open→fist→open squeeze cycles, so it stays only as a determinism pin, not a behaviour
sample. `spike-2026-09-08.events.json` is the whole-hand squeeze detector's output for this
file with default parameters (currently `[]`, since no squeeze cycles occur in it) and is
pinned by `tests/unit/test_squeeze.py::test_recorded_replay_is_deterministic_and_pinned`;
regenerate it deliberately (and review the diff) when squeeze detector behaviour changes on
purpose.
