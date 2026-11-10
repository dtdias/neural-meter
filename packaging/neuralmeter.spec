# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: NeuralMeter.exe (onefile, windowed). Caminhos relativos a SPECPATH.
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ASSETS = os.path.join(SRC, "neuralmeter", "assets")
ICO = os.path.join(ASSETS, "neuralmeter.ico")

EXCLUDES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "tkinter",
]

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[SRC],
    binaries=[],
    datas=[
        (os.path.join(ASSETS, "icon.png"), "neuralmeter/assets"),
        (ICO, "neuralmeter/assets"),
    ],
    hiddenimports=collect_submodules("websockets") + collect_submodules("neuralmeter"),
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NeuralMeter",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICO,
)
