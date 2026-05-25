#!/bin/bash
# Photo Sorter — instalador y launcher para macOS / Linux
set -e

VENV_DIR="$(dirname "$0")/.venv"
PYTHON=""

# Buscar Python 3.11+
for cmd in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c 'import sys; print(sys.version_info >= (3,11))' 2>/dev/null)
        if [ "$version" = "True" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ERROR: se requiere Python 3.11 o superior."
    echo "  Descárgalo en https://www.python.org/downloads/"
    echo ""
    exit 1
fi

# Crear entorno virtual si no existe
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creando entorno virtual..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"
PYEXE="$VENV_DIR/bin/python"

# Instalar / actualizar dependencias
echo "  Instalando dependencias..."
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet anthropic "Pillow>=10" "keyring>=25" pillow-heif

# Instalar SDKs opcionales si están disponibles en requirements
for pkg in google-genai openai groq; do
    "$PIP" install --quiet "$pkg" 2>/dev/null && true
done

echo "  Listo. Iniciando Photo Sorter..."
echo ""
cd "$(dirname "$0")"
"$PYEXE" photo_sorter_ui.py
