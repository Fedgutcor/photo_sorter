# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec para Photo Sorter — Windows.
Build: pyinstaller PhotoSorter_Windows.spec
Requiere: pip install pyinstaller
"""

block_cipher = None

a = Analysis(
    ["photo_sorter_ui.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Keyring — Windows Credential Manager
        "keyring.backends.Windows",
        "keyring.backends.fail",
        "keyring.backends.null",
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PhotoSorter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",          # convertir icon.icns → icon.ico antes de buildear
    version="version_info.txt",
)
