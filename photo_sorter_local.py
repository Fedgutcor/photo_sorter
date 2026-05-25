#!/usr/bin/env python3
"""
Photo/Video Sorter Local - Organiza fotos y videos (100% local, sin costo).
Renombra a formato YYYYMMDD_##.ext y detecta duplicados.
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from collections import defaultdict

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
except ImportError as e:
    print(f"Error: Pillow no instalado. Ejecuta: pip install Pillow")
    sys.exit(1)

# Soporte HEIC (opcional)
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# Extensiones soportadas
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".heic"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi", ".mkv", ".webm", ".m4v", ".3gp"}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def compute_sha256(file_path: Path) -> str:
    """Calcula SHA256 del contenido del archivo."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_exif_date(file_path: Path) -> Optional[datetime]:
    """Extrae fecha EXIF de la imagen."""
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


def get_video_date(file_path: Path) -> Optional[datetime]:
    """Intenta obtener fecha de creación de video usando ffprobe o metadata del archivo."""
    # Intentar con ffprobe si está disponible
    try:
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(file_path)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            import json as json_module
            data = json_module.loads(result.stdout)
            tags = data.get("format", {}).get("tags", {})
            for key in ["creation_time", "date"]:
                if key in tags:
                    try:
                        # Formato ISO 8601
                        date_str = tags[key].replace("Z", "+00:00")
                        return datetime.fromisoformat(date_str.split(".")[0])
                    except (ValueError, IndexError):
                        continue
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return None


def get_file_date(file_path: Path, is_video: bool = False) -> tuple[Optional[datetime], str]:
    """Obtiene la fecha del archivo."""
    if is_video:
        video_date = get_video_date(file_path)
        if video_date:
            return video_date, "video_meta"
    else:
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


class RenameCounter:
    """Contador para generar nombres únicos por fecha."""
    def __init__(self):
        self.counters: dict[str, int] = {}

    def get_next(self, date_str: str) -> int:
        self.counters[date_str] = self.counters.get(date_str, 0) + 1
        return self.counters[date_str]


def collect_files(input_dir: Path, recursive: bool = True) -> list[Path]:
    """Recolecta todas las imágenes y videos del directorio."""
    files = []
    pattern = "**/*" if recursive else "*"

    for ext in ALL_EXTENSIONS:
        files.extend(input_dir.glob(f"{pattern}{ext}"))
        files.extend(input_dir.glob(f"{pattern}{ext.upper()}"))

    # Filtrar archivos ._ de macOS
    files = [f for f in files if not f.name.startswith("._")]

    return sorted(set(files))


def build_hash_index(files: list[Path], show_progress: bool = True) -> dict[str, list[Path]]:
    """
    Pre-calcula hashes de todos los archivos para detectar duplicados.
    Retorna dict: hash -> lista de archivos con ese hash
    """
    hash_to_files: dict[str, list[Path]] = defaultdict(list)

    if show_progress:
        logger.info("Calculando hashes para detectar duplicados...")

    for i, file_path in enumerate(files, 1):
        if show_progress and i % 100 == 0:
            logger.info(f"  Hashes: {i}/{len(files)}")
        try:
            file_hash = compute_sha256(file_path)
            hash_to_files[file_hash].append(file_path)
        except Exception as e:
            logger.warning(f"Error calculando hash de {file_path}: {e}")

    return hash_to_files


