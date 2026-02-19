# Photo Sorter

Organiza fotos automáticamente en carpetas por categoría y fecha usando Claude Vision API.
Disponible como **interfaz gráfica (UI)** o como herramienta de línea de comandos (CLI).

## Estructura de salida

```
output/
├── mascotas-perros/
│   ├── 2024-01/
│   │   └── foto1.jpg
│   └── 2024-02/
│       └── foto2.jpg
├── personas-grupos/
│   └── 2023-12/
│       └── reunion.jpg
└── report.json
```

## Instalación

### 1. Requisitos

- Python 3.11+
- API Key de Anthropic

### 2. Configurar API Key

Obtén tu API key en: https://console.anthropic.com/settings/keys

```bash
# macOS/Linux - agregar a ~/.zshrc o ~/.bashrc
export ANTHROPIC_API_KEY="sk-ant-api03-..."

# Recargar
source ~/.zshrc
```

**Windows (PowerShell):**
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-api03-..."

# O permanente:
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-api03-...", "User")
```

### 3. Instalar dependencias con soporte para UI

```bash
cd photo_sorter
# Crear entorno virtual con Python 3.14 (incluye tkinter via python-tk@3.14)
/usr/local/bin/python3.14 -m venv venv_ui
source venv_ui/bin/activate
pip install anthropic Pillow
```

> Si usas Python 3.11 o 3.12, instala primero: `brew install python-tk@3.11`

## Uso

### Interfaz Gráfica (recomendado)

```bash
source venv_ui/bin/activate
python photo_sorter_ui.py
```

La UI permite:
- Seleccionar carpetas con el explorador de archivos
- Ingresar / gestionar la API Key desde la propia ventana
- Ver el progreso imagen por imagen en tiempo real
- Analizar el costo estimado antes de procesar
- Cancelar el proceso en cualquier momento
- Configuración persistente entre sesiones

### CLI — Básico

```bash
python photo_sorter.py --input ~/Fotos --output ~/FotosOrganizadas
```

### Dry-run (ver qué haría sin ejecutar)

```bash
python photo_sorter.py --input ~/Fotos --output ~/FotosOrganizadas --dry-run
```

### Mover en vez de copiar

```bash
python photo_sorter.py --input ~/Fotos --output ~/FotosOrganizadas --mode move
```

### Con más workers (más rápido, más costo)

```bash
python photo_sorter.py --input ~/Fotos --output ~/FotosOrganizadas --workers 4 --delay 0.05
```

### Sin subcarpetas de fecha

```bash
python photo_sorter.py --input ~/Fotos --output ~/FotosOrganizadas --date-buckets none
```

### Saltar confirmación

```bash
python photo_sorter.py --input ~/Fotos --output ~/FotosOrganizadas --yes
```

## Opciones

| Opción | Default | Descripción |
|--------|---------|-------------|
| `--input`, `-i` | (requerido) | Directorio de entrada |
| `--output`, `-o` | (requerido) | Directorio de salida |
| `--mode` | `copy` | `copy` o `move` |
| `--max-categories` | `20` | Límite de categorías |
| `--min-confidence` | `0.35` | Confianza mínima (menor va a `otros/`) |
| `--date-buckets` | `ym` | `ym` (YYYY-MM) o `none` |
| `--unknown-date-name` | `unknown-date` | Carpeta para fechas desconocidas |
| `--delay` | `0.1` | Segundos entre llamadas API |
| `--workers` | `2` | Workers concurrentes |
| `--dry-run` | `false` | Solo simular, no mover/copiar |
| `--no-csv` | `false` | No generar CSV |
| `--yes`, `-y` | `false` | Saltar confirmación |
| `--verbose`, `-v` | `false` | Más detalles |

## Categorías

- `mascotas-perros`, `mascotas-gatos`, `mascotas-otros`
- `personas-selfies`, `personas-retratos`, `personas-grupos`
- `lugares-paisajes`, `lugares-ciudad`, `lugares-interiores`
- `paseos-viajes`
- `actividades-eventos`, `actividades-deporte`
- `comida-bebida`
- `construccion-obra`, `construccion-materiales`, `construccion-planos`
- `memes`, `capturas-pantalla`
- `arte-digital`
- `otros`

## Reportes

Después de ejecutar, encontrarás en el directorio de salida:

**report.json:**
```json
[
  {
    "filename": "foto1.jpg",
    "category": "mascotas-perros",
    "date_bucket": "2024-01",
    "labels": ["perro", "mascota", "golden retriever", "parque"],
    "confidence": 0.95,
    "destination": "/output/mascotas-perros/2024-01/foto1.jpg",
    "date_source": "exif",
    "error": null,
    "from_cache": false
  }
]
```

**report.csv:** Mismo contenido en formato CSV.

## Cache

El programa guarda un cache en `.photo_sorter_cache.db` para no reprocesar imágenes ya analizadas. Esto ahorra tiempo y dinero en ejecuciones posteriores.

## Fechas

Prioridad para obtener YYYY-MM:
1. EXIF `DateTimeOriginal`
2. EXIF `DateTime` / `DateTimeDigitized`
3. Fecha de modificación del archivo (`mtime`)
4. `unknown-date` si todo falla

## Costos

Estimación aproximada: ~$0.01 USD por imagen (depende del tamaño).

El programa muestra una estimación antes de procesar y pide confirmación.

## Empaquetar como ejecutable

### macOS

```bash
pip install pyinstaller
pyinstaller --onefile --name photo_sorter photo_sorter.py
```

El ejecutable estará en `dist/photo_sorter`.

### Windows

```bash
pip install pyinstaller
pyinstaller --onefile --name photo_sorter.exe photo_sorter.py
```

El ejecutable estará en `dist\photo_sorter.exe`.

### Uso del ejecutable

```bash
./dist/photo_sorter --input ~/Fotos --output ~/Organizado
```

Nota: Aún necesitas configurar `ANTHROPIC_API_KEY` como variable de entorno.

## Solución de problemas

### "ANTHROPIC_API_KEY no está configurada"
Verifica que exportaste la variable correctamente:
```bash
echo $ANTHROPIC_API_KEY
```

### "Pillow no instalado"
```bash
pip install Pillow
```

### Rate limits
Aumenta el delay o reduce workers:
```bash
--delay 1.0 --workers 1
```

### Imágenes muy grandes
El programa redimensiona automáticamente imágenes que excedan los límites de Claude (20MB o 8000px).
