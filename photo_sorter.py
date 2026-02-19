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
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Advertencia: Pillow no instalado. No se podrá leer EXIF ni redimensionar imágenes.")

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# Categorías permitidas
ALLOWED_CATEGORIES = [
    "mascotas-perros", "mascotas-gatos", "mascotas-otros",
    "personas-selfies", "personas-retratos", "personas-grupos",
    "lugares-paisajes", "lugares-ciudad", "lugares-interiores",
    "paseos-viajes", "actividades-eventos", "actividades-deporte",
    "comida-bebida", "construccion-obra", "construccion-materiales",
    "construccion-planos", "memes", "capturas-pantalla", "arte-digital", "otros"
]

# Extensiones soportadas
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Límites de Claude Vision
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_IMAGE_DIMENSION = 8000  # píxeles

# Costos aproximados (USD por imagen con Haiku)
COST_PER_IMAGE_ESTIMATE = 0.001  # ~$0.001 por imagen con Haiku

# Mapeo de categoría a palabra corta para renombrado
CATEGORY_TO_WORD = {
    "mascotas-perros": "perro",
    "mascotas-gatos": "gato",
    "mascotas-otros": "mascota",
    "personas-selfies": "selfie",
    "personas-retratos": "retrato",
    "personas-grupos": "grupo",
    "lugares-paisajes": "paisaje",
    "lugares-ciudad": "ciudad",
    "lugares-interiores": "interior",
    "paseos-viajes": "viaje",
    "actividades-eventos": "evento",
    "actividades-deporte": "deporte",
    "comida-bebida": "comida",
    "construccion-obra": "obra",
    "construccion-materiales": "material",
    "construccion-planos": "plano",
    "memes": "meme",
    "capturas-pantalla": "captura",
    "arte-digital": "arte",
    "otros": "foto"
}

