"""Adapter contract shared by all agent integrations."""

from __future__ import annotations

import difflib
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Protocol

CheckStatus = Literal["ok", "warn", "fail"]


class SetupError(Exception):
    """A setup action could not be completed."""


@dataclass(frozen=True)
class DetectResult:
    found: bool
    version: str | None = None
    path: str | None = None


@dataclass
class SetupAction:
    kind: str
    description: str
    run: Callable[[], str]

    def apply(self) -> str:
        return self.run()


@dataclass
class SetupPlan:
    actions: list[SetupAction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Check:
    name: str
    status: CheckStatus
    detail: str
    hint: str = ""


@dataclass(frozen=True)
class SetupOptions:
    project: bool = False
    install_instructions: bool = False
    project_dir: Path = field(default_factory=Path.cwd)


class AgentAdapter(Protocol):
    name: str

    def detect(self) -> DetectResult: ...

    def plan_setup(self, options: SetupOptions) -> SetupPlan: ...

    def plan_remove(self, options: SetupOptions) -> SetupPlan: ...

    def verify(self) -> list[Check]: ...


def instructions_text() -> str:
    """The packaged agent instruction text every adapter installs."""
    return resources.files("taprivo.resources").joinpath("agent-instructions.md").read_text("utf-8")


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text("utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def backup(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    target = path.with_name(f"{path.name}.taprivo-backup-{stamp}-{suffix}")
    shutil.copy2(path, target)
    return target


def _render_block(body: str, start: str, end: str) -> str:
    return f"{start}\n{body}\n{end}\n"


def upsert_block(text: str, body: str, start: str, end: str) -> str:
    block = _render_block(body, start, end)
    begin = text.find(start)
    finish = text.find(end, begin + len(start)) if begin != -1 else -1
    if begin != -1 and finish != -1:
        finish_end = finish + len(end)
        if finish_end < len(text) and text[finish_end] == "\n":
            finish_end += 1
        return text[:begin] + block + text[finish_end:]
    if not text:
        return block
    base = text if text.endswith("\n") else text + "\n"
    return base + "\n" + block


def remove_block(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin == -1:
        return text
    finish = text.find(end, begin)
    if finish == -1:
        return text
    finish_end = finish + len(end)
    if finish_end < len(text) and text[finish_end] == "\n":
        finish_end += 1
    head = text[:begin]
    tail = text[finish_end:]
    if not tail and head.endswith("\n"):
        head = head[:-1]
    return head + tail


def unified_diff(before: str, after: str, name: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{name} (before)",
        tofile=f"{name} (after)",
    )
    return "".join(lines)


def merge_mcp_server(existing: dict[str, Any], name: str, entry: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    servers = dict(merged.get("mcpServers") or {})
    servers[name] = entry
    merged["mcpServers"] = servers
    return merged


def remove_mcp_server(existing: dict[str, Any], name: str) -> dict[str, Any]:
    if "mcpServers" not in existing:
        return existing
    merged = dict(existing)
    servers = dict(merged.get("mcpServers") or {})
    servers.pop(name, None)
    merged["mcpServers"] = servers
    return merged


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text
