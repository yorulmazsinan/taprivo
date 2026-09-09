# Taprivo

**Power your code with motion.** ⚡

[Türkçe README](README.tr.md)

Taprivo turns hand movement into a playful work budget for AI coding agents.
Squeeze your hands in front of the camera (or tap keys in the simulator), build
Motion Energy, and let Claude Code read and spend it through MCP. Hand tracking
runs locally on your device; camera frames never leave it. Motion Energy is a
game mechanic, not API tokens or credits.

> **Status: beta (0.1.0b7).** Camera-based
> squeeze detection with calibration is available alongside the keyboard
> simulator. Tested on macOS on Apple Silicon with the built-in FaceTime
> camera.

## What it does

- Every tap adds 10 Motion Energy (× combo multiplier, up to 2×) to a session balance (cap 10,000); a camera squeeze counts as five taps (50 energy). Hold a steady beat and the energy per tap rises by another 25 %.
- A small always-on-top HUD (dark or light theme) leads with the energy number and its bar, then a hand map: two hand silhouettes whose finger tips light up as you drum, with the key caps under each finger. Combo and multiplier, rate and tempo, and camera and MCP status badges sit alongside.
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

The balance resets when Taprivo quits, but the session is not forgotten:
each session and its daily totals are written to a local SQLite file, so
`taprivo stats --today` and `taprivo stats --history` still work with the app
closed.

## Requirements

- macOS on Apple Silicon (other platforms are untested)
- Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://code.claude.com) for the agent integration

## Install

Pick the path that fits you; the app is the easiest.

### 1. macOS app (easiest, Apple Silicon)

