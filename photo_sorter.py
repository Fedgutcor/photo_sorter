#!/usr/bin/env python3
"""
Photo Sorter - Organiza fotos en carpetas por categoría y fecha usando Claude Vision API.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import anthropic
except ImportError:
    print("Error: anthropic no está instalado. Ejecuta: pip install anthropic")
    sys.exit(1)

try:
    import keyring
    _KEYRING_SERVICE = "PhotoSorter"
    _KEYRING_ACCOUNT = "anthropic_api_key"
except ImportError:
    keyring = None

def _resolve_api_key() -> str | None:
    """Resuelve la API key: keychain → env → prompt interactivo."""
    import getpass
    # 1. Keychain (macOS)
    if keyring:
        k = keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
        if k:
            return k
    # 2. Variable de entorno (override para usuarios pro)
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    # 3. Primera vez — pedir sin eco y guardar
    print("\nPhoto Sorter necesita tu Anthropic API key para analizar imágenes.")
    print("Consíguela en: https://console.anthropic.com/settings/keys\n")
    k = getpass.getpass("Pega tu API key (no se mostrará): ").strip()
    if not k:
        print("Error: API key requerida.")
        return None
    if keyring:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, k)
        print("✔ API key guardada de forma segura en el Keychain de macOS.\n")
    return k

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Advertencia: Pillow no instalado. No se podrá leer EXIF ni redimensionar imágenes.")

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except ImportError:
    HAS_HEIF = False

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# Categorías predeterminadas
ALLOWED_CATEGORIES = [
    "mascotas-perros", "mascotas-gatos", "mascotas-otros",
    "personas-selfies", "personas-retratos", "personas-grupos",
    "lugares-paisajes", "lugares-ciudad", "lugares-interiores",
    "paseos-viajes", "actividades-eventos", "actividades-deporte",
    "comida-bebida", "construccion-obra", "construccion-materiales",
    "construccion-planos", "memes", "capturas-pantalla", "arte-digital", "otros"
]

# Extensiones soportadas (HEIC/HEIF requiere pillow-heif)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".bmp", ".tiff"}

# Límites de imagen para envío a APIs
MAX_IMAGE_SIZE      = 20 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8000

# ── Providers ──────────────────────────────────────────────────────────────────
# Cada proveedor declara sus modelos con etiqueta y costo estimado por imagen.
# cost=0.0 significa gratuito o sin cargo visible al usuario.
PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "name": "Anthropic (Claude)",
        "key_url": "https://console.anthropic.com/settings/keys",
        "models": {
            "claude-3-haiku-20240307":    {"label": "Haiku 3  (rápido · barato)",   "cost": 0.0010},
            "claude-3-5-haiku-20241022":  {"label": "Haiku 3.5  (mejor calidad)",   "cost": 0.0030},
            "claude-3-5-sonnet-20241022": {"label": "Sonnet 3.5  (alta precisión)", "cost": 0.0100},
        },
        "default_model": "claude-3-haiku-20240307",
    },
    "google": {
        "name": "Google AI Studio (Gemini)",
        "key_url": "https://aistudio.google.com/app/apikey",
        "models": {
            "gemini-2.0-flash":    {"label": "Gemini 2.0 Flash  (rápido · gratis)",  "cost": 0.0},
            "gemini-1.5-flash":    {"label": "Gemini 1.5 Flash  (estable · gratis)", "cost": 0.0},
            "gemini-1.5-pro":      {"label": "Gemini 1.5 Pro  (alta precisión)",     "cost": 0.0035},
        },
        "default_model": "gemini-2.0-flash",
    },
    "openai": {
        "name": "OpenAI (GPT-4o)",
        "key_url": "https://platform.openai.com/api-keys",
        "models": {
            "gpt-4o-mini": {"label": "GPT-4o mini  (rápido · barato)",   "cost": 0.0015},
            "gpt-4o":      {"label": "GPT-4o  (alta precisión)",          "cost": 0.0100},
        },
        "default_model": "gpt-4o-mini",
    },
    "groq": {
        "name": "Groq (Llama Vision)",
        "key_url": "https://console.groq.com/keys",
        "models": {
            "llama-3.2-11b-vision-preview": {"label": "Llama 3.2 11B Vision  (gratis)", "cost": 0.0},
            "llama-3.2-90b-vision-preview": {"label": "Llama 3.2 90B Vision",           "cost": 0.0009},
        },
        "default_model": "llama-3.2-11b-vision-preview",
    },
}

# Alias plano de costos (para compatibilidad y estimaciones)
MODEL_COSTS: dict[str, float] = {
    model_id: info["cost"]
    for p in PROVIDERS.values()
    for model_id, info in p["models"].items()
}

CATEGORY_TO_WORD = {
    "mascotas-perros":       "perro",
    "mascotas-gatos":        "gato",
    "mascotas-otros":        "mascota",
    "personas-selfies":      "selfie",
    "personas-retratos":     "retrato",
    "personas-grupos":       "grupo",
    "lugares-paisajes":      "paisaje",
    "lugares-ciudad":        "ciudad",
    "lugares-interiores":    "interior",
    "paseos-viajes":         "viaje",
    "actividades-eventos":   "evento",
    "actividades-deporte":   "deporte",
    "comida-bebida":         "comida",
    "construccion-obra":     "obra",
    "construccion-materiales": "material",
    "construccion-planos":   "plano",
    "memes":                 "meme",
    "capturas-pantalla":     "captura",
    "arte-digital":          "arte",
    "otros":                 "foto",
}


def build_vision_prompt(categories: list[str], extra_prompt: str = "") -> str:
    """Construye el prompt para Claude con las categorías especificadas."""
    cats_json = json.dumps(categories, ensure_ascii=False)
    prompt = f"""Analiza la imagen y devuelve SOLO un JSON válido siguiendo este esquema:

