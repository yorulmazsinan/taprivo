"""Application bootstrap: lock, engine, MCP thread, Qt event loop."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from taprivo import paths
from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.logging_setup import setup_logging
from taprivo.mcp.server import McpServerThread
from taprivo.simulator import Simulator
from taprivo.ui.bridge import EngineSignals, connect_engine
from taprivo.ui.hud import HudWindow

log = logging.getLogger(__name__)


def run_app(config: Config, *, start_simulator: bool) -> int:
    setup_logging()
    lock = paths.InstanceLock()
    if not lock.acquire():
        print("Taprivo is already running.", file=sys.stderr)
        return 1
    try:
        token = paths.read_or_create_token()
        engine = EnergyEngine(config)
        simulator = Simulator(engine, config)
        server = McpServerThread(engine, config, token)
        server.start()
        try:
            existing = QApplication.instance()
            qt_app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
            qt_app.setApplicationName("Taprivo")
            qt_app.setQuitOnLastWindowClosed(True)

            window = HudWindow(engine, simulator, config)
            signals = EngineSignals(parent=window)
            signals.snapshot_changed.connect(window.on_snapshot, Qt.ConnectionType.QueuedConnection)
            connect_engine(engine, signals)
            window.on_snapshot(engine.snapshot())
            if start_simulator:
                simulator.start()
            window.show()
            window.raise_()
            window.activateWindow()
            log.info("Taprivo started (simulator=%s)", start_simulator)
            code = qt_app.exec()
            return int(code)
        finally:
            server.stop()
    finally:
        lock.release()
