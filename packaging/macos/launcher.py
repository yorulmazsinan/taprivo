"""PyInstaller entry point: mirrors the `taprivo` console script."""

from __future__ import annotations

import multiprocessing
import sys


def main() -> None:
    multiprocessing.freeze_support()
    from taprivo.cli import app

    app()


if __name__ == "__main__":
    sys.exit(main())
