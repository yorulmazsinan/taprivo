import logging
from pathlib import Path

from taprivo.logging_setup import setup_logging


def test_setup_logging_creates_rotating_file(taprivo_home: Path) -> None:
    setup_logging(log_dir=taprivo_home / "logs")
    logging.getLogger("taprivo.test").info("hello")
    for handler in logging.getLogger("taprivo").handlers:
        handler.flush()
    log_file = taprivo_home / "logs" / "taprivo.log"
    assert log_file.exists() and "hello" in log_file.read_text()
    count = len(logging.getLogger("taprivo").handlers)
    setup_logging(log_dir=taprivo_home / "logs")
    assert len(logging.getLogger("taprivo").handlers) == count
