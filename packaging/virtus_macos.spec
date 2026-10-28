# -*- mode: python ; coding: utf-8 -*-
import json
from pathlib import Path

ROOT = Path(SPECPATH).parent
METADATA = ROOT / "build" / "generated" / "build_metadata.json"
ICON = ROOT / "build" / "generated" / "Virtus.icns"
BUILD_METADATA = json.loads(METADATA.read_text(encoding="utf-8"))

a = Analysis(
    [str(ROOT / "desktop_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "templates"), "templates"),
        (str(ROOT / "static"), "static"),
        (str(METADATA), "."),
        (str(ROOT / "Dockerfile.sandbox"), "."),
        (str(ROOT / "sandbox_tools.py"), "."),
        (str(ROOT / "sandbox_files.py"), "."),
        (str(ROOT / ".dockerignore"), "."),
    ],
    hiddenimports=[
        "mcp.client.stdio",
        "mcp.types",
        "rumps",
        "waitress",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
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
    target_arch="arm64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Virtus AI Chat",
)

app = BUNDLE(
    coll,
    name="Virtus AI Chat.app",
    icon=str(ICON),
    bundle_identifier="com.virtus.chat",
    info_plist={
        "CFBundleDisplayName": "Virtus AI Chat",
        "CFBundleShortVersionString": BUILD_METADATA["bundle_short_version"],
        "CFBundleVersion": BUILD_METADATA["bundle_version"],
        "LSMinimumSystemVersion": "14.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
)
