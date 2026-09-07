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


def test_token_race_condition(taprivo_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate concurrent token creation: another process wins the race."""
    token_path = paths.token_path()
    known_token = "race-winner-token-value-12345678"
    real_open = os.open
    call_count = [0]

    def mock_open(path: int | str | Path, flags: int = 0, mode: int = 0o777) -> int:
        """Wrap os.open to simulate race condition on first token creation."""
        call_count[0] += 1
        path_str = str(path) if isinstance(path, (str, Path)) else ""
        token_path_str = str(token_path)
        # On first open attempt for token file with EXCL flag, create it and raise FileExistsError
        if call_count[0] == 1 and path_str == token_path_str and (flags & os.O_EXCL):
            token_path.write_text(known_token + "\n")
            os.chmod(token_path, 0o600)
            raise FileExistsError(f"File exists: {path}")
        # Fall back to real os.open for other calls
        return real_open(path, flags, mode)  # type: ignore[return-value]

    monkeypatch.setattr("taprivo.paths.os.open", mock_open)
    result = paths.read_or_create_token()
    assert result == known_token
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
