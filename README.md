# Taprivo

**Power your code with motion.** ⚡

[Türkçe README](README.tr.md)

Taprivo turns hand movement into a playful work budget for AI coding agents.
Squeeze your hands in front of the camera (or tap keys in the simulator), build
Motion Energy, and let Claude Code read and spend it through MCP. Hand tracking
runs locally on your device; camera frames never leave it. Motion Energy is a
game mechanic, not API tokens or credits.

> **Status: beta (0.1.0b1).** Camera-based
> squeeze detection with calibration is available alongside the keyboard
> simulator. Tested on macOS on Apple Silicon with the built-in FaceTime
> camera.

## What it does

- Every valid tap adds 10 Motion Energy to a session balance (cap 10,000); a camera squeeze counts as five taps (50 energy).
- A small always-on-top HUD (dark or light theme) shows energy, per-finger counts, combo, rate, camera and MCP status.
- A local MCP server on `http://127.0.0.1:32145/mcp` exposes `get_energy`,
  `spend_energy`, `get_stats` and `get_session`.
- Claude Code reads the balance and spends a suitable amount before a
  substantial implementation burst, following an instruction file you opt into.
- Every Claude Code project on your machine shares one balance.

## How Motion Energy works

```
available = generated - overflow - spent
```

`generated` counts every tap, `overflow` is energy lost while the balance is
full, and `spent` is what agents charged. Spending is atomic: two clients can
never spend the same energy, and retrying a spend with the same request id
returns the original result instead of charging twice.

Energy is **not** a token balance, API credit or permission. Your existing
task and agent permissions stay exactly as they are; running out of energy
never blocks you, and the budget can be disabled at any time.

The balance resets when Taprivo quits. Persistent history is planned for a
later release.

## Requirements

- macOS on Apple Silicon (other platforms are untested)
- Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://code.claude.com) for the agent integration

## Quickstart (simulator, no camera)

```bash
git clone https://github.com/yorulmazsinan/taprivo.git
cd taprivo
uv sync
uv run taprivo simulate
```

The HUD opens with the simulator running. Press `1`–`5` to tap thumb, index,
middle, ring and pinky. Each press adds 10 energy.

With a camera: click **Open Camera** in the HUD or run `uv run taprivo calibrate` — see [Camera](#camera).

## Connect Claude Code

```bash
uv run taprivo setup claude --install-instructions
uv run taprivo doctor
```

`setup claude` registers a user-scope HTTP MCP server named `taprivo` using
the official `claude mcp add` command, writes `~/.claude/taprivo.md`, and, with
`--install-instructions`, imports that file from `~/.claude/CLAUDE.md` inside a
clearly marked block (a backup is written first). Run with `--dry-run` to see
the changes without writing anything. `doctor` checks the endpoint, token,
registration and instruction files.

To share the configuration inside a repository, run `uv run taprivo setup
claude --project`. That writes a `.mcp.json` entry whose token comes from the
`TAPRIVO_TOKEN` environment variable, so no secret is committed.

`uv run taprivo remove claude` undoes the registration and removes only the
files and blocks Taprivo added.

## Camera

Taprivo tracks up to two hands in front of the camera and counts a **squeeze**
each time a hand goes open → fist → open. Each squeeze is worth 50 Motion
Energy. Think of it as a short circulation exercise between coding bursts.

1. Start Taprivo and click **Open Camera** in the HUD (or run `uv run taprivo calibrate`).
2. Pick a device. Devices that deliver black frames (for example an idle iPhone
   Continuity Camera) are marked *no signal*.
3. Click **Start Camera**. macOS asks for camera permission the first time.
4. Click **Calibrate** and follow the prompts: show your hand, open it wide,
   make a fist, then squeeze five times. Apply the result for this session.

Closing the Camera window stops the camera; energy from squeezes stays in the HUD.

The Camera window shows a Left/Right openness meter per hand with the
calibrated open and closed levels as tick marks. Camera frames are processed in
memory and shown only in the Camera window; they are never stored, logged or
exposed through MCP. Calibration data can be exported as a CSV of per-hand
features (no images, no raw landmarks) for tuning.

Known limitations: good lighting and the whole hand in frame are needed; very
quick squeezes are ignored; typing with your hands in view can occasionally be
counted. Per-finger tapping is not detected by the camera — use the keyboard
simulator for that. `taprivo doctor --camera-probe` opens the camera to check permission and report processed fps.
A MacBook with its lid closed keeps the built-in camera dark; open the lid or pick another camera.
Keep the whole hand inside the frame; a hand partly out of view is ignored.

## CLI

| Command | What it does |
|---|---|
| `taprivo` | Open the HUD |
| `taprivo --version` | Print the version |
| `taprivo simulate` | Open the HUD with the keyboard simulator running |
| `taprivo camera list [--json]` | List camera devices |
| `taprivo calibrate` | Open the Camera window for calibration |
| `taprivo status [--json]` | Balance, tracking state, camera fps and endpoint of the running app |
| `taprivo stats [--json]` | Session statistics |
| `taprivo setup claude [--project] [--install-instructions] [--dry-run]` | Connect Claude Code |
| `taprivo remove claude [--project]` | Disconnect Claude Code |
| `taprivo doctor [--json] [--camera-probe]` | Diagnose the local setup; lists camera devices (briefly opens each index to detect a signal); the probe additionally checks permission and measures fps |

Exit codes: 0 success, 1 operation failure, 2 invalid arguments or config.

## Configuration

User overrides live in `~/.config/taprivo/config.yaml` and merge over the
shipped defaults (`src/taprivo/resources/default.yaml`). Common keys:

```yaml
energy:
  energy_per_tap: 10
  max_energy: 10000
server:
  port: 32145
hud:
  always_on_top: true
  opacity: 0.92
  reduced_motion: false
  theme: system   # system | dark | light
camera:
  device_index: null   # null = choose in the Camera window
  width: 640
  height: 480
squeeze:
  open_level: 0.80     # calibration overrides both levels for the session
  closed_level: 0.45
```

If port 32145 is taken, the HUD shows `MCP: Error`; set `server.port` and run
`taprivo setup claude` again. Taprivo never switches ports silently.

## Privacy and security

- The MCP server binds to loopback only, checks `Host` and `Origin`, and
  requires a random per-user bearer token stored at `~/.config/taprivo/token`
  (mode 0600). No CORS headers are sent.
- Camera frames are processed in memory and are never stored, uploaded or
  exposed over MCP. The hand-tracking model runs on-device; mediapipe is
  pinned to a release without usage logging.
- No telemetry. The only network listener is the local endpoint.
- Logs stay low-volume and never contain the token or spend reasons in full.

## Architecture

```
Camera (MediaPipe hand landmarks, squeeze detector) / keyboard simulator
  → TapEvent
  → EnergyEngine (single lock, atomic spend, idempotency)
      ├─ HUD (PySide6)
      └─ MCP server (Streamable HTTP, loopback, bearer token)
           ├─ Claude Code project A
           └─ Claude Code project B
```

## Support matrix

| Area | Status |
|---|---|
| Keyboard simulator | implemented |
| HUD | implemented |
| MCP tools and Claude Code setup | implemented |
| Camera squeeze detection (two hands) | implemented (beta) |
| Calibration | implemented (beta) |
| Rhythm, combo bonuses | planned |
| Persistent stats (SQLite) | planned |
| Other agents (Cursor, Codex, …) | planned |

See [ROADMAP.md](ROADMAP.md).

## Contributing

Start with the simulator; no camera is needed to contribute. Read
[CONTRIBUTING.md](CONTRIBUTING.md), pick a `good first issue`, and open a PR.

## License

Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
