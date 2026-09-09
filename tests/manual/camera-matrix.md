# Camera field test protocol

Run before every release that touches `vision/`. One row per environment.

## Protocol
1. `uv run taprivo` → **Open Camera** → pick the built-in camera → **Start Camera**.
2. **Calibrate**: open hand, fist, then squeeze 5 times. Note the cycle count and the levels (expected 3–7 cycles, status ok).
3. Squeeze run: 20 deliberate squeezes with one hand at about one per second. Note the cycles counted (energy / 50). Expected 18–22.
4. Rest test: keep an open hand visible and still for 3 minutes. Expected 0 cycles.
5. Typing test: type on the keyboard with both hands in view for 3 minutes. Expected ≤ 2 cycles.
6. Two hands: squeeze both hands together 5 times. Expected 10 cycles (5 per hand).
7. `uv run taprivo doctor --camera-probe` for processed fps (expected ≥ 20).
8. Optional: record the landmarks of the 20-squeeze run in the fixture format described in `tests/fixtures/landmarks/README.md` and pin it as `tests/fixtures/landmarks/squeeze-<date>.csv` with its `*.events.json`.

## Results

| Date | Mac / macOS | Camera | Light | fps | Calib cycles / levels | 20-squeeze run | Rest (3 min) | Typing (3 min) | Two hands (5×) | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-09 | MacBook (Apple M3) / macOS 26 | FaceTime HD (built-in) | desk lamp, daytime | 29 | defaults 0.80 / 0.45 | 48 counted (R 28 / L 20, both hands, cadence ≈ 0.9 s) | pending | pending | pending | iPhone Continuity Camera takes index 0 with black frames; FaceTime is index 1 |
