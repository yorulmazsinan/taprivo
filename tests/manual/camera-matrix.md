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
8. Optional: export the calibration CSV and attach it to the PR as `tests/fixtures/landmarks/squeeze-<date>.csv` with its pinned `*.events.json`.

## Results

| Date | Mac / macOS | Camera | Light | fps | Calib cycles / levels | 20-squeeze run | Rest (3 min) | Typing (3 min) | Two hands (5×) | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
