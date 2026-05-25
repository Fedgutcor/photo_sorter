#!/usr/bin/env bash
set -e

VERSION="1.0.0"
DIST_NAME="photo-sorter-v${VERSION}"
DIST_DIR="/tmp/${DIST_NAME}"
OUT="$(pwd)/dist/${DIST_NAME}.zip"

echo "→ Empaquetando Photo Sorter v${VERSION}..."

rm -rf "$DIST_DIR" && mkdir -p "$DIST_DIR"
mkdir -p "$(pwd)/dist"

cp photo_sorter.py "$DIST_DIR/"
cp photo_sorter_ui.py "$DIST_DIR/"
cp pyproject.toml "$DIST_DIR/"
cp README.md "$DIST_DIR/" 2>/dev/null || true

find "$DIST_DIR" -name "*.pyc" -delete 2>/dev/null || true
find "$DIST_DIR" -name ".DS_Store" -delete 2>/dev/null || true

cd /tmp && zip -r "$OUT" "$DIST_NAME" -q

echo "✔ Listo: $OUT"
echo "  Tamaño: $(du -sh "$OUT" | cut -f1)"