{{
  "category": "string (debe ser EXACTAMENTE una de las categorías permitidas)",
  "labels": ["string", "..."] (3–10, en español),
  "confidence": number (0.0–1.0),
  "reason": "string corto (máx 12 palabras)"
}}

Categorías permitidas (elige SOLO una):
{cats_json}

Criterios:
- Si es meme o captura de pantalla, usar "memes" o "capturas-pantalla" si están disponibles.
- Si es una imagen generada/ilustración/render/diseño, usar "arte-digital" si está disponible.
- Si hay obra/planos/materiales/herramientas, usar la categoría de construcción más adecuada.
- Si hay personas: distinguir selfie, retrato (1–2) y grupos (3+) si las categorías existen.
- Si no estás seguro, usa la categoría más genérica disponible con confidence baja.

Responde únicamente con JSON. No incluyas texto adicional."""

    if extra_prompt.strip():
        prompt += f"\n\nInstrucciones adicionales:\n{extra_prompt.strip()}"

    return prompt


# ── Cache ──────────────────────────────────────────────────────────────────────

class CacheDB:
    """Cache SQLite para evitar reprocesar imágenes."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                sha256 TEXT PRIMARY KEY,
                category TEXT,
                labels TEXT,
                confidence REAL,
                reason TEXT,
                created_at TEXT
            )
        """)
        self.conn.commit()

    def get(self, sha256: str) -> Optional[dict]:
        cursor = self.conn.execute(
            "SELECT category, labels, confidence, reason FROM cache WHERE sha256 = ?",
            (sha256,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "category": row[0],
                "labels": json.loads(row[1]),
                "confidence": row[2],
                "reason": row[3],
            }
        return None

    def set(self, sha256: str, result: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO cache
               (sha256, category, labels, confidence, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sha256, result["category"], json.dumps(result["labels"]),
             result["confidence"], result.get("reason", ""), datetime.now().isoformat())
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


# ── Utilidades ─────────────────────────────────────────────────────────────────

def compute_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def normalize_category(category: str) -> str:
    normalized = unicodedata.normalize("NFKD", category)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[\s_]+", "-", normalized)
    normalized = re.sub(r"[^a-z0-9\-]", "", normalized)
    return normalized[:30]


def get_exif_date(file_path: Path) -> Optional[datetime]:
    if not HAS_PIL:
        return None
    try:
        with Image.open(file_path) as img:
            exif_data = img._getexif()
            if not exif_data:
                return None
            exif = {TAGS.get(k, k): v for k, v in exif_data.items()}
            for tag in ["DateTimeOriginal", "DateTime", "DateTimeDigitized"]:
                if tag in exif:
                    try:
                        return datetime.strptime(exif[tag], "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        continue
    except Exception:
        pass
    return None


def get_file_date(file_path: Path) -> tuple[Optional[datetime], str]:
    exif_date = get_exif_date(file_path)
    if exif_date:
        return exif_date, "exif"
    try:
        mtime = os.path.getmtime(file_path)
        return datetime.fromtimestamp(mtime), "mtime"
    except Exception:
        pass
    return None, "none"


def resize_image_if_needed(file_path: Path) -> tuple[bytes, str]:
    media_type = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
    # HEIC no tiene MIME registrado por defecto
    if file_path.suffix.lower() in (".heic", ".heif"):
        media_type = "image/jpeg"  # se convertirá abajo

    with open(file_path, "rb") as f:
        image_bytes = f.read()

    needs_resize = len(image_bytes) > MAX_IMAGE_SIZE

    if HAS_PIL:
        from PIL import Image as _PILImage
        _PILImage.MAX_IMAGE_PIXELS = 100_000_000  # ~300MB RGB — decompression bomb guard
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    needs_resize = True

                # HEIC/HEIF o imagen que necesita resize → convertir a JPEG
                force_convert = file_path.suffix.lower() in (".heic", ".heif")

                if needs_resize or force_convert:
                    ratio = 1.0
                    if needs_resize:
                        ratio = min(
                            MAX_IMAGE_DIMENSION / max(width, 1),
                            MAX_IMAGE_DIMENSION / max(height, 1),
                            1.0,
                        )
                        if len(image_bytes) > MAX_IMAGE_SIZE:
                            ratio = min(ratio, 0.7)
                    new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
                    img_resized = img.resize(new_size, Image.Resampling.LANCZOS)

                    if img_resized.mode in ("RGBA", "P", "LA"):
                        img_resized = img_resized.convert("RGB")
                    media_type = "image/jpeg"

                    from io import BytesIO
                    buf = BytesIO()
                    img_resized.save(buf, format="JPEG", quality=85)
                    image_bytes = buf.getvalue()
                    logger.debug(f"Imagen procesada: {file_path.name} → {new_size}")
        except Exception as e:
            logger.warning(f"No se pudo procesar imagen {file_path}: {e}")

    return image_bytes, media_type


def get_unique_dest_path(dest_path: Path) -> Path:
    if not dest_path.exists():
        return dest_path
    stem, suffix, parent = dest_path.stem, dest_path.suffix, dest_path.parent
    counter = 1
    while True:
        new_path = parent / f"{stem}__{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


# ── Renombrado ─────────────────────────────────────────────────────────────────

class RenameCounter:
    def __init__(self):
        self.counters: dict[str, int] = {}

    def get_next(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


def generate_new_filename(
    file_path: Path,
    category: str,
    file_date: Optional[datetime],
    rename_counter: RenameCounter,
    naming_style: str = "date_cat_n",
    unknown_date_str: str = "00000000",
) -> str:
    ext  = file_path.suffix.lower()
    word = CATEGORY_TO_WORD.get(category, category.split("-")[0] if category else "foto")
    d8   = file_date.strftime("%Y%m%d") if file_date else unknown_date_str
    ym   = file_date.strftime("%Y-%m")  if file_date else "0000-00"
    hms  = file_date.strftime("%H%M%S") if file_date else "000000"
    orig = re.sub(r"[^\w\-]", "_", file_path.stem)[:40]

    if naming_style == "date_cat_n":
        n = rename_counter.get_next(f"{d8}_{word}")
        return f"{d8}_{word}_{n:02d}{ext}"
    elif naming_style == "date_n":
        n = rename_counter.get_next(d8)
        return f"{d8}_{n:03d}{ext}"
    elif naming_style == "date_orig":
        return f"{d8}_{orig}{ext}"
    elif naming_style == "cat_n":
        n = rename_counter.get_next(word)
        return f"{word}_{n:03d}{ext}"
    elif naming_style == "cat_date_n":
        n = rename_counter.get_next(f"{word}_{d8}")
        return f"{word}_{d8}_{n:02d}{ext}"
    elif naming_style == "n":
        n = rename_counter.get_next("__global__")
        return f"{n:04d}{ext}"
    elif naming_style == "ym_n":
        n = rename_counter.get_next(ym)
        return f"{ym}_{n:03d}{ext}"
    elif naming_style == "ymd_time":
        return f"{d8}_{hms}{ext}"
    else:
        n = rename_counter.get_next(f"{d8}_{word}")
        return f"{d8}_{word}_{n:02d}{ext}"


# ── Vision API — implementaciones por proveedor ────────────────────────────────

def _parse_json_response(text: str, allowed_categories: list) -> dict:
    """Parsea la respuesta JSON del modelo y valida la categoría."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    result = json.loads(text)
    if result.get("category") not in allowed_categories:
        result["category"] = allowed_categories[-1] if allowed_categories else "otros"
    return result


