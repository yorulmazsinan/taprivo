# Landmark fixtures

`spike-2026-09-08.csv`: 628 frames of MediaPipe Hand Landmarker output (21 normalised
landmarks per frame, ~23 fps) recorded on a MacBook FaceTime camera while a right hand
performed air-taps in front of the camera. Columns: `ts_ms`, `hand`, `score`, `x0..z20`.
No image data. `spike-2026-09-08.events.json` is the detector output for this file with
default parameters and is pinned by `tests/unit/test_detector.py::test_recorded_replay_is_pinned`;
regenerate it deliberately (and review the diff) when detector behaviour changes on purpose.
