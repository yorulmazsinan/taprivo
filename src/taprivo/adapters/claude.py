"""Claude Code adapter: user-scope MCP registration and instruction files."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from taprivo import paths
from taprivo.adapters.base import (
    Check,
    DetectResult,
    SetupAction,
    SetupError,
    SetupOptions,
    SetupPlan,
    backup,
    instructions_text,
    merge_mcp_server,
    redact,
    remove_block,
    remove_mcp_server,
    unified_diff,
    upsert_block,
    write_if_changed,
)
from taprivo.config import Config

START_MARKER = "<!-- taprivo:start -->"
END_MARKER = "<!-- taprivo:end -->"
IMPORT_LINE = "@~/.claude/taprivo.md"
SERVER_NAME = "taprivo"
#: How often Claude Code re-runs the status line command, in seconds.
STATUSLINE_REFRESH_SECONDS = 30

#: POSIX sh, no bashisms: Claude Code runs the command through /bin/sh. It reads
#: the session JSON from stdin, hands it to the local Taprivo server for the
#: energy segment, replays it to whatever status line was configured before, and
#: prints "previous · ours". The token is read from its file straight into the
#: header and is never printed.
STATUSLINE_SCRIPT = r"""#!/bin/sh
# Taprivo status line for Claude Code.
# Written by 'taprivo setup claude --statusline'; edits are overwritten.
# Remove it with 'taprivo remove claude'.

json=$(cat)

# -f: an HTTP error prints nothing, so a rate limit or a stopped app
# quietly leaves the status line to whatever came before.
# The bearer header goes through a private temp file (curl -H @file) so the
# token never appears in the process arguments visible to 'ps'.
umask 077
hdr=$(mktemp "${{TMPDIR:-/tmp}}/taprivo-hdr.XXXXXX" 2>/dev/null) || exit 0
trap 'rm -f "$hdr"' EXIT
printf 'Authorization: Bearer %s\n' "$(cat {token})" > "$hdr"
ours=$(printf '%s' "$json" | curl -sf -m 1 \
  -H "@$hdr" \
  -H 'Content-Type: application/json' \
  --data-binary @- {endpoint} 2>/dev/null)

prev=''
chain={chain}
if [ -f "$chain" ]; then
  # Pull the one string we need out of a small JSON file, without assuming jq.
  previous=$(sed -nE 's/.*"command"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)".*/\1/p' \
    "$chain" | head -n 1)
  previous=$(printf '%s' "$previous" | sed -e 's/\\"/"/g' -e 's/\\\\/\\/g')
  if [ -n "$previous" ]; then
    prev=$(printf '%s' "$json" | sh -c "$previous" 2>/dev/null)
  fi
fi

if [ -n "$prev" ] && [ -n "$ours" ]; then
  printf '%s · %s\n' "$prev" "$ours"
elif [ -n "$prev" ]; then
  printf '%s\n' "$prev"
elif [ -n "$ours" ]; then
  printf '%s\n' "$ours"
fi
"""

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def sh_quote(value: str | Path) -> str:
    """Single-quote a value for POSIX sh, so a space or a quote cannot break out."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def statusline_script(endpoint: str) -> str:
    """The status line script Taprivo installs, bound to this install's paths."""
    return STATUSLINE_SCRIPT.format(
        token=sh_quote(paths.token_path()),
        endpoint=sh_quote(endpoint),
        chain=sh_quote(paths.statusline_chain_path()),
    )


# Where a `claude` install ends up when the launching process has no login
# shell PATH -- which is exactly the case for a double-clicked .app bundle.
EXTRA_DIRS = (
    ".claude/local/bin",
    ".local/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    ".npm-global/bin",
    ".volta/bin",
)
NVM_GLOB = ".nvm/versions/node"


def _node_version_key(path: Path) -> tuple[int, ...]:
    """Sort nvm's 'v20.11.0' style directories numerically, so v20 beats v9."""
    digits = re.findall(r"\d+", path.parent.name)
    return tuple(int(part) for part in digits)


