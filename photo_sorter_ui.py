#!/usr/bin/env python3
"""
Photo Sorter UI — Interfaz gráfica para organizar fotos con Claude Vision API.
Ejecutar: python photo_sorter_ui.py
"""

import asyncio
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

try:
    from PIL import Image, ImageTk
    HAS_PIL_UI = True
except ImportError:
    HAS_PIL_UI = False

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

KEYRING_SERVICE         = "PhotoSorter"
KEYRING_USER_LEGACY     = "anthropic_api_key"   # clave vieja — migración automática

def _keyring_user(provider: str) -> str:
    return f"api_key_{provider}"

try:
    from photo_sorter import (
        ALLOWED_CATEGORIES, MODEL_COSTS, PROVIDERS,
        CacheDB, collect_images, compute_sha256,
        estimate_cost, generate_report, normalize_category, process_images,
    )
    HAS_CORE = True
    IMPORT_ERROR = ""
except ImportError as e:
    HAS_CORE = False
    IMPORT_ERROR = str(e)
    PROVIDERS = {}
    MODEL_COSTS = {}

# ── Persistencia ──────────────────────────────────────────────────────────────
if sys.platform == "darwin":
    _APP_DATA = Path.home() / "Library" / "Application Support" / "PhotoSorter"
elif sys.platform == "win32":
    _APP_DATA = Path(os.environ.get("APPDATA", Path.home())) / "PhotoSorter"
else:
    _APP_DATA = Path.home() / ".config" / "photo_sorter"

_APP_DATA.mkdir(parents=True, exist_ok=True)
CONFIG_FILE  = _APP_DATA / "config.json"
PRESETS_FILE = _APP_DATA / "presets.json"

for _old, _new in [
    (Path(__file__).parent / ".ui_config.json", CONFIG_FILE),
    (Path(__file__).parent / ".presets.json",   PRESETS_FILE),
]:
    if _old.exists() and not _new.exists():
        _old.rename(_new)

# ── Opciones ──────────────────────────────────────────────────────────────────
STRUCTURE_OPTIONS: dict[str, str] = {
    "Una sola carpeta":       "flat",
    "Por categoría":          "cat",
    "Categoría / Año-Mes":    "cat/ym",
    "Categoría / Año":        "cat/y",
    "Categoría / Año / Mes":  "cat/y/m",
    "Año-Mes / Categoría":    "ym/cat",
    "Año / Categoría":        "y/cat",
}

STRUCTURE_EXAMPLES: dict[str, str] = {
    "flat":    "salida/foto.jpg",
    "cat":     "mascotas-perros/foto.jpg",
    "cat/ym":  "mascotas-perros/2024-03/foto.jpg",
    "cat/y":   "mascotas-perros/2024/foto.jpg",
    "cat/y/m": "mascotas-perros/2024/03/foto.jpg",
    "ym/cat":  "2024-03/mascotas-perros/foto.jpg",
    "y/cat":   "2024/mascotas-perros/foto.jpg",
}

NAMING_OPTIONS: dict[str, str] = {
    "Nombre original":                                   "original",
    "Fecha + categoría  —  20240315_perro_01.jpg":       "date_cat_n",
    "Fecha + número  —  20240315_001.jpg":               "date_n",
    "Fecha + nombre original  —  20240315_IMG_0542.jpg": "date_orig",
    "Fecha + hora  —  20240315_143022.jpg":              "ymd_time",
    "Categoría + número  —  perro_001.jpg":              "cat_n",
    "Categoría + fecha  —  perro_20240315_01.jpg":       "cat_date_n",
    "Año-mes + número  —  2024-03_001.jpg":              "ym_n",
    "Solo número global  —  0001.jpg":                   "n",
}

PROVIDER_OPTIONS: dict[str, str] = {
    info["name"]: pid for pid, info in PROVIDERS.items()
} if PROVIDERS else {"Anthropic (Claude)": "anthropic"}

def _model_options_for(provider_id: str) -> dict[str, str]:
    """Devuelve {label: model_id} para el proveedor dado."""
    p = PROVIDERS.get(provider_id, {})
    return {info["label"]: mid for mid, info in p.get("models", {}).items()}

# Compatibilidad con código que usa MODEL_OPTIONS directamente
MODEL_OPTIONS: dict[str, str] = _model_options_for("anthropic")

BUILTIN_PRESETS: dict[str, dict] = {
    "Estándar  (cat/mes, nombre original)": {
        "structure": "cat/ym", "naming": "original", "recursive": False,
    },
    "Renombrado completo  (cat/mes, fecha+cat)": {
        "structure": "cat/ym", "naming": "date_cat_n", "recursive": False,
    },
    "Archivo anual  (cat/año, fecha+cat, recursivo)": {
        "structure": "cat/y", "naming": "date_cat_n", "recursive": True,
    },
    "Orden cronológico  (año-mes/cat, fecha+nº)": {
        "structure": "ym/cat", "naming": "date_n", "recursive": False,
    },
    "Una sola carpeta  (todo junto, solo nº)": {
        "structure": "flat", "naming": "n", "recursive": False,
    },
    "Solo categorías  (sin fechas, nombre original)": {
        "structure": "cat", "naming": "original", "recursive": False,
    },
    "Fotos personales  (cat/mes, fecha+nombre, recursivo)": {
        "structure": "cat/ym", "naming": "date_orig", "recursive": True,
    },
}
_CUSTOM_LABEL = "─── personalizado ───"

# ── Paleta ────────────────────────────────────────────────────────────────────
C = {
    "bg":      "#f0f0f0", "surface": "#ffffff", "border":  "#d0d0d0",
    "accent":  "#4c5fd7", "text":    "#1a1a1a", "muted":   "#888888",
    "log_bg":  "#16161e", "log_txt": "#c0caf5",
    "success": "#9ece6a", "warn":    "#e0af68",
    "err":     "#f7768e", "cache":   "#7dcfff",
    "thumb":   "#d0d0d4", "sep":     "#c8c8c8",
}

# ─────────────────────────────────────────────────────────────────────────────

class PhotoSorterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Photo Sorter")
        self.root.configure(bg=C["bg"])
        self.root.minsize(660, 700)
        self.root.resizable(True, True)

        self.running          = False
        self._completed       = False
        self.cancel_event     = threading.Event()
        self.pause_event      = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self.results: list[dict]    = []
        self._applying_preset       = False
        self._last_thumbnail        = None
        self._custom_categories: list[str] | None = None
        self._images_to_process: list[Path] | None = None
        self._existing_results: list[dict] = []
        self._active_provider: str = "anthropic"

        # refs para colapsibles
        self._adv_body   = None
        self._adv_open   = [False]
        self._adv_btn    = None
        self._instr_body = None
        self._instr_open = [False]
        self._instr_btn  = None

        self._setup_styles()
        self._build_ui()
        self._load_config()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._bind_keys()
        self._center(700, 820)

    # ── Estilos ───────────────────────────────────────────────────────────────

    def _setup_styles(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        base = {"background": C["bg"], "foreground": C["text"], "font": ("Helvetica", 10)}
        s.configure(".", **base)
        for w in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton"):
            s.configure(w, background=C["bg"])

        s.configure("Dim.TLabel",
                    background=C["bg"], foreground=C["muted"])
        s.configure("Section.TLabel",
                    background=C["bg"], foreground=C["muted"],
                    font=("Helvetica", 8, "bold"))
        s.configure("TSeparator", background=C["sep"])
        s.configure("TEntry",
                    fieldbackground=C["surface"], bordercolor=C["border"],
                    lightcolor=C["border"], darkcolor=C["border"])
        s.configure("TCombobox",
                    fieldbackground=C["surface"], bordercolor=C["border"],
                    selectbackground=C["accent"], selectforeground="white")
        s.map("TCombobox", fieldbackground=[("readonly", C["surface"])])
        s.configure("TNotebook", background=C["bg"], bordercolor=C["border"])
        s.configure("TNotebook.Tab",
                    background=C["bg"], foreground=C["muted"],
                    font=("Helvetica", 9), padding=(10, 4))
        s.map("TNotebook.Tab",
              background=[("selected", C["surface"])],
              foreground=[("selected", C["text"])])

        # Botones
        s.configure("Primary.TButton",
                    background=C["accent"], foreground="white",
                    font=("Helvetica", 10, "bold"), padding=(16, 6), relief="flat")
        s.map("Primary.TButton",
              background=[("active", "#3a4ec0"), ("disabled", "#b8b8b8")],
              foreground=[("disabled", "#e0e0e0")])
        s.configure("Flat.TButton",
                    background=C["surface"], foreground=C["text"],
                    font=("Helvetica", 10), padding=(10, 5), relief="flat")
        s.map("Flat.TButton",
              background=[("active", C["border"]), ("disabled", C["bg"])],
              foreground=[("disabled", "#aaaaaa")])
        s.configure("Pause.TButton",
                    background="#fff8e0", foreground="#7a5500",
                    font=("Helvetica", 10), padding=(10, 5), relief="flat")
        s.map("Pause.TButton",
              background=[("active", "#ffefb0"), ("disabled", C["bg"])],
              foreground=[("disabled", "#aaaaaa")])
        s.configure("Danger.TButton",
                    background="#fff0f0", foreground="#b71c1c",
                    font=("Helvetica", 10), padding=(10, 5), relief="flat")
        s.map("Danger.TButton",
              background=[("active", "#ffd7d7"), ("disabled", C["bg"])],
              foreground=[("disabled", "#aaaaaa")])
        s.configure("Ghost.TButton",
                    background=C["bg"], foreground=C["muted"],
                    font=("Helvetica", 9), padding=(2, 2), relief="flat")
        s.map("Ghost.TButton",
              background=[("active", C["border"])],
              foreground=[("active", C["text"])])
        s.configure("Accent.Horizontal.TProgressbar",
                    troughcolor=C["border"], background=C["accent"], thickness=6)

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _sep(self, parent, title: str, pady_above: int = 12) -> None:
        """Separador horizontal con etiqueta de sección inline."""
        f = ttk.Frame(parent)
        f.pack(fill=tk.X, pady=(pady_above, 5))
        ttk.Label(f, text=title, style="Section.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Separator(f, orient=tk.HORIZONTAL).pack(
            side=tk.LEFT, fill=tk.X, expand=True, pady=(2, 0))

    def _collapsible(self, parent, label: str, initially_open: bool = False):
        """
        Sección colapsable. Devuelve (body_frame, toggle_fn).
        toggle_fn(open_to=None) — si open_to es None, invierte el estado.
        """
        is_open = [initially_open]
        btn_row = ttk.Frame(parent)
        btn_row.pack(fill=tk.X, pady=(4, 0))
        body = ttk.Frame(parent, padding="14 4 0 0")

        def toggle(open_to=None):
            target = (not is_open[0]) if open_to is None else open_to
            if target and not is_open[0]:
                body.pack(fill=tk.X, after=btn_row)
                btn.config(text=f"▾  {label}")
                is_open[0] = True
            elif not target and is_open[0]:
                body.pack_forget()
                btn.config(text=f"▸  {label}")
                is_open[0] = False

        arrow = "▾" if initially_open else "▸"
        btn = ttk.Button(btn_row, text=f"{arrow}  {label}",
                         style="Ghost.TButton", command=toggle)
        btn.pack(side=tk.LEFT)

        if initially_open:
            body.pack(fill=tk.X)

        return body, toggle, btn

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        p = ttk.Frame(self.root, padding="16 14 16 14")
        p.pack(fill=tk.BOTH, expand=True)
        self._build_rutas(p)
        self._build_formato(p)
        self._build_actions(p)
        self._build_progress(p)
        self._build_notebook(p)

    # ── Rutas ─────────────────────────────────────────────────────────────────

    def _build_rutas(self, p):
        self._sep(p, "RUTAS", pady_above=0)
        f = ttk.Frame(p)
        f.pack(fill=tk.X)
        f.columnconfigure(1, weight=1)

        self.input_var  = tk.StringVar()
        self.output_var = tk.StringVar()
        self.apikey_var = tk.StringVar()  # key del proveedor activo

        for row, (lbl, var, cmd) in enumerate([
            ("Entrada:", self.input_var,  self._browse_input),
            ("Salida:",  self.output_var, self._browse_output),
        ]):
            ttk.Label(f, text=lbl, style="Dim.TLabel").grid(
                row=row, column=0, sticky=tk.W, padx=(0, 8), pady=2)
            ttk.Entry(f, textvariable=var).grid(
                row=row, column=1, sticky=tk.EW, pady=2)
            ttk.Button(f, text="…", style="Flat.TButton", width=3,
                       command=cmd).grid(row=row, column=2, padx=(4, 0), pady=2)

        # Proveedor
        ttk.Label(f, text="Proveedor:", style="Dim.TLabel").grid(
            row=2, column=0, sticky=tk.W, padx=(0, 8), pady=2)
        self.provider_combo = ttk.Combobox(
            f, values=list(PROVIDER_OPTIONS.keys()), state="readonly", width=28)
        self.provider_combo.grid(row=2, column=1, sticky=tk.W, pady=2)
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        # API Key
        ttk.Label(f, text="API Key:", style="Dim.TLabel").grid(
            row=3, column=0, sticky=tk.W, padx=(0, 8), pady=2)
        self.apikey_status_var = tk.StringVar()
        ttk.Label(f, textvariable=self.apikey_status_var,
                  style="Dim.TLabel").grid(row=3, column=1, sticky=tk.W, pady=2)
        ttk.Button(f, text="Cambiar…", style="Flat.TButton",
                   command=self._change_apikey).grid(row=3, column=2, padx=(4, 0), pady=2)

    # ── Formato ───────────────────────────────────────────────────────────────

    def _build_formato(self, p):
        self._sep(p, "FORMATO")

        # Preset bar
        pb = ttk.Frame(p)
        pb.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(pb, text="Preset:", style="Dim.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self.preset_combo = ttk.Combobox(pb, width=34, state="readonly")
        self.preset_combo.pack(side=tk.LEFT, padx=(0, 6))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ttk.Button(pb, text="Guardar…", style="Flat.TButton",
                   command=self._save_preset).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(pb, text="✕", style="Flat.TButton", width=2,
                   command=self._delete_preset).pack(side=tk.LEFT)

        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 6))

        # Carpetas + Archivos
        g = ttk.Frame(p)
        g.pack(fill=tk.X)
        g.columnconfigure(1, weight=1)

        ttk.Label(g, text="Carpetas:", style="Dim.TLabel").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8), pady=2)
        self.struct_combo = ttk.Combobox(
            g, values=list(STRUCTURE_OPTIONS.keys()), state="readonly")
        self.struct_combo.grid(row=0, column=1, sticky=tk.EW, pady=2)
        self.struct_combo.bind("<<ComboboxSelected>>", self._on_format_change)

        self.struct_ex_var = tk.StringVar()
        ttk.Label(g, textvariable=self.struct_ex_var,
                  style="Dim.TLabel", font=("Menlo", 9)).grid(
            row=1, column=1, sticky=tk.W, pady=(0, 2))

        ttk.Label(g, text="Archivos:", style="Dim.TLabel").grid(
            row=2, column=0, sticky=tk.W, padx=(0, 8), pady=2)
        self.naming_combo = ttk.Combobox(
            g, values=list(NAMING_OPTIONS.keys()), state="readonly")
        self.naming_combo.grid(row=2, column=1, sticky=tk.EW, pady=2)
        self.naming_combo.bind("<<ComboboxSelected>>", self._on_format_change)

        # Flags row
        flags = ttk.Frame(p)
        flags.pack(fill=tk.X, pady=(6, 0))
        self.cat_label_var = tk.StringVar()
        ttk.Label(flags, textvariable=self.cat_label_var,
                  style="Dim.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(flags, text="Editar categorías…", style="Ghost.TButton",
                   command=self._open_categories_editor).pack(side=tk.LEFT, padx=(0, 16))
        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags, text="Subcarpetas",
                        variable=self.recursive_var,
                        command=self._on_format_change).pack(side=tk.LEFT, padx=(0, 12))
        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags, text="Dry run",
                        variable=self.dry_run_var).pack(side=tk.LEFT)

        self._update_categories_label()

        # ── Colapsible: Avanzado ──────────────────────────────────────────────
        adv_body, adv_toggle, adv_btn = self._collapsible(p, "Avanzado")
        self._adv_body, self._adv_toggle, self._adv_btn = adv_body, adv_toggle, adv_btn

        ar = ttk.Frame(adv_body)
        ar.pack(fill=tk.X)

        ttk.Label(ar, text="Modo:", style="Dim.TLabel").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="copy")
        ttk.Radiobutton(ar, text="Copiar", variable=self.mode_var,
                        value="copy").pack(side=tk.LEFT, padx=(4, 6))
        ttk.Radiobutton(ar, text="Mover", variable=self.mode_var,
                        value="move").pack(side=tk.LEFT, padx=(0, 14))

        ttk.Separator(ar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))

        ttk.Label(ar, text="Workers:", style="Dim.TLabel").pack(side=tk.LEFT)
        self.workers_var = tk.IntVar(value=2)
        ttk.Spinbox(ar, from_=1, to=8, width=3,
                    textvariable=self.workers_var).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(ar, text="Conf. mín:", style="Dim.TLabel").pack(side=tk.LEFT)
        self.confidence_var = tk.DoubleVar(value=0.35)
        ttk.Spinbox(ar, from_=0.0, to=1.0, increment=0.05, width=5,
                    textvariable=self.confidence_var,
                    format="%.2f").pack(side=tk.LEFT, padx=(4, 14))

        ttk.Separator(ar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))

        self.model_combo = ttk.Combobox(
            ar, values=list(MODEL_OPTIONS.keys()), state="readonly", width=26)
        self.model_combo.pack(side=tk.LEFT)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_change)
        self.model_cost_var = tk.StringVar()
        ttk.Label(ar, textvariable=self.model_cost_var,
                  style="Dim.TLabel").pack(side=tk.LEFT, padx=(8, 0))
        self._set_model("claude-3-haiku-20240307")

        # ── Colapsible: Instrucción ───────────────────────────────────────────
        instr_body, instr_toggle, instr_btn = self._collapsible(
            p, "Instrucción para Claude")
        self._instr_body, self._instr_toggle, self._instr_btn = (
            instr_body, instr_toggle, instr_btn)

        self.prompt_text = tk.Text(
            instr_body, height=2, font=("Helvetica", 10), wrap=tk.WORD,
            relief="flat", background=C["surface"], foreground=C["text"],
            insertbackground=C["text"], borderwidth=0,
            highlightthickness=1, highlightbackground=C["border"],
            highlightcolor=C["accent"],
        )
        self.prompt_text.pack(fill=tk.X)

        _ph = "Ej: Si hay niños, usa personas-grupos. Prioriza fotos de construcción."
        self.prompt_text.insert("1.0", _ph)
        self.prompt_text.config(foreground=C["muted"])

        def _in(_):
            if self.prompt_text.cget("foreground") == C["muted"]:
                self.prompt_text.delete("1.0", tk.END)
                self.prompt_text.config(foreground=C["text"])

        def _out(_):
            if not self.prompt_text.get("1.0", tk.END).strip():
                self.prompt_text.insert("1.0", _ph)
                self.prompt_text.config(foreground=C["muted"])

        self.prompt_text.bind("<FocusIn>",  _in)
        self.prompt_text.bind("<FocusOut>", _out)

        # Defaults
        self._set_structure("cat/ym")
        self._set_naming("original")
        self._refresh_preset_combo()
        # Inicializar proveedor por defecto (Anthropic)
        if PROVIDER_OPTIONS:
            self.provider_combo.set(list(PROVIDER_OPTIONS.keys())[0])
            self._on_provider_change()

    # ── Acciones — barra contextual ───────────────────────────────────────────

    def _build_actions(self, p):
        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(10, 8))

        self.btn_bar = ttk.Frame(p)
        self.btn_bar.pack(fill=tk.X, pady=(0, 4))

        # Crear todos los botones (se empaquetan dinámicamente)
        self.btn_start   = ttk.Button(self.btn_bar, text="▶  Iniciar",
                                      style="Primary.TButton", command=self._start_sorting)
        self.btn_analyze = ttk.Button(self.btn_bar, text="Analizar costo",
                                      style="Flat.TButton",   command=self._analyze_cost)
        self.btn_pause   = ttk.Button(self.btn_bar, text="⏸  Pausar",
                                      style="Pause.TButton",  command=self._toggle_pause)
        self.btn_cancel  = ttk.Button(self.btn_bar, text="✕  Cancelar",
                                      style="Danger.TButton", command=self._cancel_sorting)
        self.btn_retry   = ttk.Button(self.btn_bar, text="↺  Reintentar errores",
                                      style="Flat.TButton",   command=self._retry_errors)
        self.btn_open    = ttk.Button(self.btn_bar, text="↗  Abrir carpeta",
                                      style="Flat.TButton",   command=self._open_output_folder)
        self.btn_new     = ttk.Button(self.btn_bar, text="▶  Nueva sesión",
                                      style="Flat.TButton",   command=self._new_session)

        self._show_bar("idle")

    # ── Progreso ──────────────────────────────────────────────────────────────

    def _build_progress(self, p):
        self._sep(p, "PROGRESO", pady_above=4)

        self.progress_bar = ttk.Progressbar(
            p, mode="determinate", style="Accent.Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=(0, 8))

        row = ttk.Frame(p)
        row.pack(fill=tk.X)

        self.thumb_canvas = tk.Canvas(
            row, width=120, height=90,
            background=C["thumb"], highlightthickness=0,
        )
        self.thumb_canvas.pack(side=tk.LEFT, padx=(0, 12))
        self._draw_thumb_placeholder()

        sf = ttk.Frame(row)
        sf.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.status_lbl = ttk.Label(sf, text="Listo.", style="Dim.TLabel")
        self.status_lbl.pack(anchor=tk.W)
        self.stats_lbl  = ttk.Label(sf, text="", style="Dim.TLabel")
        self.stats_lbl.pack(anchor=tk.W)

    # ── Notebook ──────────────────────────────────────────────────────────────

    def _build_notebook(self, p):
        self._sep(p, "SALIDA", pady_above=10)

        self.result_nb = ttk.Notebook(p)
        self.result_nb.pack(fill=tk.BOTH, expand=True)

        # — Log —
        log_tab = ttk.Frame(self.result_nb)
        self.result_nb.add(log_tab, text="  Log  ")
        self.log_text = scrolledtext.ScrolledText(
            log_tab, height=7,
            font=("Menlo", 9) if sys.platform == "darwin" else ("Courier", 9),
            wrap=tk.WORD, state=tk.DISABLED, relief="flat",
            background=C["log_bg"], foreground=C["log_txt"],
            insertbackground="white",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        for tag, col in [
            ("info", C["log_txt"]), ("success", C["success"]),
            ("warning", C["warn"]),  ("error",   C["err"]),
            ("cache",   C["cache"]),
        ]:
            self.log_text.tag_configure(tag, foreground=col)

        # — Resultados —
        res_tab = ttk.Frame(self.result_nb)
        self.result_nb.add(res_tab, text="  Resultados  ")

        cols = ("archivo", "categoría", "conf", "destino", "estado")
        self.result_tree = ttk.Treeview(res_tab, columns=cols, show="headings", height=8)
        for cid, heading, width, anchor in [
            ("archivo",   "Archivo",   180, tk.W),
            ("categoría", "Categoría", 130, tk.W),
            ("conf",      "Conf.",      50, tk.CENTER),
            ("destino",   "Destino",   230, tk.W),
            ("estado",    "Estado",     80, tk.CENTER),
        ]:
            self.result_tree.heading(cid, text=heading)
            self.result_tree.column(cid, width=width, anchor=anchor, minwidth=40)

        vsb = ttk.Scrollbar(res_tab, orient=tk.VERTICAL,   command=self.result_tree.yview)
        hsb = ttk.Scrollbar(res_tab, orient=tk.HORIZONTAL, command=self.result_tree.xview)
        self.result_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.result_tree.pack(fill=tk.BOTH, expand=True)
        self.result_tree.tag_configure("ok",    background="#f0fff4")
        self.result_tree.tag_configure("cache", background="#eef6ff")
        self.result_tree.tag_configure("error", background="#fff0f0")

    # ── Helpers visuales ──────────────────────────────────────────────────────

    def _center(self, w: int, h: int):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _log(self, msg: str, level: str = "info"):
        self.log_text.config(state=tk.NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n", level)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _draw_thumb_placeholder(self):
        self.thumb_canvas.delete("all")
        self.thumb_canvas.create_text(
            60, 45, text="—", fill=C["muted"], font=("Helvetica", 20))

    def _update_title(self, cur=None, total=None):
        if cur is not None and total:
            pct = int(cur / total * 100)
            self.root.title(f"Photo Sorter  —  {cur}/{total} ({pct}%)")
        else:
            self.root.title("Photo Sorter")

    # ── Barra de botones contextual ───────────────────────────────────────────

    def _show_bar(self, state: str):
        for w in self.btn_bar.winfo_children():
            w.pack_forget()

        if state == "idle":
            self.btn_start.pack(side=tk.LEFT, padx=(0, 6))
            self.btn_analyze.pack(side=tk.LEFT)

        elif state == "running":
            self.btn_pause.pack(side=tk.LEFT, padx=(0, 6))
            self.btn_cancel.pack(side=tk.LEFT)

        elif state == "done_ok":
            self.btn_open.pack(side=tk.LEFT, padx=(0, 6))
            self.btn_new.pack(side=tk.LEFT)

        elif state == "done_errors":
            self.btn_retry.pack(side=tk.LEFT, padx=(0, 6))
            self.btn_open.pack(side=tk.LEFT, padx=(0, 6))
            self.btn_new.pack(side=tk.LEFT)

    # ── Teclado ───────────────────────────────────────────────────────────────

    def _bind_keys(self):
        self.root.bind("<Command-Return>", lambda _: self._start_sorting()
                       if not self.running else None)
        self.root.bind("<Escape>", self._handle_escape)

    def _handle_escape(self, _=None):
        if not self.running:
            return
        if self.pause_event.is_set():
            self._toggle_pause()
        else:
            self._cancel_sorting()

    # ── Proveedor / API Key ───────────────────────────────────────────────────

    def _get_provider_id(self) -> str:
        return PROVIDER_OPTIONS.get(self.provider_combo.get(), "anthropic")

    def _load_key_for_provider(self, provider_id: str) -> str:
        """Carga la API key del proveedor desde keyring o env."""
        if HAS_KEYRING:
            k = keyring.get_password(KEYRING_SERVICE, _keyring_user(provider_id))
            if k:
                return k
            # Migración automática de la key legacy de Anthropic
            if provider_id == "anthropic":
                legacy = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_LEGACY)
                if legacy:
                    keyring.set_password(KEYRING_SERVICE, _keyring_user("anthropic"), legacy)
                    return legacy
        # Fallback a variables de entorno por proveedor
        env_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "google":    "GOOGLE_API_KEY",
            "openai":    "OPENAI_API_KEY",
            "groq":      "GROQ_API_KEY",
        }
        return os.environ.get(env_map.get(provider_id, ""), "")

    def _on_provider_change(self, _=None):
        provider_id = self._get_provider_id()
        self._active_provider = provider_id
        # Cargar key del nuevo proveedor
        key = self._load_key_for_provider(provider_id)
        self.apikey_var.set(key)
        self._refresh_apikey_status()
        # Actualizar modelos disponibles
        opts = _model_options_for(provider_id)
        self.model_combo["values"] = list(opts.keys())
        default_mid = PROVIDERS.get(provider_id, {}).get("default_model", "")
        default_lbl = next((l for l, m in opts.items() if m == default_mid), "")
        if default_lbl:
            self.model_combo.set(default_lbl)
        elif opts:
            self.model_combo.set(list(opts.keys())[0])
        self._update_model_cost()
        # Mostrar URL de la key
        url = PROVIDERS.get(provider_id, {}).get("key_url", "")
        if url and not key:
            self._log(f"Consigue tu API key en: {url}", "warning")

    def _refresh_apikey_status(self):
        key = self.apikey_var.get()
        if key:
            masked = key[:8] + "••••" + key[-4:] if len(key) > 12 else "••••••••"
            src = " (Keychain)" if HAS_KEYRING else " (entorno)"
            self.apikey_status_var.set(f"{masked}{src}  ✓")
        else:
            provider_id = self._active_provider
            url = PROVIDERS.get(provider_id, {}).get("key_url", "")
            hint = f"  →  {url}" if url else ""
            self.apikey_status_var.set(f"No configurada{hint}")

    def _change_apikey(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("API Key")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        dlg.configure(bg=C["bg"])

        f = ttk.Frame(dlg, padding=16)
        f.pack(fill=tk.BOTH)
        ttk.Label(f, text="Anthropic API Key:", style="Dim.TLabel").pack(anchor=tk.W)
        entry_var = tk.StringVar(value=self.apikey_var.get())
        entry = ttk.Entry(f, textvariable=entry_var, show="•", width=48)
        entry.pack(fill=tk.X, pady=(4, 4))
        entry.focus()
        show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Mostrar", variable=show_var,
                        command=lambda: entry.config(
                            show="" if show_var.get() else "•")).pack(anchor=tk.W)
        url = PROVIDERS.get(self._active_provider, {}).get("key_url", "")
        ttk.Label(f, text=url or "—",
                  style="Dim.TLabel", font=("Helvetica", 9)).pack(anchor=tk.W, pady=(2, 10))

        btn_row = ttk.Frame(f)
        btn_row.pack(fill=tk.X)

        def _save():
            key = entry_var.get().strip()
            if not key:
                messagebox.showwarning("Vacía", "Ingresa una API Key.", parent=dlg)
                return
            self.apikey_var.set(key)
            if HAS_KEYRING:
                try:
                    keyring.set_password(
                        KEYRING_SERVICE, _keyring_user(self._active_provider), key)
                except Exception:
                    pass
            self._refresh_apikey_status()
            dlg.destroy()

        def _clear():
            if messagebox.askyesno("Borrar", "¿Eliminar la API Key?", parent=dlg):
                self.apikey_var.set("")
                if HAS_KEYRING:
                    try:
                        keyring.delete_password(
                            KEYRING_SERVICE, _keyring_user(self._active_provider))
                    except Exception:
                        pass
                self._refresh_apikey_status()
                dlg.destroy()

        ttk.Button(btn_row, text="Borrar", style="Danger.TButton",
                   command=_clear).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Cancelar", style="Flat.TButton",
                   command=dlg.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="Guardar", style="Primary.TButton",
                   command=_save).pack(side=tk.RIGHT, padx=(0, 6))
        entry.bind("<Return>", lambda _: _save())

        dlg.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()  - dlg.winfo_reqwidth())  // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")

    # ── Validación ────────────────────────────────────────────────────────────

    def _validate(self) -> bool:
        inp = self.input_var.get()
        out = self.output_var.get()
        checks = [
            (not inp,                          "Selecciona una carpeta de entrada."),
            (not Path(inp).exists(),           "La carpeta de entrada no existe."),
            (not out,                          "Selecciona una carpeta de salida."),
            (inp and out and Path(inp).resolve() == Path(out).resolve(),
             "La carpeta de entrada y salida no pueden ser la misma."),
            (not self.apikey_var.get(),
             'API Key no configurada.\nHaz clic en "Cambiar…" para ingresarla.'),
        ]
        for cond, msg in checks:
            if cond:
                messagebox.showerror("Error", msg)
                return False
        return True

    def _browse_input(self):
        path = filedialog.askdirectory(title="Carpeta de entrada")
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(
                    str(Path(path).parent / (Path(path).name + "_organizado")))

    def _browse_output(self):
        path = filedialog.askdirectory(title="Carpeta de salida")
        if path:
            self.output_var.set(path)

    # ── Estructura / naming ───────────────────────────────────────────────────

    def _get_structure_key(self) -> str:
        return STRUCTURE_OPTIONS.get(self.struct_combo.get(), "cat/ym")

    def _get_naming_key(self) -> str:
        return NAMING_OPTIONS.get(self.naming_combo.get(), "original")

    def _get_model_key(self) -> str:
        opts = _model_options_for(self._active_provider)
        return opts.get(self.model_combo.get(),
                        PROVIDERS.get(self._active_provider, {}).get(
                            "default_model", "claude-3-haiku-20240307"))

    def _get_prompt(self) -> str:
        if self.prompt_text.cget("foreground") == C["muted"]:
            return ""
        return self.prompt_text.get("1.0", tk.END).strip()

    def _set_structure(self, key: str):
        label = next((l for l, k in STRUCTURE_OPTIONS.items() if k == key),
                     "Categoría / Año-Mes")
        self.struct_combo.set(label)
        self._update_struct_example()

    def _set_naming(self, key: str):
        key = {"rename": "date_cat_n"}.get(key, key)
        label = next((l for l, k in NAMING_OPTIONS.items() if k == key), "Nombre original")
        self.naming_combo.set(label)

    def _set_model(self, key: str):
        opts = _model_options_for(self._active_provider)
        label = next((l for l, k in opts.items() if k == key), "")
        if not label and opts:
            label = list(opts.keys())[0]
        if label:
            self.model_combo.set(label)
        self._update_model_cost()

    def _update_struct_example(self):
        ex = STRUCTURE_EXAMPLES.get(self._get_structure_key(), "")
        self.struct_ex_var.set(f"→  {ex}")

    def _update_model_cost(self, *_):
        cost = MODEL_COSTS.get(self._get_model_key(), 0.0)
        self.model_cost_var.set("gratis" if cost == 0.0 else f"~${cost:.4f}/img")

    def _on_format_change(self, *_):
        self._update_struct_example()
        if not self._applying_preset:
            self.preset_combo.set(_CUSTOM_LABEL)

    def _on_model_change(self, *_):
        self._update_model_cost()

    # ── Categorías ────────────────────────────────────────────────────────────

    def _update_categories_label(self):
        if self._custom_categories is None:
            self.cat_label_var.set(f"{len(ALLOWED_CATEGORIES)} categorías")
        else:
            self.cat_label_var.set(f"{len(self._custom_categories)} categorías (custom)")

    def _open_categories_editor(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Editar categorías")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        dlg.configure(bg=C["bg"])

        current  = list(self._custom_categories if self._custom_categories is not None
                        else ALLOWED_CATEGORIES)
        list_var = tk.StringVar(value=current)

        frame = ttk.Frame(dlg, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Categorías:", style="Dim.TLabel").pack(
            anchor=tk.W, pady=(0, 4))

        lb_f = ttk.Frame(frame)
        lb_f.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        vsb = ttk.Scrollbar(lb_f)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        _mono = "Menlo" if sys.platform == "darwin" else "Courier New"
        lb = tk.Listbox(lb_f, listvariable=list_var, yscrollcommand=vsb.set,
                        selectmode=tk.SINGLE, height=14, width=38,
                        font=(_mono, 10), bg=C["surface"])
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.config(command=lb.yview)

        add_row = ttk.Frame(frame)
        add_row.pack(fill=tk.X, pady=(0, 4))
        add_var   = tk.StringVar()
        add_entry = ttk.Entry(add_row, textvariable=add_var)
        add_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        def _add():
            name = normalize_category(add_var.get().strip().lower())
            if name and name not in list(lb.get(0, tk.END)):
                lb.insert(tk.END, name)
            add_var.set("")
            add_entry.focus()

        ttk.Button(add_row, text="Agregar", style="Flat.TButton",
                   command=_add).pack(side=tk.LEFT)
        add_entry.bind("<Return>", lambda _: _add())

        ttk.Button(frame, text="Eliminar seleccionada", style="Flat.TButton",
                   command=lambda: lb.delete(lb.curselection()[0])
                   if lb.curselection() else None).pack(anchor=tk.W, pady=(0, 2))
        ttk.Button(frame, text="Restaurar predeterminadas", style="Flat.TButton",
                   command=lambda: list_var.set(ALLOWED_CATEGORIES)).pack(
            anchor=tk.W, pady=(0, 10))

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X)

        def _ok():
            cats = list(lb.get(0, tk.END))
            if not cats:
                messagebox.showwarning("Sin categorías",
                                       "Agrega al menos una.", parent=dlg)
                return
            self._custom_categories = (
                None if cats == list(ALLOWED_CATEGORIES) else cats)
            self._update_categories_label()
            dlg.destroy()

        ttk.Button(btn_row, text="Cancelar", style="Flat.TButton",
                   command=dlg.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="OK", style="Primary.TButton",
                   command=_ok).pack(side=tk.RIGHT, padx=(0, 6))

        dlg.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()  - dlg.winfo_reqwidth())  // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")

    # ── Presets ───────────────────────────────────────────────────────────────

    def _all_presets(self) -> dict:
        result = dict(BUILTIN_PRESETS)
        try:
            if PRESETS_FILE.exists():
                with open(PRESETS_FILE, encoding="utf-8") as f:
                    result.update(json.load(f))
        except Exception:
            pass
        return result

    def _user_presets(self) -> dict:
        try:
            if PRESETS_FILE.exists():
                with open(PRESETS_FILE, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _write_user_presets(self, data: dict):
        try:
            with open(PRESETS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _refresh_preset_combo(self):
        names = list(self._all_presets().keys()) + [_CUSTOM_LABEL]
        self.preset_combo["values"] = names
        if self.preset_combo.get() not in names:
            self.preset_combo.set(_CUSTOM_LABEL)

    def _on_preset_selected(self, _=None):
        name = self.preset_combo.get()
        if name == _CUSTOM_LABEL:
            return
        p = self._all_presets().get(name)
        if not p:
            return
        self._applying_preset = True
        self._set_structure(p.get("structure", "cat/ym"))
        self._set_naming(p.get("naming", "original"))
        self.recursive_var.set(p.get("recursive", False))
        self._applying_preset = False

    def _save_preset(self):
        name = simpledialog.askstring(
            "Guardar preset", "Nombre:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in BUILTIN_PRESETS:
            messagebox.showerror("Reservado",
                                 f'"{name}" es un preset integrado.')
            return
        user = self._user_presets()
        user[name] = {
            "structure": self._get_structure_key(),
            "naming":    self._get_naming_key(),
            "recursive": self.recursive_var.get(),
        }
        self._write_user_presets(user)
        self._refresh_preset_combo()
        self._applying_preset = True
        self.preset_combo.set(name)
        self._applying_preset = False
        self._log(f'Preset guardado: "{name}"', "success")

    def _delete_preset(self):
        name = self.preset_combo.get()
        if name == _CUSTOM_LABEL:
            messagebox.showinfo("Sin selección", "Selecciona un preset para eliminar.")
            return
        if name in BUILTIN_PRESETS:
            messagebox.showerror("Integrado",
                                 "Los presets integrados no se pueden eliminar.")
            return
        user = self._user_presets()
        if name not in user:
            return
        if messagebox.askyesno("Confirmar", f'¿Eliminar "{name}"?'):
            del user[name]
            self._write_user_presets(user)
            self._refresh_preset_combo()
            self._log(f'Preset eliminado: "{name}"', "warning")

    # ── Analizar costo ────────────────────────────────────────────────────────

    def _analyze_cost(self):
        if not self._validate():
            return
        images = collect_images(
            Path(self.input_var.get()), recursive=self.recursive_var.get())
        if not images:
            messagebox.showinfo("Sin imágenes", "No se encontraron imágenes.")
            return
        cached, out = 0, Path(self.output_var.get())
        try:
            cp = (out / ".photo_sorter_cache.db" if out.exists()
                  else Path(tempfile.gettempdir()) / ".photo_sorter_cache.db")
            cache = CacheDB(cp)
            cached = sum(1 for img in images if cache.get(compute_sha256(img)))
            cache.close()
        except Exception:
            pass
        model = self._get_model_key()
        cost  = estimate_cost(len(images), cached, model)
        messagebox.showinfo("Estimación de costo",
            f"Imágenes:       {len(images)}\n"
            f"En caché:       {cached}\n"
            f"A procesar:     {len(images) - cached}\n"
            f"Modelo:         {self.model_combo.get().split('  ')[0]}\n"
            f"────────────────\n"
            f"Costo estimado: ${cost:.3f} USD")
        self._log(f"Análisis: {len(images)} imgs | caché: {cached} | ~${cost:.3f} USD")

    # ── Control del proceso ───────────────────────────────────────────────────

    def _start_sorting(self):
        if self.running or not self._validate():
            return
        self._save_config()
        self.results            = []
        self._images_to_process = None
        self._existing_results  = []
        self._completed         = False
        self.cancel_event.clear()
        self.pause_event.clear()
        self.progress_bar.config(value=0, maximum=100)
        self.status_lbl.config(text="Iniciando…")
        self.stats_lbl.config(text="")
        self._draw_thumb_placeholder()
        self.running = True
        self._show_bar("running")
        self._update_title()
        mode_str = "DRY-RUN" if self.dry_run_var.get() else self.mode_var.get().upper()
        self._log("━" * 48)
        self._log(f"Modo: {mode_str}  ·  {self.struct_combo.get()}")
        self._log(f"Archivos: {self.naming_combo.get()}")
        self._log(f"Modelo: {self.model_combo.get().split('  ')[0]}")
        threading.Thread(target=self._worker_thread, daemon=True).start()
        self.root.after(100, self._poll_queue)

    def _toggle_pause(self):
        if not self.running:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.btn_pause.config(text="⏸  Pausar")
            self._log("Reanudado.", "info")
        else:
            self.pause_event.set()
            self.btn_pause.config(text="▶  Continuar")
            self._log("Pausado.  Esc para reanudar.", "warning")

    def _cancel_sorting(self):
        if self.running:
            self.cancel_event.set()
            self.pause_event.clear()
            self._log("Cancelando…", "warning")
            self.btn_cancel.config(state=tk.DISABLED)
            self.btn_pause.config(state=tk.DISABLED)

    def _new_session(self):
        self.results            = []
        self._existing_results  = []
        self._completed         = False
        self.progress_bar.config(value=0, maximum=100)
        self.status_lbl.config(text="Listo.")
        self.stats_lbl.config(text="")
        self._draw_thumb_placeholder()
        self._show_bar("idle")
        self._update_title()

    def _retry_errors(self):
        error_results = [r for r in self.results if r.get("error")]
        if not error_results:
            return
        error_paths = [
            Path(r["source_path"]) for r in error_results
            if Path(r.get("source_path", "")).exists()
        ]
        if not error_paths:
            messagebox.showinfo("Sin archivos",
                "Los archivos con error ya no existen en el origen.")
            return
        if not messagebox.askyesno(
                "Reintentar", f"¿Reprocesar {len(error_paths)} imagen(es)?"):
            return
        self._save_config()
        self._images_to_process = error_paths
        self._existing_results  = [r for r in self.results if not r.get("error")]
        self._completed         = False
        self.cancel_event.clear()
        self.pause_event.clear()
        self.progress_bar.config(value=0, maximum=100)
        self.status_lbl.config(text="Reintentando errores…")
        self.stats_lbl.config(text="")
        self._draw_thumb_placeholder()
        self.running = True
        self._show_bar("running")
        self._log("━" * 48)
        self._log(f"Reintentando {len(error_paths)} imagen(es) con error…", "warning")
        threading.Thread(target=self._worker_thread, daemon=True).start()
        self.root.after(100, self._poll_queue)

    def _open_output_folder(self):
        output = self.output_var.get()
        p = Path(output).resolve() if output else None
        if not p or not p.is_dir():
            messagebox.showwarning("Sin carpeta", "La carpeta de salida no existe aún.")
            return
        out_str = str(p)
        if sys.platform == "darwin":
            subprocess.run(["open", out_str])
        elif sys.platform == "win32":
            subprocess.run(["explorer", out_str])
        else:
            subprocess.run(["xdg-open", out_str])

    # ── Thumbnail ─────────────────────────────────────────────────────────────

    def _update_thumbnail(self, path_str: str):
        if not HAS_PIL_UI:
            return
        try:
            img   = Image.open(path_str)
            img.thumbnail((120, 90), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._last_thumbnail = photo
            self.thumb_canvas.delete("all")
            self.thumb_canvas.create_image(60, 45, image=photo, anchor=tk.CENTER)
        except Exception:
            pass

    # ── Worker ────────────────────────────────────────────────────────────────

    def _worker_thread(self):
        try:
            asyncio.run(self._async_main())
        except Exception as e:
            self.msg_queue.put(("error", str(e)))
        finally:
            self.msg_queue.put(("done", None))

    async def _async_main(self):
        input_dir    = Path(self.input_var.get())
        output_dir   = Path(self.output_var.get())
        extra_prompt = self._get_prompt()

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.msg_queue.put(("error", f"No se pudo crear carpeta de salida:\n{e}"))
            return

        images = (self._images_to_process
                  if self._images_to_process is not None
                  else collect_images(input_dir, recursive=self.recursive_var.get()))

        if not images:
            self.msg_queue.put(("log", ("No se encontraron imágenes.", "warning")))
            return

        self.msg_queue.put(("total", len(images)))
        self.msg_queue.put(("log", (f"Imágenes: {len(images)}", "info")))

        if extra_prompt:
            preview = extra_prompt[:55] + ("…" if len(extra_prompt) > 55 else "")
            self.msg_queue.put(("log", (f'Instrucción: "{preview}"', "cache")))

        cache    = CacheDB(output_dir / ".photo_sorter_cache.db")
        cached_n = sum(1 for img in images if cache.get(compute_sha256(img)))
        model    = self._get_model_key()
        cost     = estimate_cost(len(images), cached_n, model)
        self.msg_queue.put(("log", (
            f"Caché: {cached_n}  ·  A procesar: {len(images)-cached_n}  ·  ~${cost:.3f}",
            "info",
        )))

        def on_progress(cur, total, img_path, fname, cat, from_cache):
            self.msg_queue.put(
                ("progress", (cur, total, str(img_path), fname, cat, from_cache)))

        new_results = await process_images(
            images=images, output_dir=output_dir, cache=cache,
            api_key=self.apikey_var.get(), provider=self._active_provider,
            mode=self.mode_var.get(), min_confidence=self.confidence_var.get(),
            max_categories=20, structure=self._get_structure_key(),
            unknown_date_name="unknown-date", delay=0.1,
            workers=self.workers_var.get(), dry_run=self.dry_run_var.get(),
            naming_style=self._get_naming_key(),
            progress_callback=on_progress,
            cancel_event=self.cancel_event,
            pause_event=self.pause_event,
            extra_prompt=extra_prompt,
            model=model,
            allowed_categories=self._custom_categories,
        )

        cache.close()
        all_results   = self._existing_results + new_results
        self.results  = all_results
        if all_results:
            generate_report(all_results, output_dir)
        self.msg_queue.put(("complete", all_results))

    # ── Queue poll ────────────────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                self._handle(self.msg_queue.get_nowait())
        except queue.Empty:
            pass
        if self.running:
            self.root.after(100, self._poll_queue)

    def _handle(self, msg: tuple):
        kind, data = msg

        if kind == "total":
            self.progress_bar.config(maximum=data, value=0)

        elif kind == "progress":
            cur, total, img_path_str, fname, cat, from_cache = data
            self.progress_bar["value"] = cur
            pct     = int(cur / total * 100) if total else 0
            src     = "caché" if from_cache else "API"
            cat_str = f" → {cat}" if cat else ""
            self.status_lbl.config(text=f"[{src}]  {fname}{cat_str}")
            self.stats_lbl.config(text=f"{cur} / {total}  ({pct}%)")
            self._log(f"[{cur}/{total}] [{src}] {fname}{cat_str}",
                      "cache" if from_cache else "success")
            self._update_thumbnail(img_path_str)
            self._update_title(cur, total)

        elif kind == "log":
            self._log(*data)

        elif kind == "error":
            self._log(f"ERROR: {data}", "error")
            messagebox.showerror("Error", data)

        elif kind == "complete":
            self._on_complete(data)

        elif kind == "done":
            self.running = False
            if not self._completed:   # cancelado o error antes de completar
                self._show_bar("idle")
                self._update_title()

    def _on_complete(self, results: list[dict]):
        self._completed = True

        if not results:
            self.status_lbl.config(text="Completado — sin resultados.")
            self._show_bar("idle")
            return

        ok        = sum(1 for r in results if not r.get("error"))
        errors    = sum(1 for r in results if r.get("error"))
        cached    = sum(1 for r in results if r.get("from_cache"))
        cancelled = self.cancel_event.is_set()
        tag       = "Cancelado" if cancelled else "✓ Completado"

        self.progress_bar["value"] = self.progress_bar["maximum"]
        self.status_lbl.config(text=f"{tag}  —  {ok} imágenes organizadas")
        self.stats_lbl.config(text=f"Errores: {errors}  ·  Caché: {cached}")

        self._log("━" * 48)
        self._log(f"{tag}: {ok}/{len(results)}  errores: {errors}  caché: {cached}",
                  "warning" if cancelled else "success")

        cats: dict[str, int] = {}
        for r in results:
            if c := r.get("category"):
                cats[c] = cats.get(c, 0) + 1
        if cats:
            self._log("Distribución:")
            mx = max(cats.values())
            for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
                bar = "█" * max(1, round(n / mx * 16))
                self._log(f"  {cat:<26} {bar} {n}", "cache")

        for r in [r for r in results if r.get("error")][:8]:
            self._log(f"  {r['filename']}: {r['error']}", "error")

        self._populate_treeview(results)
        if not cancelled:
            self.result_nb.select(1)

        self._show_bar("done_errors" if (errors and not cancelled) else
                       "done_ok"     if not cancelled else "idle")
        self._update_title()

    def _populate_treeview(self, results: list[dict]):
        for row in self.result_tree.get_children():
            self.result_tree.delete(row)
        for r in results:
            conf = f"{r.get('confidence', 0):.0%}" if r.get("confidence") else "—"
            dest = r.get("destination") or "—"
            if dest != "—":
                parts = Path(dest).parts
                dest  = str(Path(*parts[-3:])) if len(parts) >= 3 else dest
            if r.get("error"):
                tag, status, cat = "error", "Error", "—"
            elif r.get("from_cache"):
                tag, status, cat = "cache", "Caché", r.get("category") or "—"
            else:
                tag, status, cat = "ok",    "OK",    r.get("category") or "—"
            self.result_tree.insert("", tk.END, values=(
                r.get("filename", "?"), cat, conf, dest, status,
            ), tags=(tag,))

    # ── Persistencia ──────────────────────────────────────────────────────────

    def _save_config(self):
        key = self.apikey_var.get().strip()
        if key and HAS_KEYRING:
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            except Exception:
                pass
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "input":             self.input_var.get(),
                    "output":            self.output_var.get(),
                    "mode":              self.mode_var.get(),
                    "workers":           self.workers_var.get(),
                    "confidence":        self.confidence_var.get(),
                    "dry_run":           self.dry_run_var.get(),
                    "structure":         self._get_structure_key(),
                    "naming":            self._get_naming_key(),
                    "model":             self._get_model_key(),
                    "provider":          self._active_provider,
                    "recursive":         self.recursive_var.get(),
                    "extra_prompt":      self._get_prompt(),
                    "last_preset":       self.preset_combo.get(),
                    "custom_categories": self._custom_categories,
                    "adv_open":          self._adv_open[0],
                }, f, indent=2)
        except Exception:
            pass

    def _load_config(self):
        try:
            if not CONFIG_FILE.exists():
                return
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)

            self.input_var.set(cfg.get("input", ""))
            self.output_var.set(cfg.get("output", ""))
            self.mode_var.set(cfg.get("mode", "copy"))
            self.workers_var.set(cfg.get("workers", 2))
            self.confidence_var.set(cfg.get("confidence", 0.35))
            self.dry_run_var.set(cfg.get("dry_run", False))
            self.recursive_var.set(cfg.get("recursive", False))

            struct_key = cfg.get("structure", "cat/ym")
            struct_key = {"%Y-%m": "cat/ym", "%Y": "cat/y",
                          "": "cat"}.get(struct_key, struct_key)

            self._applying_preset = True
            self._set_structure(struct_key)
            self._set_naming(cfg.get("naming", "original"))
            self._applying_preset = False

            # Restaurar proveedor (dispara _on_provider_change que carga key + modelos)
            saved_provider = cfg.get("provider", "anthropic")
            provider_label = next(
                (l for l, pid in PROVIDER_OPTIONS.items() if pid == saved_provider),
                list(PROVIDER_OPTIONS.keys())[0] if PROVIDER_OPTIONS else ""
            )
            if provider_label:
                self.provider_combo.set(provider_label)
                self._on_provider_change()

            self._set_model(cfg.get("model", PROVIDERS.get(
                self._active_provider, {}).get("default_model", "")))

            last = cfg.get("last_preset", _CUSTOM_LABEL)
            if last in self.preset_combo["values"]:
                self._applying_preset = True
                self.preset_combo.set(last)
                self._applying_preset = False

            saved_prompt = cfg.get("extra_prompt", "")
            if saved_prompt:
                self._instr_toggle(open_to=True)
                self.prompt_text.delete("1.0", tk.END)
                self.prompt_text.insert("1.0", saved_prompt)
                self.prompt_text.config(foreground=C["text"])

            custom_cats = cfg.get("custom_categories")
            if isinstance(custom_cats, list) and custom_cats:
                self._custom_categories = custom_cats
                self._update_categories_label()

            if cfg.get("adv_open", False):
                self._adv_toggle(open_to=True)

        except Exception:
            pass

    def _on_close(self):
        if self.running:
            if messagebox.askyesno("Proceso activo", "¿Cancelar y salir?"):
                self.cancel_event.set()
                self.pause_event.clear()
                self.root.destroy()
        else:
            self._save_config()
            self.root.destroy()


# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not HAS_CORE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Error",
            f"No se pudo importar photo_sorter.py:\n\n{IMPORT_ERROR}\n\n"
            "Ejecuta desde el directorio del proyecto con el venv activado.")
        root.destroy()
        return

    root = tk.Tk()
    PhotoSorterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
