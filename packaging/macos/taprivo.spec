# -*- mode: python ; coding: utf-8 -*-
# ruff: noqa: F821
"""PyInstaller spec: standalone macOS .app bundle for Taprivo (arm64).

Run it from the repository root:

    pyinstaller --clean --noconfirm packaging/macos/taprivo.spec \
        --distpath .build/macos/dist --workpath .build/macos/work

`Analysis`, `PYZ`, `EXE`, `COLLECT`, `BUNDLE` and `SPECPATH` are injected by
PyInstaller when it evaluates this file, hence the file-level F821 exemption.
"""

import os
import re

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

from taprivo import __version__

# CFBundleVersion carries the full version (for example 0.1.0b4); CFBundleShortVersionString
# has to be a plain dotted number, so the pre-release suffix is stripped.
BUNDLE_VERSION = __version__
_release_match = re.match(r"\d+(?:\.\d+)*", __version__)
SHORT_VERSION = _release_match.group(0) if _release_match else __version__

datas = []
binaries = []
hiddenimports = []

# --- taprivo itself: package data (model, default.yaml, agent-instructions.md) ---
datas += collect_data_files("taprivo", includes=["**/*.task", "**/*.yaml", "**/*.md"])
hiddenimports += collect_submodules("taprivo")

# --- mediapipe: .tflite/.binarypb graphs + the _framework_bindings extension ---
mp_datas, mp_binaries, mp_hidden = collect_all("mediapipe")
datas += mp_datas
binaries += mp_binaries
hiddenimports += mp_hidden

# --- cv2 (opencv-contrib-python, pulled in by mediapipe) ---
cv_datas, cv_binaries, cv_hidden = collect_all("cv2")
datas += cv_datas
binaries += cv_binaries
hiddenimports += cv_hidden

# --- server stack: hooks miss the dynamically imported bits ---
for pkg in ("mcp", "uvicorn", "starlette", "anyio", "sse_starlette"):
    hiddenimports += collect_submodules(pkg)
datas += collect_data_files("mcp")

hiddenimports += [
    "taprivo.cli",
    "taprivo.ui.app",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "h11",
]

# Qt modules Taprivo never touches; each is tens of MB.
excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuick3D",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtUiTools",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtStateMachine",
    # mediapipe drags matplotlib in through its solutions/ demos; Taprivo uses none.
    "matplotlib",
    "tkinter",
    "pytest",
    "IPython",
]

# The PySide6 hook copies the whole Qt/lib directory regardless of `excludes`,
# so the frameworks behind the excluded modules are dropped from the TOC by hand.
# Nothing in Taprivo loads QML, so the QtQml/QtQuick stack goes with them.
DROPPED_FRAMEWORK_PREFIXES = (
    "QtQml",
    "QtQuick",
    "QtVirtualKeyboard",
    "QtPdf",
    "QtWebEngine",
)
# The virtual-keyboard input-context plugin links against QtVirtualKeyboard;
# leaving it behind would make Qt log a load failure at every start.
DROPPED_PLUGINS = ("platforminputcontexts/libqtvirtualkeyboardplugin.dylib",)


def _is_dropped(dest: str) -> bool:
    """True for TOC entries that belong to an excluded Qt framework or plugin."""
    normalised = dest.replace(os.sep, "/")
    for part in normalised.split("/"):
        if part.endswith(".framework") and part.startswith(DROPPED_FRAMEWORK_PREFIXES):
            return True
    return any(normalised.endswith(plugin) for plugin in DROPPED_PLUGINS)


a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

a.binaries = [entry for entry in a.binaries if not _is_dropped(entry[0])]
a.datas = [entry for entry in a.datas if not _is_dropped(entry[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Taprivo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    # Ad-hoc signing only; build.sh sign re-signs with a Developer ID.
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Taprivo",
)

app = BUNDLE(
    coll,
    name="Taprivo.app",
    icon=None,
    bundle_identifier="com.sinanyorulmaz.taprivo",
    version=BUNDLE_VERSION,
    info_plist={
        "CFBundleName": "Taprivo",
        "CFBundleDisplayName": "Taprivo",
        "CFBundleShortVersionString": SHORT_VERSION,
        "CFBundleVersion": BUNDLE_VERSION,
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
        "NSCameraUsageDescription": "Taprivo counts hand squeezes to build Motion Energy.",
        "LSUIElement": False,
    },
)