def candidate_dirs(home: Path | None = None) -> list[Path]:
    """Directories to search for `claude` when it is not on PATH, in the order
    a person's shell would most likely find it. Newest node version first."""
    base = home or Path.home()
    dirs = [Path(entry) if entry.startswith("/") else base / entry for entry in EXTRA_DIRS]
    versions = sorted((base / NVM_GLOB).glob("*/bin"), key=_node_version_key, reverse=True)
    return dirs + versions


def search_path(home: Path | None = None) -> str:
    """PATH for a `claude` subprocess: the inherited one plus the candidates,
    appended so a person's own PATH still decides which install wins."""
    inherited = os.environ.get("PATH", "")
    extra = [str(path) for path in candidate_dirs(home) if path.is_dir()]
    return os.pathsep.join([inherited, *extra]) if inherited else os.pathsep.join(extra)


def resolve_bin(name: str, home: Path | None = None) -> str | None:
    """The absolute path to `name`, searching PATH first and the candidate
    directories after it. An explicit path is taken as given."""
    found = shutil.which(name)
    if found is not None:
        return found
    if os.sep in name:
        return None
    for directory in candidate_dirs(home):
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def default_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = search_path()
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False, env=env)
    except OSError as exc:  # a missing binary must read as a failed call, not a crash
        return subprocess.CompletedProcess(args, 127, "", str(exc))


