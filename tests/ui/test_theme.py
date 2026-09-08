from __future__ import annotations

from PySide6.QtWidgets import QApplication

from taprivo.ui import theme


def test_resolve_dark_without_app() -> None:
    assert theme.resolve("dark", None) is theme.DARK


def test_resolve_light_without_app() -> None:
    assert theme.resolve("light", None) is theme.LIGHT


def test_resolve_system_without_app_is_dark() -> None:
    assert theme.resolve("system", None) is theme.DARK


def test_resolve_system_with_app_returns_a_known_palette(qapp: QApplication) -> None:
    palette = theme.resolve("system", qapp)
    assert palette in (theme.DARK, theme.LIGHT)


def test_stylesheet_contains_accent_and_primary_button() -> None:
    sheet = theme.stylesheet(theme.DARK)
    assert "#F6C844" in sheet
    assert "QPushButton#primary" in sheet


def test_apply_theme_sets_app_stylesheet_and_returns_palette(qapp: QApplication) -> None:
    palette = theme.apply_theme(qapp, "dark")
    assert palette is theme.DARK
    assert qapp.styleSheet() != ""
