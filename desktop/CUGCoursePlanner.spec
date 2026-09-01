# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).resolve().parent
backend_root = project_root / "backend"
seed_database = Path(os.environ["KESHI_BUILD_SEED_DB"]).resolve()

if not seed_database.is_file():
    raise SystemExit(f"prepared desktop seed is missing: {seed_database}")

datas = [
    (str(project_root / "frontend"), "frontend"),
    (str(project_root / "data" / "catalog"), "data/catalog"),
    (str(project_root / "data" / "curricula"), "data/curricula"),
    (str(seed_database), "seed"),
    (str(project_root / "desktop" / "assets"), "desktop/assets"),
]

hiddenimports = collect_submodules("uvicorn") + [
    "webview.platforms.edgechromium",
]

a = Analysis(
    [str(project_root / "desktop" / "launcher.py")],
    pathex=[str(project_root), str(backend_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "cefpython3",
        "gi",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "_pytest",
        "httpx",
        "hypothesis",
        "jsonschema",
        "mypy",
        "pytest",
        "ruff",
        "tkinter",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Keshi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    hide_console="hide-early",
    icon=str(project_root / "desktop" / "assets" / "app.ico"),
    version=str(project_root / "desktop" / "windows_version_info.txt"),
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="课石-v0.2.0-win64",
)
