# Changelog

All notable changes to this project are documented here. The format follows
Keep a Changelog and the project uses Semantic Versioning.

## [Unreleased]

### Changed
- Pin mediapipe to 0.10.33; a test fails if the installed wheel contains telemetry code.

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

[Unreleased]: https://github.com/yorulmazsinan/taprivo/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/yorulmazsinan/taprivo/releases/tag/v0.1.0a1