def _call_anthropic(api_key: str, model: str, image_b64: str,
                    media_type: str, prompt: str) -> str:
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=500,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": media_type, "data": image_b64,
            }},
            {"type": "text", "text": prompt},
        ]}],
    )
    return response.content[0].text


def _call_google(api_key: str, model: str, image_b64: str,
                 media_type: str, prompt: str) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError(
            "google-genai no está instalado. "
            "Ejecuta: pip install google-genai"
        )
    client = genai.Client(api_key=api_key)
    image_bytes = base64.b64decode(image_b64)
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=media_type),
            prompt,
        ],
    )
    return response.text


def _call_openai_compat(api_key: str, model: str, image_b64: str,
                        media_type: str, prompt: str,
                        base_url: Optional[str] = None) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "openai no está instalado. Ejecuta: pip install openai"
        )
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    response = client.chat.completions.create(
        model=model,
        max_tokens=500,
        messages=[{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
    )
    return response.choices[0].message.content


def _call_groq(api_key: str, model: str, image_b64: str,
               media_type: str, prompt: str) -> str:
    try:
        from groq import Groq
    except ImportError:
        # Groq es OpenAI-compatible — fallback sin SDK dedicado
        return _call_openai_compat(
            api_key, model, image_b64, media_type, prompt,
            base_url="https://api.groq.com/openai/v1",
        )
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=500,
        messages=[{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
    )
    return response.choices[0].message.content


_PROVIDER_CALLERS = {
    "anthropic": _call_anthropic,
    "google":    _call_google,
    "openai":    _call_openai_compat,
    "groq":      _call_groq,
}


async def analyze_image(
    api_key: str,
    file_path: Path,
    semaphore: asyncio.Semaphore,
    delay: float = 0.0,
    extra_prompt: str = "",
    model: str = "claude-3-haiku-20240307",
    provider: str = "anthropic",
    allowed_categories: Optional[list] = None,
    max_retries: int = 3,
) -> dict:
    """Analiza una imagen con el proveedor indicado y devuelve categoría + labels."""
    if allowed_categories is None:
        allowed_categories = ALLOWED_CATEGORIES

    caller = _PROVIDER_CALLERS.get(provider)
    if caller is None:
        raise ValueError(f"Proveedor desconocido: '{provider}'. "
                         f"Opciones: {list(_PROVIDER_CALLERS)}")

    async with semaphore:
        if delay > 0:
            await asyncio.sleep(delay)

        image_bytes, media_type = resize_image_if_needed(file_path)
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        prompt    = build_vision_prompt(allowed_categories, extra_prompt)
        loop      = asyncio.get_running_loop()
        last_exc  = None

        for attempt in range(max_retries):
            try:
                text = await loop.run_in_executor(
                    None,
                    lambda: caller(api_key, model, image_b64, media_type, prompt),
                )
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                # Rate limit / server error → retry con backoff
                err_str = str(e).lower()
                is_retryable = (
                    "rate" in err_str or "429" in err_str
                    or "500" in err_str or "503" in err_str
                )
                if is_retryable and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Error API ({e}), reintentando en {wait}s "
                                   f"(intento {attempt+1}/{max_retries})…")
                    await asyncio.sleep(wait)
                else:
                    raise

        if last_exc is not None:
            raise last_exc

        return _parse_json_response(text, allowed_categories)



# ── Estructura de carpetas ─────────────────────────────────────────────────────

def build_dest_dir(
    output_dir: Path,
    category: str,
    file_date,
    structure: str,
    unknown_date_name: str,
) -> tuple[Path, str]:
    def _fmt(fmt: str) -> str:
        return file_date.strftime(fmt) if file_date else unknown_date_name

    if structure == "flat":
        return output_dir, ""
    elif structure == "cat":
        return output_dir / category, ""
    elif structure == "cat/ym":
        bkt = _fmt("%Y-%m")
        return output_dir / category / bkt, bkt
    elif structure == "cat/y":
        bkt = _fmt("%Y")
        return output_dir / category / bkt, bkt
    elif structure == "cat/y/m":
        y, m = _fmt("%Y"), _fmt("%m")
        return output_dir / category / y / m, f"{y}/{m}"
    elif structure == "ym/cat":
        bkt = _fmt("%Y-%m")
        return output_dir / bkt / category, bkt
    elif structure == "y/cat":
        bkt = _fmt("%Y")
        return output_dir / bkt / category, bkt
    else:
        return output_dir / category, ""


def collect_images(input_dir: Path, recursive: bool = False) -> list[Path]:
    """Recolecta imágenes. Excluye HEIC/HEIF si pillow-heif no está instalado."""
    exts = set(IMAGE_EXTENSIONS)
    if not HAS_HEIF:
        exts -= {".heic", ".heif"}
        # contar omitidos para avisar
        glob_fn = input_dir.rglob if recursive else input_dir.glob
        heic_count = sum(
            len(list(glob_fn(f"*{e}"))) + len(list(glob_fn(f"*{e.upper()}")))
            for e in (".heic", ".heif")
        )
        if heic_count:
            logger.warning(
                f"Se omitieron {heic_count} archivos HEIC/HEIF. "
                "Instala pillow-heif para soporte: pip install pillow-heif"
            )

    images = []
    glob_fn = input_dir.rglob if recursive else input_dir.glob
    for ext in exts:
        images.extend(glob_fn(f"*{ext}"))
        images.extend(glob_fn(f"*{ext.upper()}"))
    return sorted(f for f in set(images) if not f.name.startswith("._"))


# ── Proceso principal ──────────────────────────────────────────────────────────

async def process_images(
    images: list[Path],
    output_dir: Path,
    cache: CacheDB,
    api_key: str,
    mode: str,
    min_confidence: float,
    max_categories: int,
    structure: str,
    unknown_date_name: str,
    delay: float,
    workers: int,
    dry_run: bool,
    naming_style: str = "original",
    progress_callback=None,
    cancel_event=None,
    pause_event=None,
    extra_prompt: str = "",
    model: str = "claude-3-haiku-20240307",
    provider: str = "anthropic",
    allowed_categories: Optional[list] = None,
    # Alias de compatibilidad — ignorado si api_key está presente
    client=None,
) -> list[dict]:
    """
    Procesa todas las imágenes.

    progress_callback(current, total, img_path, filename, category, from_cache)
    cancel_event: threading.Event — detiene el proceso
    pause_event:  threading.Event — cuando está SET, pausa; al limpiar, reanuda
    """
    # Compatibilidad con versiones antiguas que pasaban client=anthropic.Anthropic(...)
    if not api_key and client is not None and hasattr(client, "api_key"):
        api_key = client.api_key
    if allowed_categories is None:
        allowed_categories = ALLOWED_CATEGORIES

    fallback_cat = allowed_categories[-1] if allowed_categories else "otros"
    results: list[dict] = []
    category_counts: dict[str, int] = {}
    semaphore = asyncio.Semaphore(workers)
    rename_counter = RenameCounter()

    for i, img_path in enumerate(images, 1):
        # — Cancelar —
        if cancel_event and cancel_event.is_set():
            logger.info("Procesamiento cancelado.")
            break

        # — Pausar —
        if pause_event:
            while pause_event.is_set():
                if cancel_event and cancel_event.is_set():
                    break
                await asyncio.sleep(0.2)
        if cancel_event and cancel_event.is_set():
            break

        logger.info(f"[{i}/{len(images)}] {img_path.name}")

        result: dict = {
            "filename": img_path.name,
            "new_filename": None,
            "source_path": str(img_path),
            "category": None,
            "date_bucket": None,
            "labels": [],
            "confidence": 0.0,
            "destination": None,
            "date_source": None,
            "error": None,
            "from_cache": False,
        }

        try:
            sha256      = compute_sha256(img_path)
            prompt_hash = hashlib.md5(extra_prompt.strip().encode()).hexdigest()[:8]
            cache_key   = f"{sha256}_{prompt_hash}" if extra_prompt.strip() else sha256

            cached = cache.get(cache_key)
            if cached:
                logger.info("  → cache hit")
                analysis = cached
                result["from_cache"] = True
            else:
                analysis = await analyze_image(
                    api_key=api_key, file_path=img_path, semaphore=semaphore,
                    delay=delay, extra_prompt=extra_prompt, model=model,
                    provider=provider, allowed_categories=allowed_categories,
                )
                cache.set(cache_key, analysis)

            category   = normalize_category(analysis["category"])
            confidence = analysis.get("confidence", 0.5)
            labels     = analysis.get("labels", [])

            if confidence < min_confidence:
                logger.info(f"  → confianza baja ({confidence:.2f}) → {fallback_cat}")
                category = fallback_cat

            if category not in category_counts:
                if len(category_counts) >= max_categories and category != fallback_cat:
                    logger.info(f"  → límite categorías → {fallback_cat}")
                    category = fallback_cat

            category_counts[category] = category_counts.get(category, 0) + 1

            file_date, date_source = get_file_date(img_path)
            dest_dir, date_bucket  = build_dest_dir(
                output_dir, category, file_date, structure, unknown_date_name
            )

            if naming_style != "original":
                new_filename = generate_new_filename(
                    img_path, category, file_date, rename_counter, naming_style
                )
                dest_path = get_unique_dest_path(dest_dir / new_filename)
            else:
                dest_path = get_unique_dest_path(dest_dir / img_path.name)

            result.update({
                "category":     category,
                "new_filename": dest_path.name if naming_style != "original" else None,
                "date_bucket":  date_bucket or None,
                "labels":       labels,
                "confidence":   confidence,
                "destination":  str(dest_path),
                "date_source":  date_source,
            })

            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if mode == "move":
                    shutil.move(str(img_path), str(dest_path))
                else:
                    shutil.copy2(str(img_path), str(dest_path))
                logger.info(f"  → {mode}: {dest_path}")
            else:
                logger.info(f"  → [DRY-RUN] {dest_path}")

        except json.JSONDecodeError as e:
            result["error"] = f"Error parseando JSON: {e}"
            logger.error(f"  → {result['error']}")
        except anthropic.APIError as e:
            result["error"] = f"Error API: {e}"
            logger.error(f"  → {result['error']}")
        except Exception as e:
            result["error"] = f"Error: {e}"
            logger.error(f"  → {result['error']}")

        results.append(result)

        if progress_callback:
            progress_callback(
                i, len(images), img_path,
                img_path.name, result.get("category"), result.get("from_cache", False),
            )

    return results


# ── Reportes ───────────────────────────────────────────────────────────────────

def generate_report(results: list[dict], output_dir: Path, include_csv: bool = True):
    report_json = output_dir / "report.json"
    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"Reporte JSON: {report_json}")

    if include_csv:
        import csv
        report_csv = output_dir / "report.csv"
        fields = [
            "filename", "new_filename", "category", "date_bucket", "labels",
            "confidence", "destination", "date_source", "error", "from_cache",
        ]
        with open(report_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in results:
                row = r.copy()
                row["labels"] = "|".join(r.get("labels", []))
                writer.writerow({k: row.get(k, "") for k in fields})
        logger.info(f"Reporte CSV: {report_csv}")


def estimate_cost(
    num_images: int,
    cached: int = 0,
    model: str = "claude-3-haiku-20240307",
) -> float:
    cost_per = MODEL_COSTS.get(model, 0.001)
    return (num_images - cached) * cost_per


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Organiza fotos en carpetas por categoría y fecha usando Vision AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  %(prog)s --input ./fotos --output ./organizado
  %(prog)s --input ./fotos --output ./organizado --mode move --dry-run
  %(prog)s --input ./fotos --output ./organizado --workers 3 --model claude-3-5-sonnet-20241022
        """,
    )

    parser.add_argument("--input",  "-i", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--mode", choices=["copy", "move"], default="copy")
    parser.add_argument("--max-categories", type=int, default=20)
    parser.add_argument("--min-confidence", type=float, default=0.35)
    parser.add_argument(
        "--date-buckets",
        choices=["ym", "y", "none", "cat-y-m", "ym-cat", "y-cat"],
        default="ym",
        help="Estructura: ym (cat/YYYY-MM), y (cat/YYYY), none (cat), "
             "cat-y-m, ym-cat, y-cat",
    )
    parser.add_argument("--unknown-date-name", default="unknown-date")
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--naming",
        choices=list({"original", "date_cat_n", "date_n", "date_orig",
                      "cat_n", "cat_date_n", "n", "ym_n", "ymd_time"}),
        default="original",
    )
    parser.add_argument("--rename", action="store_true",
                        help="Alias para --naming date_cat_n (compat)")
    parser.add_argument(
        "--provider",
        choices=list(PROVIDERS.keys()),
        default="anthropic",
        help="Proveedor de Vision AI (default: anthropic)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Modelo a usar (default: el primero del proveedor elegido)",
    )
    parser.add_argument("--recursive", "-r", action="store_true")
    parser.add_argument("--no-csv", action="store_true")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_dir  = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()

    if not input_dir.exists():
        logger.error(f"Directorio de entrada no existe: {input_dir}")
        sys.exit(1)

    provider = args.provider
    model    = args.model or PROVIDERS[provider]["default_model"]

    if model not in PROVIDERS[provider]["models"]:
        logger.warning(f"Modelo '{model}' no reconocido para {provider}. "
                       f"Modelos disponibles: {list(PROVIDERS[provider]['models'])}")

    api_key = _resolve_api_key()
    if not api_key:
        sys.exit(1)

    images = collect_images(input_dir, recursive=args.recursive)
    if not images:
        logger.warning(f"No se encontraron imágenes en {input_dir}")
        sys.exit(0)

    logger.info(f"Encontradas {len(images)} imágenes")

    cache_path = output_dir / ".photo_sorter_cache.db"
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    elif not output_dir.exists():
        cache_path = Path(tempfile.gettempdir()) / ".photo_sorter_cache.db"

    cache = CacheDB(cache_path)
    cached_count = sum(1 for img in images if cache.get(compute_sha256(img)))

    estimated_cost = estimate_cost(len(images), cached_count, model)

    logger.info("")
    logger.info("=== ESTIMACIÓN ===")
    logger.info(f"Total:     {len(images)}")
    logger.info(f"Caché:     {cached_count}")
    logger.info(f"Procesar:  {len(images) - cached_count}")
    logger.info(f"Modelo:    {model}")
    logger.info(f"Costo est: ${estimated_cost:.3f} USD")
    logger.info("")

    if not args.yes and not args.dry_run:
        confirm = input("¿Continuar? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes", "s", "si"):
            logger.info("Cancelado.")
            sys.exit(0)

    structure_map = {
        "ym": "cat/ym", "y": "cat/y", "none": "cat",
        "cat-y-m": "cat/y/m", "ym-cat": "ym/cat", "y-cat": "y/cat",
    }
    structure    = structure_map.get(args.date_buckets, "cat/ym")
    naming_style = "date_cat_n" if args.rename else args.naming

    results = asyncio.run(process_images(
        images=images, output_dir=output_dir, cache=cache,
        api_key=api_key, provider=provider,
        mode=args.mode, min_confidence=args.min_confidence,
        max_categories=args.max_categories, structure=structure,
        unknown_date_name=args.unknown_date_name, delay=args.delay,
        workers=args.workers, dry_run=args.dry_run, naming_style=naming_style,
        model=model,
    ))

    cache.close()

    if args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    generate_report(results, output_dir, include_csv=not args.no_csv)

    successful  = sum(1 for r in results if not r["error"])
    errors      = sum(1 for r in results if r["error"])
    from_cache  = sum(1 for r in results if r["from_cache"])

    logger.info("")
    logger.info("=== RESUMEN ===")
    logger.info(f"Procesadas: {successful}/{len(images)}")
    logger.info(f"Caché: {from_cache}  Errores: {errors}")

    categories: dict[str, int] = {}
    for r in results:
        if r["category"]:
            categories[r["category"]] = categories.get(r["category"], 0) + 1

    if categories:
        logger.info("")
        logger.info("Por categoría:")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            logger.info(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
