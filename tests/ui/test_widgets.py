from __future__ import annotations

from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from taprivo.ui.widgets import Chip, EnergyBar, Meter, StatusDot


def _has_multiple_colors(image: QImage) -> bool:
    colors: set[int] = set()
    for y in range(image.height()):
        for x in range(image.width()):
            colors.add(image.pixel(x, y))
            if len(colors) > 1:
                return True
    return False


def test_energy_bar_range_and_value_clamp(qtbot: QtBot) -> None:
    bar = EnergyBar()
    qtbot.addWidget(bar)
    bar.setRange(0, 10)
    bar.setValue(15)
    assert bar.value() == 10
    bar.setValue(-5)
    assert bar.value() == 0
    bar.setValue(4)
    assert bar.value() == 4


def test_energy_bar_renders_non_uniform(qtbot: QtBot) -> None:
    bar = EnergyBar()
    qtbot.addWidget(bar)
    bar.resize(200, 14)
    bar.setRange(0, 100)
    bar.setValue(60)
    bar.show()
    image = bar.grab().toImage()
    assert image.size().width() == 200
    assert _has_multiple_colors(image)


def test_meter_value_clamps_and_levels_stored(qtbot: QtBot) -> None:
    meter = Meter()
    qtbot.addWidget(meter)
    meter.setValue(1.5)
    assert meter.value() == 1.0
    meter.setValue(-0.5)
    assert meter.value() == 0.0
    meter.setLevels(0.8, 0.45)
    assert meter.levels() == (0.8, 0.45)


def test_meter_renders_non_uniform(qtbot: QtBot) -> None:
    meter = Meter()
    qtbot.addWidget(meter)
    meter.resize(180, 22)
    meter.setValue(0.7)
    meter.setLevels(0.8, 0.45)
    meter.setState("Open")
    meter.show()
    image = meter.grab().toImage()
    assert image.size().height() == 22
    assert _has_multiple_colors(image)


def test_status_dot_sets_status_and_text(qtbot: QtBot) -> None:
    dot = StatusDot()
    qtbot.addWidget(dot)
    dot.setStatus("ok", "Ready")
    assert dot.text() == "Ready"


def test_status_dot_renders_non_uniform(qtbot: QtBot) -> None:
    dot = StatusDot()
    qtbot.addWidget(dot)
    dot.setStatus("err", "No signal")
    dot.resize(120, 18)
    dot.show()
    image = dot.grab().toImage()
    assert _has_multiple_colors(image)


def test_chip_set_chip_contains_label_and_value(qtbot: QtBot) -> None:
    chip = Chip()
    qtbot.addWidget(chip)
    chip.setChip("#F97316", "Thumb", 3)
    assert "Thumb" in chip.text()
    assert "3" in chip.text()


def test_chip_renders_non_uniform(qtbot: QtBot) -> None:
    chip = Chip()
    qtbot.addWidget(chip)
    chip.setChip("#F97316", "Thumb", 3)
    chip.adjustSize()
    chip.show()
    image = chip.grab().toImage()
    assert _has_multiple_colors(image)