def process_files(
    files: list[Path],
    output_dir: Path,
    hash_index: dict[str, list[Path]],
    mode: str,
    dry_run: bool
) -> list[dict]:
    """Procesa todos los archivos: renombra y detecta duplicados."""
    results = []
    rename_counter = RenameCounter()

    # Rastrear qué hashes ya procesamos (el primero es original, los demás duplicados)
    seen_hashes: dict[str, str] = {}  # hash -> path del original

    for i, file_path in enumerate(files, 1):
        logger.info(f"[{i}/{len(files)}] {file_path.name}")

        result = {
            "filename": file_path.name,
            "new_filename": None,
            "source_path": str(file_path),
            "destination": None,
            "date_source": None,
            "is_duplicate": False,
            "duplicate_of": None,
            "error": None
        }

        try:
            # Calcular hash
            file_hash = compute_sha256(file_path)

            # Detectar si es video
            is_video = file_path.suffix.lower() in VIDEO_EXTENSIONS

            # Obtener fecha
            file_date, date_source = get_file_date(file_path, is_video)
            date_str = file_date.strftime("%Y%m%d") if file_date else "00000000"

            # Verificar si es duplicado
            is_duplicate = file_hash in seen_hashes

            # Generar nombre
            ext = file_path.suffix.lower()
            num = rename_counter.get_next(date_str)

            if is_duplicate:
                new_filename = f"{date_str}_{num:02d}_duplicado{ext}"
                result["is_duplicate"] = True
                result["duplicate_of"] = seen_hashes[file_hash]
            else:
                new_filename = f"{date_str}_{num:02d}{ext}"
                seen_hashes[file_hash] = str(file_path)

            dest_path = output_dir / new_filename

            # Evitar colisiones de nombre
            if dest_path.exists():
                base = dest_path.stem
                counter = 1
                while dest_path.exists():
                    dest_path = output_dir / f"{base}_{counter}{ext}"
                    counter += 1
                new_filename = dest_path.name

            result["new_filename"] = new_filename
            result["destination"] = str(dest_path)
            result["date_source"] = date_source

            if not dry_run:
                output_dir.mkdir(parents=True, exist_ok=True)
                if mode == "move":
                    shutil.move(str(file_path), str(dest_path))
                else:
                    shutil.copy2(str(file_path), str(dest_path))

            status = "[DRY-RUN] " if dry_run else ""
            dup_status = "DUPLICADO " if is_duplicate else ""
            logger.info(f"  -> {status}{dup_status}{new_filename}")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"  -> Error: {e}")

        results.append(result)

    return results


def generate_report(results: list[dict], output_dir: Path):
    """Genera reportes JSON y CSV."""
    import csv

    report_json = output_dir / "report.json"
    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"Reporte JSON: {report_json}")

    report_csv = output_dir / "report.csv"
    with open(report_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "filename", "new_filename", "destination", "date_source",
            "is_duplicate", "duplicate_of", "error"
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in writer.fieldnames})
    logger.info(f"Reporte CSV: {report_csv}")


def undo_from_report(report_path: str, skip_confirm: bool = False):
    """Revierte los movimientos usando un report.json previo."""
    report_file = Path(report_path)

    if not report_file.exists():
        logger.error(f"No existe el archivo: {report_file}")
        sys.exit(1)

    with open(report_file, "r", encoding="utf-8") as f:
        results = json.load(f)

    to_revert = [
        r for r in results
        if r.get("source_path") and r.get("destination") and not r.get("error")
    ]

    if not to_revert:
        logger.warning("No hay archivos para revertir")
        return

    existing = [
        r for r in to_revert
        if Path(r["destination"]).exists()
        and not Path(r["destination"]).is_symlink()
        and Path(r["source_path"]).parent.exists()
    ]

    logger.info("")
    logger.info("=== UNDO ===")
    logger.info(f"Archivos en reporte: {len(to_revert)}")
    logger.info(f"Archivos existentes en destino: {len(existing)}")

    if not existing:
        logger.warning("No hay archivos para revertir")
        return

    if not skip_confirm:
        confirm = input(f"Revertir {len(existing)} archivos? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes", "s", "si"):
            logger.info("Cancelado")
            return

    reverted = 0
    errors = 0

    for r in existing:
        src = Path(r["destination"])
        dst = Path(r["source_path"])

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            logger.info(f"Revertido: {src.name} -> {dst}")
            reverted += 1
        except Exception as e:
            logger.error(f"Error revirtiendo {src}: {e}")
            errors += 1

    logger.info(f"")
    logger.info(f"Revertidos: {reverted}, Errores: {errors}")


