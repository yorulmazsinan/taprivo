"""Render the Taprivo app icon from `icon.svg`.

Produces the macOS `.icns` used by the app bundle and the PNG the running app
hands to `QApplication.setWindowIcon`:

    uv run python packaging/macos/make_icon.py

Only `iconutil` (macOS) is needed on top of PySide6; every raster size is
rendered from the same vector source, so the icon stays crisp at 16 px.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
SVG_PATH = HERE / "icon.svg"
ICNS_PATH = HERE / "Taprivo.icns"
APP_ICON_PATH = HERE.parents[1] / "src" / "taprivo" / "resources" / "icon.png"
APP_ICON_SIZE = 256

#: Rendered sizes; the @2x variants share the pixel size of the next entry up,
#: which is exactly how an `.iconset` is laid out.
SIZES = (16, 32, 64, 128, 256, 512, 1024)
#: Below this the bolt cut-out closes up into noise, so it is dropped.
DETAIL_MIN_SIZE = 64
_DETAIL_RE = re.compile(r'<g class="detail">.*?</g>', re.DOTALL)


def svg_source(*, detail: bool) -> bytes:
    """The SVG markup, optionally with the small-size detail group removed."""
    text = SVG_PATH.read_text(encoding="utf-8")
    if not detail:
        text = _DETAIL_RE.sub("", text)
    return text.encode("utf-8")


def ensure_app() -> QGuiApplication:
    """QImage painting needs a GUI application; reuse one if it exists."""
    existing = QGuiApplication.instance()
    if isinstance(existing, QGuiApplication):
        return existing
    return QGuiApplication(sys.argv[:1])


def render(size: int, *, detail: bool | None = None) -> QImage:
    """Render the icon at `size` x `size` pixels."""
    if detail is None:
        detail = size >= DETAIL_MIN_SIZE
    renderer = QSvgRenderer(QByteArray(svg_source(detail=detail)))
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    return image


def write_iconset(directory: Path) -> list[Path]:
    """Write the `icon_<n>x<n>[@2x].png` files `iconutil` expects."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for size in SIZES:
        image = render(size)
        for point, suffix in ((size, ""), (size // 2, "@2x")):
            if suffix and point not in SIZES:
                continue
            path = directory / f"icon_{point}x{point}{suffix}.png"
            if not image.save(str(path), "PNG"):  # pragma: no cover - disk failure
                raise RuntimeError(f"could not write {path}")
            written.append(path)
    return written


def write_app_icon(path: Path = APP_ICON_PATH, size: int = APP_ICON_SIZE) -> Path:
    """Write the PNG the running app uses as its window icon."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not render(size).save(str(path), "PNG"):  # pragma: no cover - disk failure
        raise RuntimeError(f"could not write {path}")
    return path


def build_icns(icns_path: Path = ICNS_PATH) -> Path:
    """Render an iconset next to the .icns and fold it up with `iconutil`."""
    iconset = icns_path.with_suffix(".iconset")
    write_iconset(iconset)
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
        check=True,
    )
    for png in sorted(iconset.glob("*.png")):
        png.unlink()
    iconset.rmdir()
    return icns_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iconset-only",
        action="store_true",
        help="write the .iconset directory and stop (skips iconutil)",
    )
    args = parser.parse_args(argv)
    ensure_app()
    if args.iconset_only:
        directory = ICNS_PATH.with_suffix(".iconset")
        write_iconset(directory)
        print(f"wrote {directory}")
        return 0
    print(f"wrote {build_icns()}")
    print(f"wrote {write_app_icon()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
