"""Cursor adapter: MCP registration in ~/.cursor and project rules."""

from __future__ import annotations

import json
import os
import shutil
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
    remove_mcp_server,
    unified_diff,
)
from taprivo.config import Config

SERVER_NAME = "taprivo"
RULE_FRONTMATTER = "---\ndescription: Taprivo Motion Energy budget\nalwaysApply: true\n---\n"
USER_RULES_HINT = (
    "Cursor keeps user rules in Settings > Rules; paste the contents of ~/.cursor/taprivo.md there."
)

TokenProvider = Callable[[], str]


def rule_text() -> str:
    return RULE_FRONTMATTER + instructions_text()


class CursorAdapter:
    name = "cursor"

    def __init__(
        self,
        config: Config,
        *,
        home: Path | None = None,
        token_provider: TokenProvider = paths.read_or_create_token,
        cursor_bin: str = "cursor",
    ) -> None:
        self._config = config
        self._home = home or Path.home()
        self._token = token_provider
        self._bin = cursor_bin

    # -- locations -----------------------------------------------------------

    @property
    def cursor_dir(self) -> Path:
        return self._home / ".cursor"

    @property
    def user_config_path(self) -> Path:
        return self.cursor_dir / "mcp.json"

    @property
    def instructions_path(self) -> Path:
        return self.cursor_dir / "taprivo.md"

    def project_config_path(self, project_dir: Path) -> Path:
        return project_dir / ".cursor" / "mcp.json"

    def project_rule_path(self, project_dir: Path) -> Path:
        return project_dir / ".cursor" / "rules" / "taprivo.mdc"

    @property
    def endpoint(self) -> str:
        return self._config.endpoint_url

    # -- detection -----------------------------------------------------------

    def detect(self) -> DetectResult:
        if self.cursor_dir.exists():
            return DetectResult(True, None, str(self.cursor_dir))
        path = shutil.which(self._bin)
        if path is None:
            return DetectResult(False)
        return DetectResult(True, None, path)

    def registered_url(self) -> str | None:
        """The URL of the 'taprivo' entry in the user mcp.json, or None when the
        file or the entry is missing. Raises SetupError for unreadable JSON."""
        data = self._read_json(self.user_config_path)
        if data is None:
            return None
        servers = data.get("mcpServers")
        entry = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
        if not isinstance(entry, dict):
            return None
        url = entry.get("url")
        return url if isinstance(url, str) else ""

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            raise SetupError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SetupError(f"{path} is not a JSON object")
        return data

    # -- setup ---------------------------------------------------------------

    def plan_setup(self, options: SetupOptions) -> SetupPlan:
        plan = SetupPlan()
        if not self.detect().found:
            plan.notes.append(f"Cursor was not detected; {self.cursor_dir} will be created for it.")
        plan.actions.append(
            SetupAction(
                "ensure_token",
                f"Ensure local token file exists at {paths.token_path()} (mode 0600)",
                self._ensure_token,
            )
        )
        plan.actions.append(
            SetupAction(
                "merge_json",
                f"Merge server 'taprivo' into {self.user_config_path} at {self.endpoint} "
                "(mode 0600, backup first)",
                self._merge_user,
            )
        )
        if options.install_instructions:
            plan.actions.append(
                SetupAction(
                    "write_file",
                    f"Write agent instructions to {self.instructions_path}",
                    self._write_instructions,
                )
            )
            plan.notes.append(USER_RULES_HINT)
        else:
            plan.notes.append(
                "Instruction file not written; re-run with --install-instructions to create "
                f"{self.instructions_path}."
            )
        if options.project:
            target = self.project_config_path(options.project_dir)
            plan.actions.append(
                SetupAction(
                    "merge_json",
                    f"Merge server 'taprivo' into {target}",
                    lambda: self._merge_project(target),
                )
            )
            if options.install_instructions:
                rule = self.project_rule_path(options.project_dir)
                plan.actions.append(
                    SetupAction(
                        "write_file",
                        f"Write project rule to {rule}",
                        lambda: self._write_rule(rule),
                    )
                )
        return plan

    def _ensure_token(self) -> str:
        self._token()
        return f"token ready at {paths.token_path()}"

    def _write_private(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)

    def _merge_user(self) -> str:
        token = self._token()
        target = self.user_config_path
        existing = self._read_json(target) or {}
        entry = {"url": self.endpoint, "headers": {"Authorization": f"Bearer {token}"}}
        merged = merge_mcp_server(existing, SERVER_NAME, entry)
        if merged == existing:
            os.chmod(target, 0o600)
            return f"{target} already configured"
        before = target.read_text("utf-8") if target.exists() else ""
        after = json.dumps(merged, indent=2) + "\n"
        note = f"backup saved to {backup(target)}\n" if target.exists() else ""
        self._write_private(target, after)
        diff = unified_diff(before, after, str(target))
        return redact(f"{note}updated {target} (mode 0600)\n{diff}", token)

    def _write_instructions(self) -> str:
        self._write_private(self.instructions_path, instructions_text())
        return f"instructions written to {self.instructions_path}\n{USER_RULES_HINT}"

    def _write_rule(self, target: Path) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rule_text(), encoding="utf-8")
        return f"project rule written to {target}"

    def _merge_project(self, target: Path) -> str:
        existing = self._read_json(target) or {}
        entry = {
            "url": self.endpoint,
            "headers": {"Authorization": "Bearer ${env:TAPRIVO_TOKEN}"},
        }
        merged = merge_mcp_server(existing, SERVER_NAME, entry)
        if merged == existing:
            return f"{target} already configured"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        return (
            f"updated {target}\n"
            "Export the token in the shell that starts Cursor for this project:\n"
            f'  export TAPRIVO_TOKEN="$(cat {paths.token_path()})"\n'
            "Never commit the literal token."
        )

    # -- removal -------------------------------------------------------------

    def plan_remove(self, options: SetupOptions) -> SetupPlan:
        plan = SetupPlan()
        if self._has_server(self.user_config_path):
            plan.actions.append(
                SetupAction(
                    "edit_json",
                    f"Remove server 'taprivo' from {self.user_config_path} (backup first)",
                    self._remove_user,
                )
            )
        if self.instructions_path.exists():
            plan.actions.append(
                SetupAction(
                    "delete_file",
                    f"Delete {self.instructions_path}",
                    self._delete_instructions,
                )
            )
        if options.project:
            target = self.project_config_path(options.project_dir)
            if target.exists():
                plan.actions.append(
                    SetupAction(
                        "edit_json",
                        f"Remove server 'taprivo' from {target}",
                        lambda: self._remove_project(target),
                    )
                )
            rule = self.project_rule_path(options.project_dir)
            if rule.exists():
                plan.actions.append(
                    SetupAction("delete_file", f"Delete {rule}", lambda: self._delete_rule(rule))
                )
        if not plan.actions:
            plan.notes.append("Nothing to remove.")
        return plan

    def _has_server(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = self._read_json(path)
        except SetupError:
            return False
        servers = data.get("mcpServers") if data else None
        return isinstance(servers, dict) and SERVER_NAME in servers

    def _remove_user(self) -> str:
        target = self.user_config_path
        existing = self._read_json(target) or {}
        updated = remove_mcp_server(existing, SERVER_NAME)
        backup(target)
        self._write_private(target, json.dumps(updated, indent=2) + "\n")
        return f"removed 'taprivo' from {target}"

    def _delete_instructions(self) -> str:
        self.instructions_path.unlink(missing_ok=True)
        return f"deleted {self.instructions_path}"

    def _delete_rule(self, target: Path) -> str:
        target.unlink(missing_ok=True)
        return f"deleted {target}"

    def _remove_project(self, target: Path) -> str:
        existing = self._read_json(target) or {}
        updated = remove_mcp_server(existing, SERVER_NAME)
        target.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        return f"removed 'taprivo' from {target}"

    # -- verification --------------------------------------------------------

    def verify(self) -> list[Check]:
        checks: list[Check] = []
        detected = self.detect()
        try:
            url = self.registered_url()
        except SetupError as exc:
            checks.append(
                Check(
                    "cursor_registration",
                    "fail",
                    str(exc),
                    f"fix or delete {self.user_config_path}, then run 'taprivo setup cursor'",
                )
            )
            return checks
        if url == self.endpoint:
            checks.append(Check("cursor_registration", "ok", f"user mcp.json points to {url}"))
        elif url:
            checks.append(
                Check(
                    "cursor_registration",
                    "fail",
                    f"registered URL is {url}",
                    "run 'taprivo setup cursor'",
                )
            )
        else:
            checks.append(
                Check(
                    "cursor_registration",
                    "warn",
                    "Cursor not registered",
                    "run 'taprivo setup cursor'",
                )
            )
        if not detected.found:
            return checks
        path = self.instructions_path
        if path.exists() and path.read_text("utf-8") == instructions_text():
            checks.append(Check("cursor_instructions", "ok", f"{path} present"))
        else:
            checks.append(
                Check(
                    "cursor_instructions",
                    "warn",
                    "instruction file missing or outdated",
                    "run 'taprivo setup cursor --install-instructions'",
                )
            )
        return checks