class ClaudeAdapter:
    name = "claude"

    def __init__(
        self,
        config: Config,
        *,
        home: Path | None = None,
        runner: Runner = default_runner,
        claude_bin: str = "claude",
    ) -> None:
        self._config = config
        self._home = home or Path.home()
        self._run = runner
        self._bin = claude_bin

    # -- locations -----------------------------------------------------------

    @property
    def claude_dir(self) -> Path:
        return self._home / ".claude"

    @property
    def instructions_path(self) -> Path:
        return self.claude_dir / "taprivo.md"

    @property
    def claude_md_path(self) -> Path:
        return self.claude_dir / "CLAUDE.md"

    @property
    def settings_path(self) -> Path:
        return self.claude_dir / "settings.json"

    @property
    def statusline_script_path(self) -> Path:
        return paths.statusline_script_path()

    @property
    def statusline_chain_path(self) -> Path:
        return paths.statusline_chain_path()

    @property
    def endpoint(self) -> str:
        return self._config.endpoint_url

    @property
    def statusline_endpoint(self) -> str:
        server = self._config.server
        return f"http://{server.host}:{server.port}/statusline"

    # -- detection -----------------------------------------------------------

    def binary(self) -> str | None:
        """The resolved `claude` executable, or None when it is nowhere to be
        found. Resolved on every call so installing Claude Code while the
        Setup window is open is picked up without a restart."""
        return resolve_bin(self._bin, self._home)

    def detect(self) -> DetectResult:
        path = self.binary()
        if path is None:
            return DetectResult(False)
        proc = self._run([path, "--version"])
        version = proc.stdout.strip() if proc.returncode == 0 else None
        return DetectResult(True, version, path)

    def registered_url(self) -> str | None:
        proc = self._run([self.binary() or self._bin, "mcp", "get", SERVER_NAME])
        if proc.returncode != 0:
            return None
        match = re.search(r"https?://\S+", proc.stdout)
        return match.group(0).rstrip(",;)") if match else ""

    # -- setup ---------------------------------------------------------------

    def plan_setup(self, options: SetupOptions) -> SetupPlan:
        plan = SetupPlan()
        plan.actions.append(
            SetupAction(
                "ensure_token",
                f"Ensure local token file exists at {paths.token_path()} (mode 0600)",
                self._ensure_token,
            )
        )
        if self.registered_url() == self.endpoint:
            plan.notes.append(
                "Claude Code user-scope MCP server 'taprivo' already points to the endpoint."
            )
        else:
            plan.actions.append(
                SetupAction(
                    "register_mcp",
                    "Register HTTP MCP server 'taprivo' in Claude Code (user scope) at "
                    f"{self.endpoint}",
                    self._register,
                )
            )
        plan.actions.append(
            SetupAction(
                "write_file",
                f"Write agent instructions to {self.instructions_path}",
                self._write_instructions,
            )
        )
        if options.install_instructions:
            plan.actions.append(
                SetupAction(
                    "patch_marked_block",
                    f"Add '{IMPORT_LINE}' inside a marked block in {self.claude_md_path} "
                    "(backup first)",
                    self._install_import,
                )
            )
        else:
            plan.notes.append(
                "Instruction import not installed; re-run with --install-instructions to add it to "
                f"{self.claude_md_path}."
            )
        if options.statusline:
            plan.actions.append(
                SetupAction(
                    "write_file",
                    f"Write the status line script to {self.statusline_script_path} (mode 0700)",
                    self._write_statusline,
                )
            )
            plan.actions.append(
                SetupAction(
                    "merge_json",
                    f"Point 'statusLine' at that script in {self.settings_path} (backup first)",
                    self._install_statusline,
                )
            )
        else:
            plan.notes.append(
                "Claude Code usage card not installed; re-run with --statusline to show the "
                "model, context and rate limits in the HUD."
            )
        if options.project:
            target = options.project_dir / ".mcp.json"
            plan.actions.append(
                SetupAction(
                    "merge_json",
                    f"Merge server 'taprivo' into {target}",
                    lambda: self._merge_project(target),
                )
            )
        return plan

    def _ensure_token(self) -> str:
        paths.read_or_create_token()
        return f"token ready at {paths.token_path()}"

    def _register(self) -> str:
        token = paths.read_or_create_token()
        claude = self.binary() or self._bin
        if self.registered_url() is not None:
            self._run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME])
        proc = self._run(
            [
                claude,
                "mcp",
                "add",
                "--transport",
                "http",
                "--scope",
                "user",
                SERVER_NAME,
                self.endpoint,
                "--header",
                f"Authorization: Bearer {token}",
            ]
        )
        if proc.returncode != 0:
            raise SetupError(
                "claude mcp add failed: "
                + redact(proc.stderr.strip() or proc.stdout.strip(), token)
            )
        return "registered 'taprivo' in Claude Code (user scope)"

    def _write_instructions(self) -> str:
        changed = write_if_changed(self.instructions_path, instructions_text())
        return "instructions written" if changed else "instructions already up to date"

    def _install_import(self) -> str:
        path = self.claude_md_path
        before = path.read_text("utf-8") if path.exists() else ""
        after = upsert_block(before, IMPORT_LINE, START_MARKER, END_MARKER)
        if after == before:
            return "instruction import already installed"
        if path.exists():
            saved = backup(path)
            note = f"backup saved to {saved}\n"
        else:
            note = ""
        write_if_changed(path, after)
        return note + "instruction import installed\n" + unified_diff(before, after, str(path))

    def _merge_project(self, target: Path) -> str:
        try:
            existing = json.loads(target.read_text("utf-8")) if target.exists() else {}
        except json.JSONDecodeError as exc:
            raise SetupError(f"{target} is not valid JSON: {exc}") from exc
        entry = {
            "type": "http",
            "url": self.endpoint,
            "headers": {"Authorization": "Bearer ${TAPRIVO_TOKEN}"},
        }
        merged = merge_mcp_server(existing, SERVER_NAME, entry)
        if merged == existing:
            return f"{target} already configured"
        write_if_changed(target, json.dumps(merged, indent=2) + "\n")
        return (
            f"updated {target}\n"
            "Export the token in the shell that starts Claude Code for this project:\n"
            f'  export TAPRIVO_TOKEN="$(cat {paths.token_path()})"\n'
            "Never commit the literal token."
        )

    # -- status line ---------------------------------------------------------

    def _read_settings(self) -> dict[str, Any]:
        path = self.settings_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            raise SetupError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SetupError(f"{path} must contain a JSON object")
        return data

    def _write_settings(self, data: dict[str, Any]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def write_statusline_script(self) -> Path:
        """Write the status line script and make it executable by its owner only."""
        path = self.statusline_script_path
        paths.ensure_home()
        path.write_text(statusline_script(self.statusline_endpoint), encoding="utf-8")
        os.chmod(path, 0o700)
        return path

    def _write_statusline(self) -> str:
        paths.read_or_create_token()
        return f"status line script written to {self.write_statusline_script()}"

    def _ours(self, entry: object) -> bool:
        """True when a `statusLine` setting already runs Taprivo's own script."""
        return (
            isinstance(entry, dict)
            and isinstance(entry.get("command"), str)
            and str(self.statusline_script_path) in entry["command"]
        )

    def _statusline_installed(self) -> bool:
        """Whether the settings currently run our script. A settings file we
        cannot read counts as "not ours": planning must never raise."""
        try:
            return self._ours(self._read_settings().get("statusLine"))
        except SetupError:
            return False

    def _install_statusline(self) -> str:
        settings = self._read_settings()
        existing = settings.get("statusLine")
        notes = []
        if isinstance(existing, dict) and existing.get("command") and not self._ours(existing):
            # Keep someone else's status line alive: the script chains it.
            chain = {key: existing[key] for key in ("command", "type") if key in existing}
            if "refreshInterval" in existing:
                chain["refreshInterval"] = existing["refreshInterval"]
            paths.ensure_home()
            self.statusline_chain_path.write_text(
                json.dumps(chain, indent=2) + "\n", encoding="utf-8"
            )
            notes.append(f"previous status line saved to {self.statusline_chain_path}")
        merged = dict(settings)
        merged["statusLine"] = {
            "type": "command",
            "command": str(self.statusline_script_path),
            "refreshInterval": STATUSLINE_REFRESH_SECONDS,
        }
        if merged == settings:
            return "status line already configured"
        if self.settings_path.exists():
            notes.insert(0, f"backup saved to {backup(self.settings_path)}")
        self._write_settings(merged)
        notes.append(f"status line configured in {self.settings_path}")
        return "\n".join(notes)

    def _remove_statusline(self) -> str:
        """Put back whatever status line was there before, then forget ours."""
        settings = self._read_settings()
        if not self._ours(settings.get("statusLine")):
            return "status line was not Taprivo's; left untouched"
        restored: dict[str, Any] | None = None
        if self.statusline_chain_path.exists():
            try:
                chained = json.loads(self.statusline_chain_path.read_text("utf-8"))
            except json.JSONDecodeError:
                chained = None
            if isinstance(chained, dict) and chained.get("command"):
                restored = chained
        merged = dict(settings)
        if restored is not None:
            merged["statusLine"] = restored
        else:
            merged.pop("statusLine", None)
        backup(self.settings_path)
        self._write_settings(merged)
        return (
            "previous status line restored"
            if restored is not None
            else f"'statusLine' removed from {self.settings_path}"
        )

    def _delete_statusline_files(self) -> str:
        self.statusline_script_path.unlink(missing_ok=True)
        self.statusline_chain_path.unlink(missing_ok=True)
        return "status line script deleted"

    # -- removal -------------------------------------------------------------

    def plan_remove(self, options: SetupOptions) -> SetupPlan:
        plan = SetupPlan()
        if self.registered_url() is not None:
            plan.actions.append(
                SetupAction(
                    "unregister_mcp",
                    "Remove 'taprivo' from Claude Code (user scope)",
                    self._unregister,
                )
            )
        if self.claude_md_path.exists() and START_MARKER in self.claude_md_path.read_text("utf-8"):
            plan.actions.append(
                SetupAction(
                    "remove_marked_block",
                    f"Remove Taprivo block from {self.claude_md_path}",
                    self._remove_import,
                )
            )
        if self.instructions_path.exists():
            plan.actions.append(
                SetupAction(
                    "delete_file", f"Delete {self.instructions_path}", self._delete_instructions
                )
            )
        if self._statusline_installed():
            plan.actions.append(
                SetupAction(
                    "edit_json",
                    f"Restore the previous 'statusLine' in {self.settings_path} (backup first)",
                    self._remove_statusline,
                )
            )
        if self.statusline_script_path.exists() or self.statusline_chain_path.exists():
            plan.actions.append(
                SetupAction(
                    "delete_file",
                    f"Delete {self.statusline_script_path} and {self.statusline_chain_path}",
                    self._delete_statusline_files,
                )
            )
        if options.project:
            target = options.project_dir / ".mcp.json"
            if target.exists():
                plan.actions.append(
                    SetupAction(
                        "edit_json",
                        f"Remove server 'taprivo' from {target}",
                        lambda: self._remove_project(target),
                    )
                )
        if not plan.actions:
            plan.notes.append("Nothing to remove.")
        return plan

    def _unregister(self) -> str:
        proc = self._run(
            [self.binary() or self._bin, "mcp", "remove", "--scope", "user", SERVER_NAME]
        )
        if proc.returncode != 0:
            raise SetupError(
                "claude mcp remove failed: " + (proc.stderr.strip() or proc.stdout.strip())
            )
        return "removed from Claude Code"

    def _remove_import(self) -> str:
        path = self.claude_md_path
        before = path.read_text("utf-8")
        after = remove_block(before, START_MARKER, END_MARKER)
        if after != before:
            backup(path)
            path.write_text(after, encoding="utf-8")
        return "instruction import removed"

    def _delete_instructions(self) -> str:
        self.instructions_path.unlink(missing_ok=True)
        return "instructions file deleted"

    def _remove_project(self, target: Path) -> str:
        try:
            existing = json.loads(target.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            raise SetupError(f"{target} is not valid JSON: {exc}") from exc
        updated = remove_mcp_server(existing, SERVER_NAME)
        target.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        return f"removed 'taprivo' from {target}"

    # -- verification --------------------------------------------------------

    def verify(self) -> list[Check]:
        checks: list[Check] = []
        token_file = paths.token_path()
        if not token_file.exists():
            checks.append(
                Check(
                    "token",
                    "fail",
                    "no token file",
                    "run 'taprivo simulate' once or 'taprivo setup claude'",
                )
            )
        elif stat.S_IMODE(token_file.stat().st_mode) != 0o600:
            checks.append(
                Check(
                    "token",
                    "fail",
                    "token file permissions are not 0600",
                    f"chmod 600 {token_file}",
                )
            )
        else:
            checks.append(Check("token", "ok", "token file present with mode 0600"))

        detected = self.detect()
        if detected.found:
            checks.append(Check("claude_cli", "ok", f"claude {detected.version or 'found'}"))
        else:
            checks.append(
                Check(
                    "claude_cli",
                    "fail",
                    "'claude' not found on PATH or in the usual install locations",
                    "install Claude Code",
                )
            )

        url = self.registered_url() if detected.found else None
        if url == self.endpoint:
            checks.append(Check("claude_registration", "ok", f"user-scope server points to {url}"))
        elif url:
            checks.append(
                Check(
                    "claude_registration",
                    "fail",
                    f"registered URL is {url}",
                    "run 'taprivo setup claude'",
                )
            )
        else:
            checks.append(
                Check(
                    "claude_registration",
                    "fail",
                    "no 'taprivo' MCP server registered",
                    "run 'taprivo setup claude'",
                )
            )

        if self.instructions_path.exists():
            checks.append(Check("instructions_file", "ok", f"{self.instructions_path} present"))
        else:
            checks.append(
                Check(
                    "instructions_file",
                    "fail",
                    "instruction file missing",
                    "run 'taprivo setup claude'",
                )
            )

        claude_md = self.claude_md_path
        if claude_md.exists() and IMPORT_LINE in claude_md.read_text("utf-8"):
            checks.append(
                Check(
                    "instructions_import",
                    "ok",
                    "import block present (file present; agent behaviour is not verified)",
                )
            )
        else:
            checks.append(
                Check(
                    "instructions_import",
                    "warn",
                    "import block not installed",
                    "run 'taprivo setup claude --install-instructions'",
                )
            )

        if self._statusline_installed() and self.statusline_script_path.exists():
            checks.append(
                Check("claude_statusline", "ok", f"status line runs {self.statusline_script_path}")
            )
        else:
            checks.append(
                Check(
                    "claude_statusline",
                    "warn",
                    "status line not installed",
                    "run 'taprivo setup claude --statusline'",
                )
            )
        return checks
