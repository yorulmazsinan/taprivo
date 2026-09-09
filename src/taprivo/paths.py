"""Filesystem locations for Taprivo user state."""

from __future__ import annotations

import fcntl
import os
import secrets
from pathlib import Path

TOKEN_BYTES = 32


def home() -> Path:
    env = os.environ.get("TAPRIVO_HOME")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "taprivo"


def token_path() -> Path:
    return home() / "token"


def lock_path() -> Path:
    return home() / "app.lock"


def statusline_script_path() -> Path:
    """The status line command Claude Code runs on every assistant message."""
    return home() / "statusline.sh"


def statusline_chain_path() -> Path:
    """The status line that was configured before Taprivo took the setting over."""
    return home() / "statusline-chain.json"


def log_dir() -> Path:
    return home() / "logs"


def user_config_path() -> Path:
    return home() / "config.yaml"


def stats_path(override: str | None = None) -> Path:
    """Where session statistics live; `stats.path` in config.yaml wins."""
    if override:
        return Path(override).expanduser()
    return home() / "stats.sqlite"


def ensure_home() -> Path:
    path = home()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def read_or_create_token() -> str:
    ensure_home()
    path = token_path()
    if path.exists():
        os.chmod(path, 0o600)
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(TOKEN_BYTES)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except FileExistsError:
        # Another process won the race; read the token they created
        os.chmod(path, 0o600)
        return path.read_text(encoding="utf-8").strip()
    return token


class InstanceLock:
    """Advisory lock so only one Taprivo app runs per user."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or lock_path()
        self._fd: int | None = None

    def acquire(self) -> bool:
        ensure_home()
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None
