#!/usr/bin/env python3
"""
Photo Sorter UI — Interfaz gráfica para organizar fotos con Claude Vision API.
Ejecutar: python photo_sorter_ui.py
"""

import asyncio
import json
import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

try:
    from photo_sorter import (
        CacheDB, collect_images, compute_sha256,
        estimate_cost, generate_report, process_images,
    )
    import anthropic
    HAS_CORE = True
    IMPORT_ERROR = ""
except ImportError as e:
    HAS_CORE = False
    IMPORT_ERROR = str(e)

# ── Persistencia ─────────────────────────────────────────────────────────────
CONFIG_FILE  = Path(__file__).parent / ".ui_config.json"
PRESETS_FILE = Path(__file__).parent / ".presets.json"

# ── Opciones de estructura: label → clave interna ────────────────────────────
STRUCTURE_OPTIONS: dict[str, str] = {
    "Una sola carpeta":            "flat",
    "Por categoría":               "cat",
    "Categoría / Año-Mes":         "cat/ym",
    "Categoría / Año":             "cat/y",
    "Categoría / Año / Mes":       "cat/y/m",
    "Año-Mes / Categoría":         "ym/cat",
    "Año / Categoría":             "y/cat",
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

# ── Opciones de nombrado: label → clave interna ───────────────────────────────
NAMING_OPTIONS: dict[str, str] = {
    "Nombre original":                              "original",
    "Fecha + categoría  —  20240315_perro_01.jpg":  "date_cat_n",
    "Fecha + número  —  20240315_001.jpg":          "date_n",
    "Fecha + nombre original  —  20240315_IMG_0542.jpg": "date_orig",
    "Fecha + hora  —  20240315_143022.jpg":         "ymd_time",
    "Categoría + número  —  perro_001.jpg":         "cat_n",
    "Categoría + fecha  —  perro_20240315_01.jpg":  "cat_date_n",
    "Año-mes + número  —  2024-03_001.jpg":         "ym_n",
    "Solo número global  —  0001.jpg":              "n",
}

# ── Presets integrados (usan claves internas) ─────────────────────────────────
BUILTIN_PRESETS: dict[str, dict] = {
    "Estándar  (cat/mes, nombre original)": {
        "structure": "cat/ym",  "naming": "original",   "recursive": False,
    },
    "Renombrado completo  (cat/mes, fecha+cat)": {
        "structure": "cat/ym",  "naming": "date_cat_n", "recursive": False,
    },
    "Archivo anual  (cat/año, fecha+cat, recursivo)": {
        "structure": "cat/y",   "naming": "date_cat_n", "recursive": True,
    },
    "Orden cronológico  (año-mes/cat, fecha+nº)": {
        "structure": "ym/cat",  "naming": "date_n",     "recursive": False,
    },
    "Una sola carpeta  (todo junto, solo nº)": {
        "structure": "flat",    "naming": "n",          "recursive": False,
    },
    "Solo categorías  (sin fechas, nombre original)": {
        "structure": "cat",     "naming": "original",   "recursive": False,
    },
    "Fotos personales  (cat/mes, fecha+nombre, recursivo)": {
        "structure": "cat/ym",  "naming": "date_orig",  "recursive": True,
    },
}
_CUSTOM_LABEL = "─── personalizado ───"

# ── Paleta ───────────────────────────────────────────────────────────────────
C = {
    "bg":      "#efefef",  "surface": "#ffffff",  "border":  "#d8d8d8",
    "accent":  "#5b6af0",  "text":    "#1a1a1a",  "muted":   "#777777",
    "log_bg":  "#1a1b26",  "log_txt": "#c0caf5",
    "success": "#9ece6a",  "warn":    "#e0af68",
    "err":     "#f7768e",  "cache":   "#7dcfff",
}

# ─────────────────────────────────────────────────────────────────────────────

class PhotoSorterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Photo Sorter")
        self.root.configure(bg=C["bg"])
        self.root.minsize(640, 660)
        self.root.resizable(True, True)

        self.running          = False
        self.cancel_event     = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self.results: list[dict]    = []
        self._applying_preset = False

        self._setup_styles()
        self._build_ui()
        self._load_config()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._center(700, 740)

    # ── Estilos ───────────────────────────────────────────────────────────────

    def _setup_styles(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        base = {"background": C["bg"], "foreground": C["text"], "font": ("Helvetica", 10)}
        s.configure(".", **base)
        for w in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton"):
            s.configure(w, background=C["bg"])
        s.configure("Dim.TLabel",   background=C["bg"], foreground=C["muted"])
        s.configure("TSeparator",   background=C["border"])
        s.configure("TLabelframe",  background=C["bg"], bordercolor=C["border"])
        s.configure("TLabelframe.Label",
                    background=C["bg"], foreground=C["muted"],
                    font=("Helvetica", 9, "bold"))
        s.configure("TEntry",
                    fieldbackground=C["surface"], bordercolor=C["border"],
                    lightcolor=C["border"], darkcolor=C["border"])
        s.configure("TCombobox",
                    fieldbackground=C["surface"], bordercolor=C["border"],
                    selectbackground=C["accent"], selectforeground="white")
        s.map("TCombobox", fieldbackground=[("readonly", C["surface"])])
        s.configure("Primary.TButton",
                    background=C["accent"], foreground="white",
                    font=("Helvetica", 10, "bold"), padding=(14, 5), relief="flat")
        s.map("Primary.TButton",
              background=[("active", "#4a59e0"), ("disabled", "#b0b0b0")],
              foreground=[("disabled", "#e0e0e0")])
        s.configure("Flat.TButton",
                    background=C["surface"], foreground=C["text"],
                    font=("Helvetica", 10), padding=(9, 4), relief="flat")
        s.map("Flat.TButton",
              background=[("active", C["border"]), ("disabled", C["bg"])])
        s.configure("Danger.TButton",
                    background="#fff0f0", foreground="#c62828",
                    font=("Helvetica", 10), padding=(9, 4), relief="flat")
        s.map("Danger.TButton",
              background=[("active", "#ffd7d7"), ("disabled", C["bg"])])
        s.configure("Accent.Horizontal.TProgressbar",
                    troughcolor=C["border"], background=C["accent"], thickness=8)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        p = ttk.Frame(self.root, padding="14 12 14 12")
        p.pack(fill=tk.BOTH, expand=True)
        self._build_header(p)
        self._build_config(p)
        self._build_format(p)
        self._build_execution(p)
        self._build_prompt(p)
        self._build_actions(p)
        self._build_progress(p)
        self._build_log(p)

    def _build_header(self, p):
        h = ttk.Frame(p)
        h.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(h, text="Photo Sorter",
                  font=("Helvetica", 17, "bold"),
                  foreground=C["accent"]).pack(side=tk.LEFT)
        ttk.Label(h, text="  Organiza fotos con Claude Vision API",
                  style="Dim.TLabel", font=("Helvetica", 10)).pack(side=tk.LEFT, pady=(4, 0))
        ttk.Separator(p, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 8))

    def _build_config(self, p):
        f = ttk.LabelFrame(p, text="CONFIGURACIÓN", padding="8 5 8 5")
        f.pack(fill=tk.X, pady=(0, 5))
        f.columnconfigure(1, weight=1)

        self.input_var  = tk.StringVar()
        self.output_var = tk.StringVar()
        self.apikey_var = tk.StringVar(value=os.environ.get("ANTHROPIC_API_KEY", ""))

        for row, (lbl, var, cmd) in enumerate([
            ("Entrada:", self.input_var,  self._browse_input),
            ("Salida:",  self.output_var, self._browse_output),
        ]):
            ttk.Label(f, text=lbl).grid(row=row, column=0, sticky=tk.W, padx=(0, 6), pady=2)
            ttk.Entry(f, textvariable=var).grid(row=row, column=1, sticky=tk.EW, pady=2)
            ttk.Button(f, text="…", style="Flat.TButton", width=3,
                       command=cmd).grid(row=row, column=2, padx=(4, 0), pady=2)

        ttk.Label(f, text="API Key:").grid(row=2, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        self.apikey_entry = ttk.Entry(f, textvariable=self.apikey_var, show="•")
        self.apikey_entry.grid(row=2, column=1, sticky=tk.EW, pady=2)
        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="ver", variable=self.show_key_var,
                        command=lambda: self.apikey_entry.config(
                            show="" if self.show_key_var.get() else "•"
                        )).grid(row=2, column=2, padx=(4, 0))

    # ── Formato de organización ───────────────────────────────────────────────

    def _build_format(self, p):
        f = ttk.LabelFrame(p, text="FORMATO DE ORGANIZACIÓN", padding="8 6 8 8")
        f.pack(fill=tk.X, pady=(0, 5))
        f.columnconfigure(1, weight=1)

        # — Preset bar —
        pb = ttk.Frame(f)
        pb.pack(fill=tk.X, pady=(0, 7))

        ttk.Label(pb, text="Preset:", style="Dim.TLabel").pack(side=tk.LEFT, padx=(0, 5))
        self.preset_combo = ttk.Combobox(pb, width=36, state="readonly")
        self.preset_combo.pack(side=tk.LEFT, padx=(0, 6))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ttk.Button(pb, text="Guardar…", style="Flat.TButton",
                   command=self._save_preset).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(pb, text="Eliminar", style="Flat.TButton",
                   command=self._delete_preset).pack(side=tk.LEFT)

        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 7))

        # — Carpetas —
        g = ttk.Frame(f)
        g.pack(fill=tk.X, pady=(0, 2))
        g.columnconfigure(1, weight=1)

        ttk.Label(g, text="Carpetas:", style="Dim.TLabel").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.struct_combo = ttk.Combobox(
            g, values=list(STRUCTURE_OPTIONS.keys()), state="readonly")
        self.struct_combo.grid(row=0, column=1, sticky=tk.EW)
        self.struct_combo.bind("<<ComboboxSelected>>", self._on_format_change)

        self.struct_ex_var = tk.StringVar()
        ttk.Label(g, textvariable=self.struct_ex_var,
                  style="Dim.TLabel", font=("Menlo", 9)).grid(
            row=1, column=1, sticky=tk.W, pady=(2, 0))

        # — Archivos —
        g2 = ttk.Frame(f)
        g2.pack(fill=tk.X, pady=(5, 0))
        g2.columnconfigure(1, weight=1)

        ttk.Label(g2, text="Archivos:", style="Dim.TLabel").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.naming_combo = ttk.Combobox(
            g2, values=list(NAMING_OPTIONS.keys()), state="readonly")
        self.naming_combo.grid(row=0, column=1, sticky=tk.EW)
        self.naming_combo.bind("<<ComboboxSelected>>", self._on_format_change)

        # — Flags —
        r3 = ttk.Frame(f)
        r3.pack(fill=tk.X, pady=(8, 0))
        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r3, text="Buscar en subcarpetas",
                        variable=self.recursive_var,
                        command=self._on_format_change).pack(side=tk.LEFT, padx=(0, 20))
        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r3, text="Dry Run  (simular sin mover archivos)",
                        variable=self.dry_run_var).pack(side=tk.LEFT)

        # Defaults
        self._set_structure("cat/ym")
        self._set_naming("original")
        self._refresh_preset_combo()

    # ── Ejecución ─────────────────────────────────────────────────────────────

    def _build_execution(self, p):
        f = ttk.LabelFrame(p, text="EJECUCIÓN", padding="8 5 8 5")
        f.pack(fill=tk.X, pady=(0, 5))
        r = ttk.Frame(f)
        r.pack(fill=tk.X)

        ttk.Label(r, text="Modo:").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="copy")
        ttk.Radiobutton(r, text="Copiar", variable=self.mode_var,
                        value="copy").pack(side=tk.LEFT, padx=(4, 8))
        ttk.Radiobutton(r, text="Mover", variable=self.mode_var,
                        value="move").pack(side=tk.LEFT, padx=(0, 18))
        ttk.Separator(r, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 14))
        ttk.Label(r, text="Workers:").pack(side=tk.LEFT)
        self.workers_var = tk.IntVar(value=2)
        ttk.Spinbox(r, from_=1, to=8, width=3,
                    textvariable=self.workers_var).pack(side=tk.LEFT, padx=(4, 18))
        ttk.Label(r, text="Confianza mín:").pack(side=tk.LEFT)
        self.confidence_var = tk.DoubleVar(value=0.35)
        ttk.Spinbox(r, from_=0.0, to=1.0, increment=0.05, width=5,
                    textvariable=self.confidence_var,
                    format="%.2f").pack(side=tk.LEFT, padx=(4, 0))

    # ── Instrucciones ─────────────────────────────────────────────────────────

    def _build_prompt(self, p):
        f = ttk.LabelFrame(p, text="INSTRUCCIONES PARA CLAUDE  (opcional)", padding="8 5 8 5")
        f.pack(fill=tk.X, pady=(0, 5))

        self.prompt_text = tk.Text(
            f, height=2, font=("Helvetica", 10), wrap=tk.WORD,
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

    def _get_prompt(self) -> str:
        if self.prompt_text.cget("foreground") == C["muted"]:
            return ""
        return self.prompt_text.get("1.0", tk.END).strip()

    # ── Acciones ──────────────────────────────────────────────────────────────

    def _build_actions(self, p):
        f = ttk.Frame(p)
        f.pack(fill=tk.X, pady=(0, 6))
        self.btn_analyze = ttk.Button(f, text="Analizar costo", style="Flat.TButton",
                                      command=self._analyze_cost)
        self.btn_analyze.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_start = ttk.Button(f, text="▶  Iniciar", style="Primary.TButton",
                                    command=self._start_sorting)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_cancel = ttk.Button(f, text="✕  Cancelar", style="Danger.TButton",
                                     command=self._cancel_sorting, state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.LEFT)

    # ── Progreso ──────────────────────────────────────────────────────────────

    def _build_progress(self, p):
        f = ttk.LabelFrame(p, text="PROGRESO", padding="8 5 8 5")
        f.pack(fill=tk.X, pady=(0, 5))
        self.progress_bar = ttk.Progressbar(
            f, mode="determinate", style="Accent.Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=(0, 4))
        self.status_lbl = ttk.Label(f, text="Listo.", style="Dim.TLabel")
        self.status_lbl.pack(anchor=tk.W)
        self.stats_lbl  = ttk.Label(f, text="", style="Dim.TLabel")
        self.stats_lbl.pack(anchor=tk.W)

    # ── Log ───────────────────────────────────────────────────────────────────

    def _build_log(self, p):
        f = ttk.LabelFrame(p, text="LOG", padding="8 5 8 5")
        f.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(
            f, height=7,
            font=("Menlo", 9) if os.uname().sysname == "Darwin" else ("Courier", 9),
            wrap=tk.WORD, state=tk.DISABLED, relief="flat",
            background=C["log_bg"], foreground=C["log_txt"], insertbackground="white",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        for tag, col in [("info", C["log_txt"]), ("success", C["success"]),
                          ("warning", C["warn"]), ("error", C["err"]), ("cache", C["cache"])]:
            self.log_text.tag_configure(tag, foreground=col)

    # ── Helpers ───────────────────────────────────────────────────────────────

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

    def _validate(self) -> bool:
        checks = [
            (not self.input_var.get(),                     "Selecciona una carpeta de entrada."),
            (not Path(self.input_var.get()).exists(),       "La carpeta de entrada no existe."),
            (not self.output_var.get(),                    "Selecciona una carpeta de salida."),
            (not self.apikey_var.get(),                    "Ingresa tu API Key de Anthropic."),
        ]
        for cond, msg in checks:
            if cond:
                messagebox.showerror("Error", msg); return False
        return True

    def _set_running(self, state: bool):
        self.running = state
        s = tk.DISABLED if state else tk.NORMAL
        self.btn_start.config(state=s)
        self.btn_analyze.config(state=s)
        self.btn_cancel.config(state=tk.NORMAL if state else tk.DISABLED)

    def _browse_input(self):
        path = filedialog.askdirectory(title="Carpeta de entrada")
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(str(Path(path).parent / (Path(path).name + "_organizado")))

    def _browse_output(self):
        path = filedialog.askdirectory(title="Carpeta de salida")
        if path:
            self.output_var.set(path)

    # ── Estructura / naming helpers ───────────────────────────────────────────

    def _get_structure_key(self) -> str:
        return STRUCTURE_OPTIONS.get(self.struct_combo.get(), "cat/ym")

    def _get_naming_key(self) -> str:
        return NAMING_OPTIONS.get(self.naming_combo.get(), "original")

    def _set_structure(self, key: str):
        label = next((l for l, k in STRUCTURE_OPTIONS.items() if k == key), "Categoría / Año-Mes")
        self.struct_combo.set(label)
        self._update_struct_example()

    def _set_naming(self, key: str):
        # migrar claves antiguas
        old_map = {"rename": "date_cat_n", "original": "original"}
        key = old_map.get(key, key)
        label = next((l for l, k in NAMING_OPTIONS.items() if k == key), "Nombre original")
        self.naming_combo.set(label)

    def _update_struct_example(self):
        ex = STRUCTURE_EXAMPLES.get(self._get_structure_key(), "")
        self.struct_ex_var.set(f"→  {ex}")

    def _on_format_change(self, *_):
        self._update_struct_example()
        if not self._applying_preset:
            self.preset_combo.set(_CUSTOM_LABEL)

    # ── Gestión de presets ────────────────────────────────────────────────────

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
            "Guardar preset", "Nombre para este preset:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in BUILTIN_PRESETS:
            messagebox.showerror("Reservado",
                f'"{name}" es un preset integrado.\nElige otro nombre.')
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
            messagebox.showerror("Integrado", "Los presets integrados no se pueden eliminar.")
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
        images = collect_images(Path(self.input_var.get()), recursive=self.recursive_var.get())
        if not images:
            messagebox.showinfo("Sin imágenes", "No se encontraron imágenes."); return

        cached, out = 0, Path(self.output_var.get())
        try:
            cp = out / ".photo_sorter_cache.db" if out.exists() else Path("/tmp/.photo_sorter_cache.db")
            cache = CacheDB(cp)
            cached = sum(1 for img in images if cache.get(compute_sha256(img)))
            cache.close()
        except Exception:
            pass

        cost = estimate_cost(len(images), cached)
        messagebox.showinfo("Estimación de costo",
            f"Imágenes:       {len(images)}\n"
            f"En caché:       {cached}\n"
            f"A procesar:     {len(images) - cached}\n"
            f"────────────────\n"
            f"Costo estimado: ${cost:.3f} USD")
        self._log(f"Análisis: {len(images)} imgs | caché: {cached} | ~${cost:.3f} USD")

    # ── Iniciar / cancelar ────────────────────────────────────────────────────

    def _start_sorting(self):
        if not self._validate():
            return
        self._save_config()
        os.environ["ANTHROPIC_API_KEY"] = self.apikey_var.get()
        self.results = []
        self.cancel_event.clear()
        self.progress_bar.config(value=0, maximum=100)
        self.status_lbl.config(text="Iniciando…")
        self.stats_lbl.config(text="")
        self._set_running(True)
        mode_str = "DRY-RUN" if self.dry_run_var.get() else self.mode_var.get().upper()
        self._log("━" * 50)
        self._log(f"Modo: {mode_str}  |  Estructura: {self.struct_combo.get()}")
        self._log(f"Archivos: {self.naming_combo.get()}")
        threading.Thread(target=self._worker_thread, daemon=True).start()
        self.root.after(100, self._poll_queue)

    def _cancel_sorting(self):
        if self.running:
            self.cancel_event.set()
            self._log("Cancelando…", "warning")
            self.btn_cancel.config(state=tk.DISABLED)

    # ── Worker ────────────────────────────────────────────────────────────────

    def _worker_thread(self):
        try:
            asyncio.run(self._async_main())
        except Exception as e:
            self.msg_queue.put(("error", str(e)))
        finally:
            self.msg_queue.put(("done", None))

    async def _async_main(self):
        input_dir  = Path(self.input_var.get())
        output_dir = Path(self.output_var.get())
        extra_prompt = self._get_prompt()

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.msg_queue.put(("error", f"No se pudo crear carpeta de salida:\n{e}")); return

        images = collect_images(input_dir, recursive=self.recursive_var.get())
        if not images:
            self.msg_queue.put(("log", ("No se encontraron imágenes.", "warning"))); return

        self.msg_queue.put(("total", len(images)))
        self.msg_queue.put(("log", (f"Imágenes: {len(images)}", "info")))

        if extra_prompt:
            preview = extra_prompt[:55] + ("…" if len(extra_prompt) > 55 else "")
            self.msg_queue.put(("log", (f'Prompt: "{preview}"', "cache")))

        cache = CacheDB(output_dir / ".photo_sorter_cache.db")
        cached_n = sum(1 for img in images if cache.get(compute_sha256(img)))
        cost = estimate_cost(len(images), cached_n)
        self.msg_queue.put(("log", (
            f"Caché: {cached_n}  |  A procesar: {len(images)-cached_n}  |  ~${cost:.3f}", "info"
        )))

        client = anthropic.Anthropic(api_key=self.apikey_var.get())

        def on_progress(cur, total, fname, cat, from_cache):
            self.msg_queue.put(("progress", (cur, total, fname, cat, from_cache)))

        self.results = await process_images(
            images=images, output_dir=output_dir, cache=cache, client=client,
            mode=self.mode_var.get(), min_confidence=self.confidence_var.get(),
            max_categories=20, structure=self._get_structure_key(),
            unknown_date_name="unknown-date", delay=0.1,
            workers=self.workers_var.get(), dry_run=self.dry_run_var.get(),
            naming_style=self._get_naming_key(),
            progress_callback=on_progress, cancel_event=self.cancel_event,
            extra_prompt=extra_prompt,
        )

        cache.close()
        if self.results:
            generate_report(self.results, output_dir)
        self.msg_queue.put(("complete", self.results))

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
            cur, total, fname, cat, from_cache = data
            self.progress_bar["value"] = cur
            pct = int(cur / total * 100) if total else 0
            src = "caché" if from_cache else "API"
            cat_str = f" → {cat}" if cat else ""
            self.status_lbl.config(text=f"[{src}] {fname}{cat_str}")
            self.stats_lbl.config(text=f"{cur} / {total}  ({pct}%)")
            self._log(f"[{cur}/{total}] [{src}] {fname}{cat_str}",
                      "cache" if from_cache else "success")
        elif kind == "log":
            self._log(*data)
        elif kind == "error":
            self._log(f"ERROR: {data}", "error")
            messagebox.showerror("Error", data)
        elif kind == "complete":
            self._on_complete(data)
        elif kind == "done":
            self._set_running(False)

    def _on_complete(self, results: list[dict]):
        if not results:
            self.status_lbl.config(text="Completado — sin resultados."); return
        ok     = sum(1 for r in results if not r.get("error"))
        errors = sum(1 for r in results if r.get("error"))
        cached = sum(1 for r in results if r.get("from_cache"))
        cancelled = self.cancel_event.is_set()
        self.progress_bar["value"] = self.progress_bar["maximum"]
        tag = "Cancelado" if cancelled else "✓ Completado"
        self.status_lbl.config(text=f"{tag}  —  {ok} imágenes organizadas")
        self.stats_lbl.config(text=f"Errores: {errors}  |  Caché: {cached}")
        self._log("━" * 50)
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
                bar = "█" * max(1, round(n / mx * 18))
                self._log(f"  {cat:<28} {bar} {n}", "cache")
        for r in [r for r in results if r.get("error")][:8]:
            self._log(f"  {r['filename']}: {r['error']}", "error")

    # ── Persistencia ──────────────────────────────────────────────────────────

    def _save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "input": self.input_var.get(), "output": self.output_var.get(),
                    "mode": self.mode_var.get(), "workers": self.workers_var.get(),
                    "confidence": self.confidence_var.get(), "dry_run": self.dry_run_var.get(),
                    "structure": self._get_structure_key(), "naming": self._get_naming_key(),
                    "recursive": self.recursive_var.get(), "extra_prompt": self._get_prompt(),
                    "last_preset": self.preset_combo.get(),
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

            # Migrar config antigua si es necesario
            struct_key = cfg.get("structure", "cat/ym")
            old_map = {"%Y-%m": "cat/ym", "%Y": "cat/y", "": "cat", "flat": "flat"}
            struct_key = old_map.get(struct_key, struct_key)

            self._applying_preset = True
            self._set_structure(struct_key)
            self._set_naming(cfg.get("naming", "original"))
            self._applying_preset = False

            last = cfg.get("last_preset", _CUSTOM_LABEL)
            if last in self.preset_combo["values"]:
                self._applying_preset = True
                self.preset_combo.set(last)
                self._applying_preset = False

            saved_prompt = cfg.get("extra_prompt", "")
            if saved_prompt:
                self.prompt_text.delete("1.0", tk.END)
                self.prompt_text.insert("1.0", saved_prompt)
                self.prompt_text.config(foreground=C["text"])
        except Exception:
            pass

    def _on_close(self):
        if self.running:
            if messagebox.askyesno("Proceso activo", "¿Cancelar y salir?"):
                self.cancel_event.set(); self.root.destroy()
        else:
            self._save_config(); self.root.destroy()


# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not HAS_CORE:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("Error",
            f"No se pudo importar photo_sorter.py:\n\n{IMPORT_ERROR}\n\n"
            "Ejecuta desde el directorio del proyecto con el venv activado.")
        root.destroy(); return

    root = tk.Tk()
    PhotoSorterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
