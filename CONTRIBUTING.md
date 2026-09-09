# Contributing to Taprivo

Thanks for helping. You do not need a camera to contribute: the keyboard
simulator drives the full tap → energy → HUD → MCP path.

## Set up

```bash
uv sync --dev
uv run taprivo simulate
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Workflow

1. Start with the simulator and read `README.md`.
2. Pick a small issue. For larger changes, open an issue first to discuss the design.
3. Create a branch, change the behaviour, and run the related tests.
4. HUD changes: attach a screenshot. Detector or energy changes: add a
   synthetic fixture or a unit test that shows the before/after behaviour.
5. Detector or calibration changes: run the manual protocol in
   `tests/manual/camera-matrix.md` and paste the numbers into the PR.
6. In the PR, describe the user-visible effect and how you verified it.

## Ground rules

- Keep camera frames, landmarks, tokens and private paths out of logs, tests
  and issues. Raw camera recordings are never required for a contribution.
- `core/` stays free of Qt and MCP imports.
- `vision/` never imports Qt or `mcp/`; mediapipe is imported lazily inside
  `vision/tracker.py` only. Detector or calibration changes must keep the
  pinned fixtures in `tests/fixtures/landmarks/` (`*.csv` + `*.events.json`)
  deliberately regenerated and reviewed.
- `src/taprivo/ui/app.py` is the composition root and the only `ui/` module
  allowed to import from `mcp/`; `mcp/` never imports `ui/`.
- Public contract changes (CLI JSON, MCP schemas, config keys) need a note in
  `CHANGELOG.md` and, for anything non-additive, an issue labelled `rfc`.
- Contributions are accepted under the project's Apache-2.0 license. Tool-assisted
  contributions are welcome; the contributor is responsible for verifying them.

## Labels

`bug`, `enhancement`, `documentation`, `good first issue`, `help wanted`,
`needs-triage`, `area:vision`, `area:mcp`, `area:hud`, `area:cli`,
`platform:macos`, `phase:1`, `phase:2`.
