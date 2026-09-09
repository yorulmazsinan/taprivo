"""The app icon: the SVG renders, the small sizes drop the detail, the app finds it."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from taprivo.ui.icons import app_icon

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKE_ICON = REPO_ROOT / "packaging" / "macos" / "make_icon.py"


@pytest.fixture(scope="module")
def make_icon() -> ModuleType:
    """Import the generator by path: `packaging/` is a script tree, not a package."""
    spec = importlib.util.spec_from_file_location("taprivo_make_icon", MAKE_ICON)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _distinct_colors(image: QImage, limit: int = 3) -> int:
    colors: set[int] = set()
    for y in range(image.height()):
        for x in range(image.width()):
            colors.add(image.pixel(x, y))
            if len(colors) >= limit:
                return len(colors)
    return len(colors)


def test_svg_renders_a_non_uniform_image(make_icon: ModuleType, qapp: QApplication) -> None:
    image = make_icon.render(32)
    assert image.width() == 32 and image.height() == 32
    assert _distinct_colors(image) >= 3


def test_every_iconset_size_renders(make_icon: ModuleType, qapp: QApplication) -> None:
    for size in make_icon.SIZES:
        image = make_icon.render(size)
        assert image.width() == size, size
        assert not image.isNull(), size


def test_small_sizes_drop_the_detail_group(make_icon: ModuleType) -> None:
    detailed = make_icon.svg_source(detail=True)
    plain = make_icon.svg_source(detail=False)
    assert b'class="detail"' in detailed
    assert b'class="detail"' not in plain
    assert len(plain) < len(detailed)


def test_detail_follows_the_size_threshold(make_icon: ModuleType, qapp: QApplication) -> None:
    """The bolt cut-out is present at 128 px and gone at 16 px."""
    big = make_icon.render(128)
    small_with = make_icon.render(16, detail=True)
    small_without = make_icon.render(16, detail=False)
    assert _distinct_colors(big) >= 3
    assert make_icon.render(16).constBits() == small_without.constBits()
    assert small_with.constBits() != small_without.constBits()


def test_write_app_icon_writes_a_png(
    make_icon: ModuleType, qapp: QApplication, tmp_path: Path
) -> None:
    path = make_icon.write_app_icon(tmp_path / "icon.png", size=64)
    assert path.exists()
    assert QImage(str(path)).width() == 64


def test_committed_app_icon_loads(qapp: QApplication) -> None:
    icon = app_icon()
    assert not icon.isNull()
    assert icon.availableSizes()
