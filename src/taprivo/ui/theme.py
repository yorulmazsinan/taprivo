"""Colour palettes and QSS generation for the HUD and camera window."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from taprivo.core.events import Finger

_FINGER_COLORS: dict[Finger, str] = {
    Finger.THUMB: "#F97316",
    Finger.INDEX: "#22D3EE",
    Finger.MIDDLE: "#A78BFA",
    Finger.RING: "#34D399",
    Finger.PINKY: "#F472B6",
}


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface_alt: str
    text: str
    text_dim: str
    border: str
    accent: str
    ok: str
    warn: str
    err: str
    finger: dict[Finger, str] = field(default_factory=lambda: dict(_FINGER_COLORS))


DARK = Palette(
    bg="#14161C",
    surface="#1C1F27",
    surface_alt="#262A35",
    text="#E8EAF0",
    text_dim="#8B90A0",
    border="#2F3441",
    accent="#F6C844",
    ok="#34D399",
    warn="#F59E0B",
    err="#F87171",
)

LIGHT = Palette(
    bg="#F7F7F9",
    surface="#FFFFFF",
    surface_alt="#ECEEF3",
    text="#1B1E27",
    text_dim="#6B7080",
    border="#D9DCE5",
    accent="#D9A620",
    ok="#34D399",
    warn="#F59E0B",
    err="#F87171",
)


def resolve(mode: str, app: QApplication | None) -> Palette:
    """Resolve a theme mode ("system", "dark", "light") to a concrete Palette."""
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    if mode == "system":
        if app is None:
            return DARK
        scheme = app.styleHints().colorScheme()
        return DARK if scheme == Qt.ColorScheme.Dark else LIGHT
    raise ValueError(f"unknown theme mode: {mode!r}")


def stylesheet(palette: Palette) -> str:
    """Build the application-wide QSS for the given palette."""
    return f"""
    QWidget {{
        background-color: {palette.bg};
        color: {palette.text};
        font-size: 13px;
    }}
    QLabel {{
        background: transparent;
        color: {palette.text};
    }}
    QPushButton {{
        background-color: {palette.surface_alt};
        color: {palette.text};
        border: 1px solid {palette.border};
        border-radius: 8px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{
        border: 1px solid {palette.accent};
    }}
    QPushButton:pressed {{
        background-color: {palette.border};
    }}
    QPushButton:disabled {{
        color: {palette.text_dim};
        border: 1px solid {palette.border};
    }}
    QPushButton#primary {{
        background-color: {palette.accent};
        color: {palette.bg};
        border: none;
        font-weight: 600;
    }}
    QPushButton#primary:hover {{
        background-color: {palette.accent};
    }}
    QPushButton#primary:disabled {{
        background-color: {palette.surface_alt};
        color: {palette.text_dim};
    }}
    QComboBox {{
        background-color: {palette.surface};
        color: {palette.text};
        border: 1px solid {palette.border};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {palette.surface};
        color: {palette.text};
        selection-background-color: {palette.accent};
        selection-color: {palette.bg};
    }}
    QProgressBar {{
        background-color: {palette.surface_alt};
        border: none;
        border-radius: 4px;
        max-height: 8px;
    }}
    QProgressBar::chunk {{
        background-color: {palette.accent};
        border-radius: 4px;
    }}
    QMessageBox {{
        background-color: {palette.surface};
        color: {palette.text};
    }}
    QToolTip {{
        background-color: {palette.surface_alt};
        color: {palette.text};
        border: 1px solid {palette.border};
        padding: 4px;
    }}
    """


def apply_theme(app: QApplication, mode: str) -> Palette:
    """Resolve and apply the theme's stylesheet to the application."""
    palette = resolve(mode, app)
    app.setStyleSheet(stylesheet(palette))
    return palette
