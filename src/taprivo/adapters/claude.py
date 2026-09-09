"""Claude Code adapter: user-scope MCP registration and instruction files."""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

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

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def default_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


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
    def endpoint(self) -> str:
        return self._config.endpoint_url

    # -- detection -----------------------------------------------------------

    def detect(self) -> DetectResult:
        path = shutil.which(self._bin)
        if path is None:
            return DetectResult(False)
        proc = self._run([path, "--version"])
        version = proc.stdout.strip() if proc.returncode == 0 else None
        return DetectResult(True, version, path)

    def registered_url(self) -> str | None:
        proc = self._run([self._bin, "mcp", "get", SERVER_NAME])
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
        if self.registered_url() is not None:
            self._run([self._bin, "mcp", "remove", "--scope", "user", SERVER_NAME])
        proc = self._run(
            [
                self._bin,
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
        proc = self._run([self._bin, "mcp", "remove", "--scope", "user", SERVER_NAME])
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
                Check("claude_cli", "fail", "'claude' not found on PATH", "install Claude Code")
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
        return checks
