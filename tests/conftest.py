import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from taprivo.config import Config, load_config


@pytest.fixture(autouse=True)
def _reset_taprivo_logging() -> Iterator[None]:
    """Prevent a handler bound to one test's (possibly closed) stream from
    leaking into the next test via the module-level "taprivo" logger."""
    yield
    logger = logging.getLogger("taprivo")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


@pytest.fixture
def taprivo_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "taprivo-home"
    monkeypatch.setenv("TAPRIVO_HOME", str(home))
    return home


@pytest.fixture
def config(taprivo_home: Path) -> Config:
    return load_config()
