# Taprivo

**Power your code with motion.** ⚡

[Türkçe README](README.tr.md)

Taprivo turns finger taps into a playful work budget for AI coding agents.
Tap your fingers, build Motion Energy, and let Claude Code read and spend it
through MCP. Hand tracking will run locally on your device; this release
ships the keyboard simulator. Camera frames stay on your device. Motion
Energy is a game mechanic, not API tokens or credits.

> **Status: alpha (0.1.0a1).** This release ships the camera-free keyboard
> simulator, the HUD and the MCP server. Camera-based tap detection is in
> development. Tested on macOS on Apple Silicon.

## What it does

- Every valid tap adds 10 Motion Energy to a session balance (cap 10,000).
- A small always-on-top HUD shows energy, per-finger counts, combo and rate.
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

## CLI

| Command | What it does |
|---|---|
| `taprivo` | Open the HUD |
| `taprivo --version` | Print the version |
| `taprivo simulate` | Open the HUD with the keyboard simulator running |
| `taprivo status [--json]` | Balance, tracking state and endpoint of the running app |
| `taprivo stats [--json]` | Session statistics |
| `taprivo setup claude [--project] [--install-instructions] [--dry-run]` | Connect Claude Code |
| `taprivo remove claude [--project]` | Disconnect Claude Code |
| `taprivo doctor [--json]` | Diagnose the local setup |

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
```

If port 32145 is taken, the HUD shows `MCP: Error`; set `server.port` and run
`taprivo setup claude` again. Taprivo never switches ports silently.

## Privacy and security

- The MCP server binds to loopback only, checks `Host` and `Origin`, and
  requires a random per-user bearer token stored at `~/.config/taprivo/token`
  (mode 0600). No CORS headers are sent.
- Camera frames (when camera support lands) are processed in memory and are
  never stored, uploaded or exposed over MCP.
- No telemetry. The only network listener is the local endpoint.
- Logs stay low-volume and never contain the token or spend reasons in full.

## Architecture

```
Keyboard simulator / (planned) camera
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
| Camera tap detection | in development |
| Two hands, rhythm, combo bonuses | planned |
| Persistent stats (SQLite) | planned |
| Other agents (Cursor, Codex, …) | planned |

See [ROADMAP.md](ROADMAP.md).

## Contributing

Start with the simulator; no camera is needed to contribute. Read
[CONTRIBUTING.md](CONTRIBUTING.md), pick a `good first issue`, and open a PR.

## License

Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
