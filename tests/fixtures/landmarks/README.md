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

`squeeze-2026-09-09.csv`: MediaPipe Hand Landmarker output recorded on a MacBook FaceTime
camera at 29 fps on 2026-09-09, first a right hand alone doing open→fist→open squeeze
cycles for about 5 seconds, then both hands squeezing together for about 20 seconds. Same
columns as the spike file, plus rows sharing a `ts_ms` are the two hands of one frame. No
image data. Some frames catch a hand sliding out past the edge of the frame, where
MediaPipe extrapolates the missing landmarks and openness spikes; the detector's edge
guard (`hand_in_frame`) treats those frames as hand-not-visible. Replaying it with default
`SqueezeParams` yields 47 squeeze cycles (27 right, 20 left); `squeeze-2026-09-09.events.json`
is the pinned output and is checked by
`tests/unit/test_squeeze.py::test_field_recording_counts_both_hands`. Every `*.events.json`
fixture now carries a `hand` key alongside `ts_ms` and `finger`, so it can distinguish
which hand raised each event; regenerate deliberately (and review the diff) when squeeze
detector behaviour changes on purpose.

`typing-2026-09-09.csv`: MediaPipe Hand Landmarker output recorded on a MacBook FaceTime
camera at 29 fps on 2026-09-09 during a 3-minute typing test, with hands mostly resting at
the bottom edge of the frame. Same columns as the other fixtures. It is a negative sample
for the edge guard: replaying it with default `SqueezeParams` yields 3 genuine right-hand
squeeze cycles; without the guard it counts 5, with 2 extra cycles coming from a hand
sliding out of the bottom of the frame and MediaPipe extrapolating the missing fingers.
`typing-2026-09-09.events.json` is the pinned output and is checked by
`tests/unit/test_squeeze.py::test_typing_recording_counts_few_cycles`; regenerate
deliberately (and review the diff) when squeeze detector behaviour changes on purpose.