1. Download `Taprivo-<version>.dmg` from the
   [latest release](https://github.com/yorulmazsinan/taprivo/releases/latest).
2. Open the DMG and drag **Taprivo** to **Applications**. The app is signed with a
   Developer ID and notarized by Apple, so it opens without extra steps.
3. Open Taprivo. The HUD appears; drum the number row (`1`–`4` left hand,
   `7`–`0` right hand) and watch the energy grow, or click **Open Camera** to
   count hand squeezes. macOS asks for camera permission the first time.
4. Click **Setup…** in the HUD to connect Claude Code or Cursor and to run the
   doctor; the same window shows the command-line tool bundled in the app.
   The same thing from a Terminal:

   ```bash
   /Applications/Taprivo.app/Contents/MacOS/Taprivo setup claude --install-instructions
   /Applications/Taprivo.app/Contents/MacOS/Taprivo doctor
   ```

   Use `setup cursor --install-instructions` for Cursor. Keep the app running
   while you work; the balance resets when it quits.

### 2. Command line (uv or pipx)

```bash
uv tool install taprivo            # or: pipx install taprivo
taprivo                            # opens the HUD
taprivo setup claude --install-instructions
taprivo doctor
```

Needs Python 3.12 or 3.13. The install takes about 1.5 GB because of mediapipe,
OpenCV and Qt. Upgrade with `uv tool upgrade taprivo`, remove with
`uv tool uninstall taprivo`.

### 3. From source (contributors)

Follow the Quickstart below. Contributors can also build the app bundle with
`packaging/macos/build.sh build`.

## Quickstart (from a clone)

```bash
git clone https://github.com/yorulmazsinan/taprivo.git
cd taprivo
uv sync
uv run taprivo simulate
```

The HUD opens with keyboard mode running and both hands on the number row.
The left hand drums `1` `2` `3` `4` (pinky to index) and the right hand
`7` `8` `9` `0` (index to pinky). Each press adds 10 energy; keep the taps
coming and the combo multiplier lifts that to 1.5× at 10 consecutive taps and
2× at 25. Keep an even beat between 60 and 240 BPM and the HUD highlights the
tempo and adds a further 1.25×. Taps above 12 per second are ignored, so a held
key earns nothing.

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

### Usage in the HUD

```bash
uv run taprivo setup claude --statusline
```

Claude Code runs a status line command and pipes a JSON description of the
session into it on every assistant message. `--statusline` writes
`~/.config/taprivo/statusline.sh` (mode 0700) and points Claude Code's
`statusLine` setting at it, after backing up `~/.claude/settings.json` and
keeping every other setting. In the app, the same switch is the **Show Claude
Code usage in the HUD (status line)** checkbox on the Claude Code row of the
Setup window, on by default.

The HUD then shows a **Claude Code** card with the model, how full the context
window is, the five-hour and seven-day usage limits with the time until each
one resets, and the session's cost and elapsed time. The two limits come from a
Pro or Max subscription; on an API key Claude Code does not report them and the
card says so. A card older than 90 seconds dims and says when it last heard
anything.

In the other direction, Claude Code's own status line gains your energy:
`⚡ 2795 · x17 1.5× · 5h 42%`. A status line you had configured before is not
lost — it is saved to `~/.config/taprivo/statusline-chain.json`, still runs, and
its output is printed before Taprivo's. `uv run taprivo remove claude` puts it
back and deletes both files.

### Cursor

```bash
uv run taprivo setup cursor --install-instructions
uv run taprivo doctor
```

Use `taprivo setup cursor --install-instructions` when Taprivo is installed
globally. `setup cursor` adds a `taprivo` entry to `~/.cursor/mcp.json`
(mode 0600, a backup is written first) and, with `--install-instructions`,
writes `~/.cursor/taprivo.md`. Cursor has no command for user rules, so paste
the contents of that file into Settings > Rules yourself.

With `--project` it also writes `.cursor/mcp.json` in the repository, where the
token comes from the `${env:TAPRIVO_TOKEN}` environment variable, and
`.cursor/rules/taprivo.mdc` with the same budget rules. `--dry-run` shows the
changes without writing, and `uv run taprivo remove cursor` removes the entry
and the files Taprivo added.

## Camera

Taprivo tracks up to two hands in front of the camera and counts a **squeeze**
each time a hand goes open → fist → open. Each squeeze is worth 50 Motion
Energy. Think of it as a short circulation exercise between coding bursts.

1. Start Taprivo and click **Open Camera** in the HUD (or run `uv run taprivo calibrate`).
2. Pick a device. Taprivo prefers the built-in camera; an iPhone Continuity
   Camera is listed but not opened until you select it (set
   `camera.prefer_builtin: false` to change the default).
3. Click **Start Camera**. macOS asks for camera permission the first time.
4. Click **Calibrate** and follow the prompts: show your hand, open it wide,
   make a fist, then squeeze five times. Apply the result for this session.

Closing the Camera window stops the camera; energy from squeezes stays in the HUD.

The Camera window draws the tracked hand over the live preview — 21 landmarks
with per-finger fingertip colours, and an expanding ring from the palm each time
a squeeze is counted — plus a strip with the frame rate, the detection ratio and
the device name. It also shows a Left/Right openness meter per hand with the
calibrated open and closed levels as tick marks and the current state beside it.
Set `hud.reduced_motion: true` for a short static ring instead of the expanding one. Camera frames are processed in
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
| `taprivo simulate` | Open the HUD with keyboard mode running |
| `taprivo camera list [--json]` | List camera devices |
| `taprivo calibrate` | Open the Camera window for calibration |
| `taprivo status [--json]` | Balance, tracking state, camera fps and endpoint of the running app |
| `taprivo stats [--json]` | Session statistics of the running app, plus today's totals |
| `taprivo stats --today [--json]` | Today's totals, read from the local statistics file |
| `taprivo stats --history [--days N] [--json]` | Daily totals, newest first (default 30 days) |
| `taprivo setup claude [--project] [--install-instructions] [--statusline] [--dry-run] [--json]` | Connect Claude Code |
| `taprivo remove claude [--project] [--json]` | Disconnect Claude Code |
| `taprivo setup cursor [--project] [--install-instructions] [--dry-run] [--json]` | Connect Cursor |
| `taprivo remove cursor [--project] [--json]` | Disconnect Cursor |
| `taprivo doctor [--json] [--camera-probe]` | Diagnose the local setup; lists camera devices (briefly opens each index, bar an iPhone Continuity Camera, to detect a signal); the probe additionally checks permission and measures fps |

Exit codes: 0 success, 1 operation failure, 2 invalid arguments or config.

## Configuration

User overrides live in `~/.config/taprivo/config.yaml` and merge over the
shipped defaults (`src/taprivo/resources/default.yaml`). Common keys:

```yaml
energy:
  energy_per_tap: 10
  max_energy: 10000
combo:
  energy_multiplier_enabled: true
  tiers:            # combo tiers: consecutive taps → energy multiplier
    - {at: 10, multiplier: 1.5}
    - {at: 25, multiplier: 2.0}
rhythm:
  enabled: true
  steady_multiplier: 1.25   # steady beat bonus, stacks with combo
server:
  port: 32145
simulator:
  max_taps_per_second: 12   # sliding one-second cap, both hands together
hud:
  always_on_top: true
  opacity: 0.92
  reduced_motion: false
  theme: system   # system | dark | light
camera:
  device_index: null   # null = choose in the Camera window
  prefer_builtin: true # false = pick the first camera with a signal instead
  width: 640
  height: 480
squeeze:
  open_level: 0.80     # calibration overrides both levels for the session
  closed_level: 0.45
stats:
  enabled: true        # session and daily totals; no reasons, no frames
  path: null           # null = ~/.config/taprivo/stats.sqlite
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
- Statistics are stored locally in `~/.config/taprivo/stats.sqlite` as counters
  and timestamps only — no spend reasons, no camera data; disable with
  `stats.enabled: false`.
- The status line JSON Claude Code pipes to Taprivo is read in memory and
  dropped; it is never logged and never written to the statistics file.
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
| Keyboard drumming (two hands, combo multiplier) | implemented |
| HUD | implemented |
| MCP tools and Claude Code setup | implemented |
| Camera squeeze detection (two hands) | implemented (beta) |
| Calibration | implemented (beta) |
| Rhythm / BPM | implemented (beta) |
| Persistent stats (SQLite) | implemented (beta) |
| Cursor | implemented (beta) |
| Claude Code usage card | implemented (beta) |
| Other agents (Codex, …) | planned |

See [ROADMAP.md](ROADMAP.md).

## Contributing

Start with the simulator; no camera is needed to contribute. Read
[CONTRIBUTING.md](CONTRIBUTING.md), pick a `good first issue`, and open a PR.

## License

Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
