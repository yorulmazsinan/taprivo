from pathlib import Path

import pytest

from taprivo.config import Config, load_config


@pytest.fixture
def taprivo_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "taprivo-home"
    monkeypatch.setenv("TAPRIVO_HOME", str(home))
    return home


@pytest.fixture
def config(taprivo_home: Path) -> Config:
    return load_config()
