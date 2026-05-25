# Photo Sorter

Organiza fotos automáticamente en carpetas por categoría y fecha usando Vision AI.
Soporta **Anthropic Claude**, **Google AI Studio (Gemini)**, **OpenAI (GPT-4o)** y **Groq (Llama Vision)**.

Disponible como **interfaz gráfica (UI)** o **línea de comandos (CLI)**.

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

### Requisitos

- Python 3.11+
- API Key de alguno de los proveedores soportados

### Instalar dependencias

```bash
pip install anthropic Pillow keyring pillow-heif
```

Según el proveedor que uses, instala el SDK correspondiente:

```bash
# Google AI Studio (Gemini) — modelos gratuitos disponibles
pip install google-genai

# OpenAI (GPT-4o)
pip install openai

# Groq (Llama Vision) — modelo gratuito disponible
pip install groq
```

### Obtener una API Key

| Proveedor | URL | Costo |
|-----------|-----|-------|
| Anthropic (Claude) | https://console.anthropic.com/settings/keys | ~$0.001–$0.01/img |
| Google AI Studio | https://aistudio.google.com/app/apikey | Gratuito (cuota diaria) |
| OpenAI | https://platform.openai.com/api-keys | ~$0.0015–$0.01/img |
| Groq | https://console.groq.com/keys | Gratuito (cuota diaria) |

## Uso

### Interfaz gráfica (recomendado)

```bash
python photo_sorter_ui.py
```

1. Selecciona la carpeta de entrada y salida
2. Elige tu proveedor de IA y pega tu API key
3. Selecciona el modelo y formato de organización
4. Haz clic en **Iniciar**

La key queda guardada de forma segura en el Keychain del sistema (macOS) o Credential Manager (Windows). No se almacena en texto plano.

### CLI

```bash
# Con Anthropic (default)
python photo_sorter.py --input ./fotos --output ./organizado

# Con Google AI Studio
python photo_sorter.py --input ./fotos --output ./organizado --provider google --model gemini-2.0-flash

# Con Groq (gratis)
python photo_sorter.py --input ./fotos --output ./organizado --provider groq

# Con OpenAI
python photo_sorter.py --input ./fotos --output ./organizado --provider openai --model gpt-4o-mini
```

### Dry-run (simular sin mover archivos)

```bash
python photo_sorter.py --input ./fotos --output ./organizado --dry-run
```

### Mover en vez de copiar

```bash
python photo_sorter.py --input ./fotos --output ./organizado --mode move
```

## Opciones CLI

| Opción | Default | Descripción |
|--------|---------|-------------|
| `--input`, `-i` | (requerido) | Carpeta de entrada |
| `--output`, `-o` | (requerido) | Carpeta de salida |
| `--provider` | `anthropic` | `anthropic`, `google`, `openai`, `groq` |
| `--model` | (por proveedor) | Modelo a usar |
| `--mode` | `copy` | `copy` o `move` |
| `--min-confidence` | `0.35` | Confianza mínima (menor va a `otros/`) |
| `--date-buckets` | `ym` | `ym`, `y`, `none`, `cat-y-m`, `ym-cat`, `y-cat` |
| `--workers` | `2` | Llamadas simultáneas a la API |
| `--delay` | `0.1` | Segundos entre llamadas |
| `--dry-run` | `false` | Simular sin mover archivos |
| `--recursive`, `-r` | `false` | Buscar en subcarpetas |
| `--yes`, `-y` | `false` | Saltar confirmación |

## Modelos disponibles

### Anthropic (Claude)
| Modelo | Velocidad | Costo est. |
|--------|-----------|------------|
| `claude-3-haiku-20240307` | Rápido | ~$0.001/img |
| `claude-3-5-haiku-20241022` | Medio | ~$0.003/img |
| `claude-3-5-sonnet-20241022` | Preciso | ~$0.010/img |

### Google AI Studio (Gemini)
| Modelo | Velocidad | Costo est. |
|--------|-----------|------------|
| `gemini-2.0-flash` | Rápido | Gratuito |
| `gemini-1.5-flash` | Rápido | Gratuito |
| `gemini-1.5-pro` | Preciso | ~$0.0035/img |

### OpenAI
| Modelo | Velocidad | Costo est. |
|--------|-----------|------------|
| `gpt-4o-mini` | Rápido | ~$0.0015/img |
| `gpt-4o` | Preciso | ~$0.010/img |

### Groq (Llama Vision)
| Modelo | Velocidad | Costo est. |
|--------|-----------|------------|
| `llama-3.2-11b-vision-preview` | Rápido | Gratuito |
| `llama-3.2-90b-vision-preview` | Preciso | ~$0.0009/img |

## Categorías

- `mascotas-perros`, `mascotas-gatos`, `mascotas-otros`
- `personas-selfies`, `personas-retratos`, `personas-grupos`
- `lugares-paisajes`, `lugares-ciudad`, `lugares-interiores`
- `paseos-viajes`
- `actividades-eventos`, `actividades-deporte`
- `comida-bebida`
- `construccion-obra`, `construccion-materiales`, `construccion-planos`
- `memes`, `capturas-pantalla`, `arte-digital`
- `otros`

Las categorías son editables desde la interfaz gráfica.

## Caché

Las imágenes ya analizadas se guardan en `.photo_sorter_cache.db` dentro de la carpeta de salida. Ejecuciones posteriores sobre las mismas imágenes no consumen tokens de la API.

## Reporte

Después de cada ejecución se generan `report.json` y `report.csv` en la carpeta de salida con el detalle de cada imagen procesada.

## Empaquetar como ejecutable

### macOS

```bash
pip install pyinstaller
pyinstaller PhotoSorter.spec
# Resultado: dist/Photo Sorter.app
```

### Windows

```bash
pip install pyinstaller
# Convierte el ícono primero: magick icon.icns icon.ico
pyinstaller PhotoSorter_Windows.spec
# Resultado: dist/PhotoSorter.exe
```

## Solución de problemas

**"No se encontraron imágenes"** — verifica que la carpeta contiene `.jpg`, `.png`, `.webp`, `.heic` u otros formatos soportados.

**Rate limit** — reduce workers o aumenta el delay:
```bash
--workers 1 --delay 1.0
```

**Imágenes HEIC/HEIF** — requiere `pillow-heif`:
```bash
pip install pillow-heif
```

**Error de importación del proveedor** — instala el SDK correspondiente (ver sección Instalación).
