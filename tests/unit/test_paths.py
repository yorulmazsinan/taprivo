import os
import stat
from pathlib import Path

import pytest

from taprivo import paths


def test_home_uses_env_override(taprivo_home: Path) -> None:
    assert paths.home() == taprivo_home


def test_home_defaults_to_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TAPRIVO_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert paths.home() == tmp_path / "xdg" / "taprivo"


def test_ensure_home_creates_private_dir(taprivo_home: Path) -> None:
    created = paths.ensure_home()
    assert created.is_dir()
    assert stat.S_IMODE(created.stat().st_mode) == 0o700


def test_token_is_created_once_with_0600(taprivo_home: Path) -> None:
    first = paths.read_or_create_token()
    second = paths.read_or_create_token()
    assert first == second
    assert len(first) >= 32
    assert stat.S_IMODE(paths.token_path().stat().st_mode) == 0o600


def test_instance_lock_is_exclusive(taprivo_home: Path) -> None:
    a = paths.InstanceLock()
    b = paths.InstanceLock()
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()


def test_log_dir_under_home(taprivo_home: Path) -> None:
    assert paths.log_dir() == taprivo_home / "logs"
    assert os.path.basename(paths.lock_path()) == "app.lock"
