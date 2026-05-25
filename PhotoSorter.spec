# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec para Photo Sorter.
Build: pyinstaller PhotoSorter.spec
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["photo_sorter_ui.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Keyring — backends por plataforma
        "keyring.backends.macOS",
        "keyring.backends.fail",
        "keyring.backends.null",
        "keyring.backends.SecretService",
        # Pillow / HEIF
        "PIL._tkinter_finder",
        "pillow_heif",
        "pillow_heif._pillow_heif",
        # Anthropic y dependencias HTTP
        "anthropic",
        "anthropic._models",
        "anthropic._client",
        "anthropic.resources",
        "anthropic.types",
        "httpx",
        "httpcore",
        "anyio",
        "anyio._backends._asyncio",
        "sniffio",
        # email / stdlib usada por httpx
        "email.mime.multipart",
        "email.mime.text",
        # tkinter extras
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
        "tkinter.simpledialog",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoSorter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,      # Sin ventana de terminal
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PhotoSorter",
)

app = BUNDLE(
    coll,
    name="Photo Sorter.app",
    icon="icon.icns",
    bundle_identifier="com.photosorter.app",
    info_plist={
        "CFBundleDisplayName": "Photo Sorter",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,   # Soporte Dark Mode
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "Photo Sorter",
        # Permisos para acceder a carpetas del usuario
        "NSAppleEventsUsageDescription": "Photo Sorter necesita acceso para abrir carpetas.",
    },
)
