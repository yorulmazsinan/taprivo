"""Bridges engine callbacks (any thread) into Qt signals (main thread)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from taprivo.core.energy import EnergyEngine, Listener
from taprivo.core.state import AppSnapshot


class EngineSignals(QObject):
    snapshot_changed = Signal(object)


def connect_engine(engine: EnergyEngine, signals: EngineSignals) -> Listener:
    def listener(snapshot: AppSnapshot) -> None:
        signals.snapshot_changed.emit(snapshot)

    engine.subscribe(listener)
    return listener
