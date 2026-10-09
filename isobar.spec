# PyInstaller spec for Isobar. Build with: pyinstaller --noconfirm --clean isobar.spec
# -*- mode: python ; coding: utf-8 -*-

import re
import sys

# The version lives in one place, isobar/__init__.py, and the macOS bundle reads it from there.
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', open("isobar/__init__.py", encoding="utf-8").read()).group(1)

if sys.platform == "win32":
    ICON = "assets/icon.ico"
elif sys.platform == "darwin":
    ICON = "assets/icon.icns"
else:
    ICON = None

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=[("isobar/web", "isobar/web")],
    hiddenimports=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtNetwork",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data", "test"],
    noarchive=False,
)

# PyInstaller's Qt hooks pull in most of Qt. Isobar only uses Qt WebEngine
# with widgets, so the 3D, QML, multimedia and similar modules are dropped.
# This cuts a few hundred MB. If a build ever fails to start with a missing
# Qt library, remove its prefix from DROP_MODULES.

DROP_MODULES = (
    "3D", "Charts", "DataVisualization", "Graphs", "Labs", "Location", "Multimedia",
    "Quick3DAsset", "Quick3DEffects", "Quick3DHelpers", "Quick3DParticle", "Quick3DRuntime",
    "Quick3DSpatial", "Quick3DXr", "QuickControls2", "QuickDialogs2", "QuickEffects",
    "QuickLayouts", "QuickParticles", "QuickShapes", "QuickTest", "QuickTimeline",
    "QuickVectorImage", "RemoteObjects", "Scxml", "Sensors", "SpatialAudio", "Sql",
    "StateMachine", "Test", "TextToSpeech", "WebView", "WebSockets", "WaylandCompositor",
    "ShaderTools", "PdfQuick", "WebEngineQuick", "WebChannelQuick", "PositioningQuick",
    "Bluetooth", "Nfc", "HttpServer", "Designer", "UiTools", "Help",
)
QT_NAME = re.compile(r"(?:^|/)(?:lib)?Qt6?([A-Za-z0-9]+)(?:\.so|\.dll|\.framework|\.abi3|\.pyd|\.dylib)")


def keep(entry):
    dest = entry[0].replace("\\", "/")
    if "/qml/" in dest or dest.startswith("PySide6/qml/"):
        return False
    if "/translations/" in dest and not dest.endswith("qtwebengine_locales/en-US.pak"):
        return False
    match = QT_NAME.search(dest)
    if match and "/plugins/" not in dest and match.group(1).startswith(DROP_MODULES):
        return False
    return True


a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Isobar",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Isobar",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Isobar.app",
        icon=ICON,
        bundle_identifier="io.github.nullangst.isobar",
        info_plist={
            "CFBundleName": "Isobar",
            "CFBundleDisplayName": "Isobar",
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.weather",
        },
    )
