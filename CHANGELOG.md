# Changelog

All notable changes to this project are documented here. The format follows
Keep a Changelog and the project uses Semantic Versioning.

## [Unreleased]

### Added
- macOS packaging script: `packaging/macos/build.sh` builds a standalone `Taprivo.app` with PyInstaller and can sign it with a Developer ID, notarize it and wrap it in a DMG (`build`, `sign`, `notarize`, `dmg`, `all`). Apple Silicon only; see `packaging/macos/README.md`.

## [0.1.0b3] - 2026-09-09

Beta preview with a Cursor adapter, persistent statistics and a rhythm bonus.

### Added
- Steady-beat bonus: taps that hold an even tempo between 60 and 240 BPM are worth 1.25× more energy (`rhythm.steady_multiplier`), stacked on the combo multiplier. The HUD rate line shows the tempo and highlights it while the beat holds, `taprivo stats` prints a `Rhythm:` line, and `get_stats` reports `bpm`, `rhythm_steady` and `rhythm_multiplier`.
- Cursor adapter: `taprivo setup cursor` registers the MCP endpoint in `~/.cursor/mcp.json` (mode 0600, backup first), `--install-instructions` writes `~/.cursor/taprivo.md` and, with `--project`, a `.cursor/rules/taprivo.mdc` rule; `taprivo remove cursor` undoes it and `taprivo doctor` reports the Cursor registration and instruction checks.
- Project `.cursor/mcp.json` entries reference `${env:TAPRIVO_TOKEN}` instead of the literal token.
- `--json` output for the `setup` and `remove` commands of both agents, which now share one code path.
- Global install instructions: `uv tool install` (or pipx) from the tagged repository, with upgrade and uninstall commands.
- Persistent statistics: each session and its daily totals are stored in `~/.config/taprivo/stats.sqlite` (counters and timestamps only, no spend reasons and no camera data), with a new `stats` config section (`enabled`, `path`).
- `taprivo stats --today` and `taprivo stats --history [--days N]` read that file directly, so they work while Taprivo is not running; both support `--json`.
- Plain `taprivo stats` shows a `Today:` line with the day's sessions, taps and energy.
- MCP `get_stats` gained an additive `today` object (`sessions`, `taps_total`, `generated`, `spent`, `active_seconds`), null when statistics are disabled or the day is empty.

## [0.1.0b2] - 2026-09-09

Beta preview with two-hand keyboard drumming.

### Added
- Keyboard drumming with two hands: the left hand taps `1` `2` `3` `4` (pinky to index) and the right hand `7` `8` `9` `0` (index to pinky), with a chip per key in the HUD and per-hand counts in `get_stats` and `taprivo stats`.
- A sliding one-second tap cap shared by both hands (`simulator.max_taps_per_second`, default 12); taps above it are ignored, and key auto-repeat never counts.
- Combo multiplier tiers (`combo.tiers`, by default 1.5× from 10 consecutive taps and 2× from 25), shown on the HUD combo line and reported as `combo_multiplier` by `get_stats`.

### Changed
- The combo energy multiplier is on by default (`combo.energy_multiplier_enabled: true`); each tap is worth `energy_per_tap` times the multiplier the combo has reached.
- Keys `1`–`5` no longer tap thumb…pinky of a single hand; the two-hand mapping replaces them, and keys `5` and `6` do nothing.
- The HUD window is wider so that four key chips fit a row per hand.

## [0.1.0b1] - 2026-09-09

Beta preview. Install from source with `uv sync`; there is no signed
macOS package yet.

### Added
- Camera-based squeeze detection (open → fist → open) with MediaPipe hand landmarks; two hands tracked independently; one squeeze counts as five taps (50 energy).
- Camera window: device probing with no-signal detection, live preview, per-hand openness meters, guided calibration with session levels and feature-only CSV export.
- Redesigned HUD and Camera window with a dark/light theme; new `hud.theme` config key (`system`, `dark`, `light`).
- CLI: `taprivo camera list`, `taprivo calibrate`; `status` shows camera fps and hand detection ratio; `doctor` checks the bundled model, mediapipe and camera devices, and `doctor --camera-probe` opens the camera to check permission and measure processed fps.
- MCP `get_session` fields `camera_fps` and `detection_ratio`.

### Changed
- Pin mediapipe to 0.10.33; a test fails if the installed wheel contains telemetry code.
- Product framing: the camera counts whole-hand squeezes rather than per-finger taps; finger tapping stays with the keyboard simulator.
- The squeeze detector ignores hands that are partly outside the camera frame (extrapolated fingers caused false cycles when a hand slid out of view).

### Known limitations
- Good lighting and a fully visible hand are needed; typing with hands in view can occasionally register a squeeze.

## [0.1.0a1] - 2026-09-08

Developer preview. Install from source with `uv sync`; there is no signed
macOS package yet.

### Added
- Keyboard simulator producing tap events for thumb to pinky.
- Energy engine with a 10,000 cap, overflow accounting, atomic spending and
  per-session idempotency.
- PySide6 HUD with energy bar, per-finger counts, combo, rate, MCP status.
- Loopback MCP server (Streamable HTTP) with `get_energy`, `spend_energy`,
  `get_stats`, `get_session`; bearer token, Host/Origin checks, rate limit.
- CLI: `simulate`, `status`, `stats`, `setup claude`, `remove claude`, `doctor`.

### Known limitations
- No camera support yet; the balance resets when the app quits.
- Only Claude Code is tested as an MCP client.

[Unreleased]: https://github.com/yorulmazsinan/taprivo/compare/v0.1.0b3...HEAD
[0.1.0b3]: https://github.com/yorulmazsinan/taprivo/releases/tag/v0.1.0b3
[0.1.0b2]: https://github.com/yorulmazsinan/taprivo/releases/tag/v0.1.0b2
[0.1.0b1]: https://github.com/yorulmazsinan/taprivo/releases/tag/v0.1.0b1
[0.1.0a1]: https://github.com/yorulmazsinan/taprivo/releases/tag/v0.1.0a1
