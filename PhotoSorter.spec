# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec para Photo Sorter — macOS.
Build: pyinstaller PhotoSorter.spec --noconfirm --clean
"""

import sys
from pathlib import Path

block_cipher = None

HIDDEN = [
    # Keyring
    "keyring.backends.macOS",
    "keyring.backends.fail",
    "keyring.backends.null",
    "keyring.backends.SecretService",
    # Pillow / HEIF
    "PIL._tkinter_finder",
    "pillow_heif",
    "pillow_heif._pillow_heif",
    # Anthropic
    "anthropic",
    "anthropic._models",
    "anthropic._client",
    "anthropic.resources",
    "anthropic.types",
    # HTTP
    "httpx",
    "httpcore",
    "anyio",
    "anyio._backends._asyncio",
    "sniffio",
    "email.mime.multipart",
    "email.mime.text",
    # Google AI Studio
    "google.genai",
    "google.genai.types",
    "google.auth",
    # OpenAI
    "openai",
    # Groq
    "groq",
    # tkinter
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
    "tkinter.simpledialog",
]

a = Analysis(
    ["photo_sorter_ui.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PhotoSorter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.icns",
)

app = BUNDLE(
    exe,
    name="Photo Sorter.app",
    icon="icon.icns",
    bundle_identifier="com.ultragresion.photosorter",
    info_plist={
        "CFBundleDisplayName":        "Photo Sorter",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion":            "1",
        "NSHighResolutionCapable":    True,
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion":     "12.0",
        "NSHumanReadableCopyright":   "© 2025 ultragresion.com",
        "NSPhotoLibraryUsageDescription": "Photo Sorter necesita acceso a tus fotos.",
        "NSAppleEventsUsageDescription":  "Photo Sorter necesita acceso para abrir carpetas.",
    },
)