def main():
    parser = argparse.ArgumentParser(
        description="Organiza fotos y videos: renombra a YYYYMMDD_##.ext y detecta duplicados",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--input", "-i", help="Directorio de entrada")
    parser.add_argument("--output", "-o", help="Directorio de salida (default: INPUT_organizado)")
    parser.add_argument("--mode", choices=["copy", "move"], default="copy")
    parser.add_argument("--no-recursive", action="store_true", help="No buscar en subcarpetas")
    parser.add_argument("--dry-run", action="store_true", help="Simular sin mover archivos")
    parser.add_argument("--yes", "-y", action="store_true", help="Confirmar automáticamente")
    parser.add_argument("--undo", metavar="REPORT.JSON", help="Revertir usando report.json")

    args = parser.parse_args()

    # Modo UNDO
    if args.undo:
        undo_from_report(args.undo, args.yes)
        sys.exit(0)

    if not args.input:
        parser.error("--input es requerido")

    input_dir = Path(args.input).resolve()

    if args.output:
        output_dir = Path(args.output).resolve()
    else:
        output_dir = input_dir.parent / f"{input_dir.name}_organizado"
        logger.info(f"Output: {output_dir}")

    if not input_dir.exists():
        logger.error(f"No existe: {input_dir}")
        sys.exit(1)

    # Recolectar archivos
    logger.info(f"Buscando archivos en {input_dir}...")
    files = collect_files(input_dir, recursive=not args.no_recursive)

    if not files:
        logger.warning("No se encontraron archivos")
        sys.exit(0)

    # Contar tipos
    images = [f for f in files if f.suffix.lower() in IMAGE_EXTENSIONS]
    videos = [f for f in files if f.suffix.lower() in VIDEO_EXTENSIONS]

    logger.info(f"Encontrados: {len(files)} archivos ({len(images)} fotos, {len(videos)} videos)")

    # Calcular hashes para detectar duplicados
    hash_index = build_hash_index(files)

    # Contar duplicados
    duplicates = sum(len(paths) - 1 for paths in hash_index.values() if len(paths) > 1)
    unique = len(hash_index)

    logger.info("")
    logger.info("=== RESUMEN ===")
    logger.info(f"Total archivos: {len(files)}")
    logger.info(f"Archivos únicos: {unique}")
    logger.info(f"Duplicados detectados: {duplicates}")
    logger.info(f"Modo: {args.mode.upper()}")
    if args.mode == "move":
        logger.info("MOVE moverá archivos. Usa --undo report.json para revertir.")
    if args.dry_run:
        logger.info("DRY-RUN: no se moverán archivos")
    logger.info("")

    if not args.yes and not args.dry_run:
        confirm = input("Continuar? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes", "s", "si"):
            logger.info("Cancelado")
            sys.exit(0)

    # Procesar
    results = process_files(
        files=files,
        output_dir=output_dir,
        hash_index=hash_index,
        mode=args.mode,
        dry_run=args.dry_run
    )

    # Reportes
    if args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    generate_report(results, output_dir)

    # Resumen final
    successful = sum(1 for r in results if not r["error"])
    dup_count = sum(1 for r in results if r["is_duplicate"])
    errors = sum(1 for r in results if r["error"])

    logger.info("")
    logger.info("=== COMPLETADO ===")
    logger.info(f"Procesados: {successful}/{len(files)}")
    logger.info(f"Duplicados marcados: {dup_count}")
    logger.info(f"Errores: {errors}")

    if dup_count > 0:
        logger.info("")
        logger.info(f"Tip: Busca '*_duplicado.*' para revisar/eliminar duplicados")


if __name__ == "__main__":
    main()
