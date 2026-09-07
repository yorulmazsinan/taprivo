"""Low-volume logging to stderr and a rotating file."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from taprivo import paths

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO, log_dir: Path | None = None) -> None:
    root = logging.getLogger("taprivo")
    root.setLevel(level)
    if root.handlers:
        return
    formatter = logging.Formatter(LOG_FORMAT)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)
    directory = log_dir or paths.log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(directory / "taprivo.log", maxBytes=1_048_576, backupCount=3)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
