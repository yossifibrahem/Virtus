# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).parent
METADATA = ROOT / "build" / "generated" / "build_metadata.json"
ICON = ROOT / "build" / "generated" / "Virtus.ico"
VERSION_INFO = ROOT / "build" / "generated" / "windows_version_info.txt"

a = Analysis(
    [str(ROOT / "windows_desktop_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "templates"), "templates"),
        (str(ROOT / "static"), "static"),
        (str(METADATA), "."),
        (str(ICON), "."),
        (str(ROOT / "Dockerfile.sandbox"), "."),
        (str(ROOT / "sandbox_tools.py"), "."),
        (str(ROOT / "sandbox_files.py"), "."),
        (str(ROOT / ".dockerignore"), "."),
    ],
    hiddenimports=[
        "mcp.client.stdio",
        "mcp.os.win32.utilities",
        "mcp.types",
        "pystray._win32",
        "pywintypes",
        "waitress",
        "win32api",
        "win32con",
        "win32job",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["rumps", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Virtus AI Chat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON),
    version=str(VERSION_INFO),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Virtus AI Chat",
)
