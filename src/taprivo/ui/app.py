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
from taprivo.ui.camera_window import CameraWindow
from taprivo.ui.hud import HudWindow
from taprivo.ui.theme import apply_theme
from taprivo.ui.vision_bridge import VisionSignals
from taprivo.vision.controller import VisionController

log = logging.getLogger(__name__)


def run_app(config: Config, *, start_simulator: bool, open_camera: bool = False) -> int:
    setup_logging()
    lock = paths.InstanceLock()
    if not lock.acquire():
        print("Taprivo is already running.", file=sys.stderr)
        return 1
    try:
        token = paths.read_or_create_token()
        engine = EnergyEngine(config)
        simulator = Simulator(engine, config)
        controller = VisionController(engine, config)
        server = McpServerThread(engine, config, token)
        server.start()
        try:
            existing = QApplication.instance()
            qt_app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
            qt_app.setApplicationName("Taprivo")
            qt_app.setQuitOnLastWindowClosed(True)
            palette = apply_theme(qt_app, config.hud.theme)

            camera_window: CameraWindow | None = None
            signals = VisionSignals()

            def open_camera_window() -> None:
                nonlocal camera_window
                if camera_window is None:
                    camera_window = CameraWindow(controller, config, signals, palette=palette)
                camera_window.show()
                camera_window.raise_()
                camera_window.activateWindow()

            window = HudWindow(
                engine, simulator, config, on_open_camera=open_camera_window, palette=palette
            )
            engine_signals = EngineSignals(parent=window)
            engine_signals.snapshot_changed.connect(
                window.on_snapshot, Qt.ConnectionType.QueuedConnection
            )
            connect_engine(engine, engine_signals)
            window.on_snapshot(engine.snapshot())
            if start_simulator:
                simulator.start()
            window.show()
            window.raise_()
            window.activateWindow()
            if open_camera:
                open_camera_window()
            log.info("Taprivo started (simulator=%s)", start_simulator)
            code = qt_app.exec()
            return int(code)
        finally:
            controller.stop()
            server.stop()
    finally:
        lock.release()