VISION_PROMPT = """Analiza la imagen y devuelve SOLO un JSON válido siguiendo este esquema:

{
  "category": "string (debe ser EXACTAMENTE una de las categorías permitidas)",
  "labels": ["string", "..."] (3–10, en español),
  "confidence": number (0.0–1.0),
  "reason": "string corto (máx 12 palabras)"
}

Categorías permitidas (elige SOLO una):
["mascotas-perros","mascotas-gatos","mascotas-otros","personas-selfies","personas-retratos","personas-grupos","lugares-paisajes","lugares-ciudad","lugares-interiores","paseos-viajes","actividades-eventos","actividades-deporte","comida-bebida","construccion-obra","construccion-materiales","construccion-planos","memes","capturas-pantalla","arte-digital","otros"]

Criterios:
- Si es meme o captura de pantalla, usar "memes" o "capturas-pantalla".
- Si es una imagen generada/ilustración/render/diseño, usar "arte-digital".
- Si hay obra/planos/materiales/herramientas, usar "construccion-..." según corresponda.
- Si hay personas: distinguir selfie, retrato (1–2) y grupos (3+).
- Si no estás seguro, usa "otros" con confidence baja.

Responde únicamente con JSON. No incluyas texto adicional."""


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
                "reason": row[3]
            }
        return None

    def set(self, sha256: str, result: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO cache (sha256, category, labels, confidence, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sha256, result["category"], json.dumps(result["labels"]),
             result["confidence"], result.get("reason", ""), datetime.now().isoformat())
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


def compute_sha256(file_path: Path) -> str:
    """Calcula SHA256 del contenido del archivo."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def normalize_category(category: str) -> str:
    """Normaliza nombre de categoría para uso como carpeta."""
    # Remover tildes
    normalized = unicodedata.normalize("NFKD", category)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    # Lowercase, espacios a guiones
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[\s_]+", "-", normalized)
    # Solo alfanuméricos y guiones
    normalized = re.sub(r"[^a-z0-9\-]", "", normalized)
    # Máximo 30 chars
    return normalized[:30]


def get_exif_date(file_path: Path) -> Optional[datetime]:
    """Extrae fecha EXIF de la imagen."""
    if not HAS_PIL:
        return None

    try:
        with Image.open(file_path) as img:
            exif_data = img._getexif()
            if not exif_data:
                return None

            # Mapear tags
            exif = {TAGS.get(k, k): v for k, v in exif_data.items()}

            # Prioridad: DateTimeOriginal > DateTime > DateTimeDigitized
            for tag in ["DateTimeOriginal", "DateTime", "DateTimeDigitized"]:
                if tag in exif:
                    date_str = exif[tag]
                    try:
                        return datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        continue
    except Exception:
        pass

    return None


def get_file_date(file_path: Path) -> tuple[Optional[datetime], str]:
    """
    Obtiene la fecha del archivo.
    Returns: (datetime o None, source) donde source es 'exif', 'mtime', o 'none'
    """
    # Intentar EXIF
    exif_date = get_exif_date(file_path)
    if exif_date:
        return exif_date, "exif"

    # Fallback a mtime
    try:
        mtime = os.path.getmtime(file_path)
        return datetime.fromtimestamp(mtime), "mtime"
    except Exception:
        pass

    return None, "none"


def get_date_bucket(file_path: Path, unknown_name: str = "unknown-date") -> tuple[str, str]:
    """
    Obtiene el bucket de fecha (YYYY-MM) y la fuente.
    Returns: (bucket, source) donde source es 'exif', 'mtime', o 'none'
    """
    file_date, source = get_file_date(file_path)
    if file_date:
        return file_date.strftime("%Y-%m"), source
    return unknown_name, "none"


def resize_image_if_needed(file_path: Path) -> tuple[bytes, str]:
    """
    Lee imagen y la redimensiona si excede límites de Claude.
    Returns: (image_bytes, media_type)
    """
    media_type = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"

    with open(file_path, "rb") as f:
        image_bytes = f.read()

    needs_resize = len(image_bytes) > MAX_IMAGE_SIZE

    if HAS_PIL:
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    needs_resize = True

                if needs_resize:
                    # Calcular nuevo tamaño manteniendo aspect ratio
                    ratio = min(MAX_IMAGE_DIMENSION / width, MAX_IMAGE_DIMENSION / height, 1.0)
                    if len(image_bytes) > MAX_IMAGE_SIZE:
                        ratio = min(ratio, 0.7)  # Reducir más si es muy grande

                    new_size = (int(width * ratio), int(height * ratio))
                    img_resized = img.resize(new_size, Image.Resampling.LANCZOS)

                    # Convertir a RGB si es necesario (para JPEG)
                    if img_resized.mode in ("RGBA", "P"):
                        img_resized = img_resized.convert("RGB")
                        media_type = "image/jpeg"

                    from io import BytesIO
                    buffer = BytesIO()
                    fmt = "JPEG" if "jpeg" in media_type or "jpg" in media_type else "PNG"
                    img_resized.save(buffer, format=fmt, quality=85)
                    image_bytes = buffer.getvalue()
                    logger.debug(f"Imagen redimensionada: {file_path.name} -> {new_size}")
        except Exception as e:
            logger.warning(f"No se pudo procesar imagen {file_path}: {e}")

    return image_bytes, media_type


def get_unique_dest_path(dest_path: Path) -> Path:
    """Genera ruta única si ya existe el archivo."""
    if not dest_path.exists():
        return dest_path

    stem = dest_path.stem
    suffix = dest_path.suffix
    parent = dest_path.parent

    counter = 1
    while True:
        new_path = parent / f"{stem}__{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


class RenameCounter:
    """Contador secuencial con clave arbitraria."""

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
    """
    Genera nombre de archivo según el estilo de renombrado elegido.

    Estilos disponibles:
        date_cat_n  → 20240315_perro_01.jpg   (fecha + categoría + número)
        date_n      → 20240315_001.jpg         (fecha + número)
        date_orig   → 20240315_IMG_0542.jpg    (fecha + nombre original)
        cat_n       → perro_001.jpg            (categoría + número)
        cat_date_n  → perro_20240315_01.jpg    (categoría + fecha + número)
        n           → 0001.jpg                  (solo número global)
        ym_n        → 2024-03_001.jpg          (año-mes + número)
        ymd_time    → 20240315_143022.jpg      (fecha + hora EXIF, si existe)
    """
    ext  = file_path.suffix.lower()
    word = CATEGORY_TO_WORD.get(category, "foto")
    d8   = file_date.strftime("%Y%m%d")   if file_date else unknown_date_str
    ym   = file_date.strftime("%Y-%m")    if file_date else "0000-00"
    hms  = file_date.strftime("%H%M%S")   if file_date else "000000"
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

    else:  # fallback
        n = rename_counter.get_next(f"{d8}_{word}")
        return f"{d8}_{word}_{n:02d}{ext}"


async def analyze_image_with_claude(
    client: anthropic.Anthropic,
    file_path: Path,
    semaphore: asyncio.Semaphore,
    delay: float = 0.0,
    extra_prompt: str = "",
) -> dict:
    """Analiza una imagen con Claude Vision API.

    Args:
        extra_prompt: Instrucciones adicionales que se agregan al prompt base.
    """
    async with semaphore:
        if delay > 0:
            await asyncio.sleep(delay)

        image_bytes, media_type = resize_image_if_needed(file_path)
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

        # Componer prompt final
        prompt = VISION_PROMPT
        if extra_prompt.strip():
            prompt = f"{VISION_PROMPT}\n\nInstrucciones adicionales:\n{extra_prompt.strip()}"

        # Llamada síncrona (anthropic SDK no tiene async nativo para messages)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }]
            )
        )

        # Parsear respuesta JSON
        text = response.content[0].text.strip()
        # Limpiar posibles bloques de código
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        result = json.loads(text)

        # Validar categoría
        if result.get("category") not in ALLOWED_CATEGORIES:
            result["category"] = "otros"

        return result


def build_dest_dir(
    output_dir: Path,
    category: str,
    file_date,
    structure: str,
    unknown_date_name: str,
) -> tuple[Path, str]:
    """
    Construye la ruta de destino y devuelve (dest_dir, date_bucket_label).

    Claves de estructura:
        flat       → salida/
        cat        → salida/categoria/
        cat/ym     → salida/categoria/YYYY-MM/
        cat/y      → salida/categoria/YYYY/
        cat/y/m    → salida/categoria/YYYY/MM/
        ym/cat     → salida/YYYY-MM/categoria/
        y/cat      → salida/YYYY/categoria/
    """
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
    else:  # fallback
        return output_dir / category, ""


def collect_images(input_dir: Path, recursive: bool = False) -> list[Path]:
    """Recolecta todas las imágenes del directorio."""
    images = []
    glob_fn = input_dir.rglob if recursive else input_dir.glob
    for ext in IMAGE_EXTENSIONS:
        images.extend(glob_fn(f"*{ext}"))
        images.extend(glob_fn(f"*{ext.upper()}"))
    return sorted(images)


async def process_images(
    images: list[Path],
    output_dir: Path,
    cache: CacheDB,
    client: anthropic.Anthropic,
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
    extra_prompt: str = "",
) -> list[dict]:
    """Procesa todas las imágenes.

    Args:
        progress_callback: callable(current, total, filename, category, from_cache)
        cancel_event: threading.Event — si se activa, detiene el procesamiento
        extra_prompt: instrucciones adicionales para Claude (se añaden al prompt base)
    """
    results = []
    category_counts: dict[str, int] = {}
    semaphore = asyncio.Semaphore(workers)
    rename_counter = RenameCounter()

    for i, img_path in enumerate(images, 1):
        if cancel_event and cancel_event.is_set():
            logger.info("Procesamiento cancelado por el usuario.")
            break
        logger.info(f"[{i}/{len(images)}] Procesando: {img_path.name}")

        result = {
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
            "from_cache": False
        }

        try:
            # Calcular hash
            sha256 = compute_sha256(img_path)

            # Clave de caché: compuesta si hay prompt personalizado
            prompt_hash = hashlib.md5(extra_prompt.strip().encode()).hexdigest()[:8]
            cache_key = f"{sha256}_{prompt_hash}" if extra_prompt.strip() else sha256

            # Buscar en cache
            cached = cache.get(cache_key)
            if cached:
                logger.info(f"  -> Cache hit")
                analysis = cached
                result["from_cache"] = True
            else:
                # Llamar a Claude
                analysis = await analyze_image_with_claude(client, img_path, semaphore, delay, extra_prompt)
                cache.set(cache_key, analysis)

            category = normalize_category(analysis["category"])
            confidence = analysis.get("confidence", 0.5)
            labels = analysis.get("labels", [])

            # Verificar confianza mínima
            if confidence < min_confidence:
                category = "otros"
                logger.info(f"  -> Confianza baja ({confidence:.2f}), enviando a 'otros'")

            # Verificar límite de categorías
            if category not in category_counts:
                if len(category_counts) >= max_categories and category != "otros":
                    category = "otros"
                    logger.info(f"  -> Límite de categorías alcanzado, enviando a 'otros'")

            category_counts[category] = category_counts.get(category, 0) + 1

            # Obtener fecha del archivo
            file_date, date_source = get_file_date(img_path)

            # Construir ruta destino y obtener bucket de fecha
            dest_dir, date_bucket = build_dest_dir(
                output_dir, category, file_date, structure, unknown_date_name
            )

            # Determinar nombre de archivo
            if naming_style != "original":
                new_filename = generate_new_filename(
                    img_path, category, file_date, rename_counter, naming_style
                )
                dest_path = get_unique_dest_path(dest_dir / new_filename)
            else:
                dest_path = get_unique_dest_path(dest_dir / img_path.name)

            result["category"] = category
            result["new_filename"] = dest_path.name if naming_style != "original" else None
            result["date_bucket"] = date_bucket or None
            result["labels"] = labels
            result["confidence"] = confidence
            result["destination"] = str(dest_path)
            result["date_source"] = date_source

            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if mode == "move":
                    shutil.move(str(img_path), str(dest_path))
                else:
                    shutil.copy2(str(img_path), str(dest_path))
                logger.info(f"  -> {mode}: {dest_path}")
            else:
                logger.info(f"  -> [DRY-RUN] {mode} a: {dest_path}")

        except json.JSONDecodeError as e:
            result["error"] = f"Error parseando JSON: {e}"
            logger.error(f"  -> Error: {result['error']}")
        except anthropic.APIError as e:
            result["error"] = f"Error API: {e}"
            logger.error(f"  -> Error: {result['error']}")
        except Exception as e:
            result["error"] = f"Error: {e}"
            logger.error(f"  -> Error: {result['error']}")

        results.append(result)

        if progress_callback:
            progress_callback(i, len(images), img_path.name, result.get("category"), result.get("from_cache", False))

    return results


def generate_report(results: list[dict], output_dir: Path, include_csv: bool = True):
    """Genera reportes JSON y CSV."""
    # JSON
    report_json = output_dir / "report.json"
    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"Reporte JSON: {report_json}")

    # CSV
    if include_csv:
        report_csv = output_dir / "report.csv"
        import csv
        with open(report_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "filename", "new_filename", "category", "date_bucket", "labels", "confidence",
                "destination", "date_source", "error", "from_cache"
            ])
            writer.writeheader()
            for r in results:
                row = r.copy()
                row["labels"] = "|".join(r.get("labels", []))
                writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
        logger.info(f"Reporte CSV: {report_csv}")


def estimate_cost(num_images: int, cached: int = 0) -> float:
    """Estima costo aproximado en USD."""
    images_to_process = num_images - cached
    return images_to_process * COST_PER_IMAGE_ESTIMATE


def main():
    parser = argparse.ArgumentParser(
        description="Organiza fotos en carpetas por categoría y fecha usando Claude Vision API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  %(prog)s --input ./fotos --output ./organizado
  %(prog)s --input ./fotos --output ./organizado --mode move --dry-run
  %(prog)s --input ./fotos --output ./organizado --workers 3 --delay 0.5
        """
    )

    parser.add_argument("--input", "-i", required=True, help="Directorio de entrada con imágenes")
    parser.add_argument("--output", "-o", required=True, help="Directorio de salida organizado")
    parser.add_argument("--mode", choices=["copy", "move"], default="copy",
                        help="Modo: copy o move (default: copy)")
    parser.add_argument("--max-categories", type=int, default=20,
                        help="Máximo de categorías antes de usar 'otros' (default: 20)")
    parser.add_argument("--min-confidence", type=float, default=0.35,
                        help="Confianza mínima, menor va a 'otros' (default: 0.35)")
    parser.add_argument("--date-buckets",
                        choices=["ym", "y", "none", "cat-y-m", "ym-cat", "y-cat"],
                        default="ym",
                        help="Estructura: ym (cat/YYYY-MM), y (cat/YYYY), none (cat), "
                             "cat-y-m (cat/YYYY/MM), ym-cat (YYYY-MM/cat), y-cat (YYYY/cat) (default: ym)")
    parser.add_argument("--unknown-date-name", default="unknown-date",
                        help="Nombre para fechas desconocidas (default: unknown-date)")
    parser.add_argument("--delay", type=float, default=0.1,
                        help="Delay en segundos entre llamadas API (default: 0.1)")
    parser.add_argument("--workers", type=int, default=2,
                        help="Workers concurrentes para API (default: 2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="No mover/copiar archivos, solo generar reporte")
    parser.add_argument("--naming",
                        choices=["original", "date_cat_n", "date_n", "date_orig",
                                 "cat_n", "cat_date_n", "n", "ym_n", "ymd_time"],
                        default="original",
                        help="Estilo de renombrado (default: original)")
    parser.add_argument("--rename", action="store_true",
                        help="Alias para --naming date_cat_n (compat)")
    parser.add_argument("--no-csv", action="store_true",
                        help="No generar reporte CSV, solo JSON")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Saltar confirmación de estimación de costo")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Mostrar más detalles")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validar directorios
    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()

    if not input_dir.exists():
        logger.error(f"Directorio de entrada no existe: {input_dir}")
        sys.exit(1)

    # Verificar API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY no está configurada")
        logger.error("Configúrala con: export ANTHROPIC_API_KEY='tu-api-key'")
        sys.exit(1)

    # Recolectar imágenes
    images = collect_images(input_dir)
    if not images:
        logger.warning(f"No se encontraron imágenes en {input_dir}")
        sys.exit(0)

    logger.info(f"Encontradas {len(images)} imágenes")

    # Inicializar cache
    cache_path = output_dir / ".photo_sorter_cache.db"
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    elif not output_dir.exists():
        # Para dry-run, usar cache temporal
        cache_path = Path("/tmp/.photo_sorter_cache.db")

    cache = CacheDB(cache_path)

    # Contar imágenes en cache
    cached_count = 0
    for img in images:
        sha256 = compute_sha256(img)
        if cache.get(sha256):
            cached_count += 1

    # Estimación de costo
    estimated_cost = estimate_cost(len(images), cached_count)

    logger.info(f"")
    logger.info(f"=== ESTIMACIÓN ===")
    logger.info(f"Total imágenes: {len(images)}")
    logger.info(f"En cache: {cached_count}")
    logger.info(f"A procesar: {len(images) - cached_count}")
    logger.info(f"Costo estimado: ${estimated_cost:.2f} USD")
    logger.info(f"Workers: {args.workers}")
    logger.info(f"Delay: {args.delay}s")
    if args.dry_run:
        logger.info(f"MODO: DRY-RUN (no se moverán/copiarán archivos)")
    if args.rename:
        logger.info(f"Renombrado: YYYYMMDD_palabra_##.ext")
    logger.info(f"")

    if not args.yes and not args.dry_run:
        confirm = input("¿Continuar? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes", "s", "si"):
            logger.info("Cancelado por el usuario")
            sys.exit(0)

    # Crear cliente
    client = anthropic.Anthropic(api_key=api_key)

    # Procesar
    structure_map = {
        "ym": "cat/ym", "y": "cat/y", "none": "cat",
        "cat-y-m": "cat/y/m", "ym-cat": "ym/cat", "y-cat": "y/cat",
    }
    structure = structure_map.get(args.date_buckets, "cat/ym")

    results = asyncio.run(process_images(
        images=images,
        output_dir=output_dir,
        cache=cache,
        client=client,
        mode=args.mode,
        min_confidence=args.min_confidence,
        max_categories=args.max_categories,
        structure=structure,
        unknown_date_name=args.unknown_date_name,
        delay=args.delay,
        workers=args.workers,
        dry_run=args.dry_run,
        naming_style="date_cat_n" if args.rename else args.naming
    ))

    cache.close()

    # Generar reportes
    if args.dry_run:
        # En dry-run, crear directorio solo para reportes
        output_dir.mkdir(parents=True, exist_ok=True)

    generate_report(results, output_dir, include_csv=not args.no_csv)

    # Resumen
    successful = sum(1 for r in results if not r["error"])
    errors = sum(1 for r in results if r["error"])
    from_cache = sum(1 for r in results if r["from_cache"])

    logger.info("")
    logger.info("=== RESUMEN ===")
    logger.info(f"Procesadas: {successful}/{len(images)}")
    logger.info(f"Desde cache: {from_cache}")
    logger.info(f"Errores: {errors}")

    # Mostrar distribución de categorías
    categories = {}
    for r in results:
        if r["category"]:
            categories[r["category"]] = categories.get(r["category"], 0) + 1

    if categories:
        logger.info("")
        logger.info("Distribución por categoría:")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            logger.info(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
