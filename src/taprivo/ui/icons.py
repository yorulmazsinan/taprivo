"""The application icon, loaded from the packaged PNG.

`packaging/macos/make_icon.py` renders both this PNG and the bundle's `.icns`
from the same `icon.svg`, so the window icon and the Dock icon never drift.
"""

from __future__ import annotations

import logging
from importlib import resources

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QPixmap

log = logging.getLogger(__name__)

ICON_RESOURCE = "icon.png"


def app_icon() -> QIcon:
    """The Taprivo icon, or an empty icon if the resource is missing."""
    try:
        data = (resources.files("taprivo.resources") / ICON_RESOURCE).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        log.warning("application icon %s is missing", ICON_RESOURCE)
        return QIcon()
    pixmap = QPixmap()
    if not pixmap.loadFromData(QByteArray(data)):
        log.warning("application icon %s could not be decoded", ICON_RESOURCE)
        return QIcon()
    return QIcon(pixmap)
