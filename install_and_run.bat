@echo off
:: Photo Sorter — instalador y launcher para Windows
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"

:: Buscar Python 3.11+
set "PYTHON="
for %%P in (python python3) do (
    where %%P >nul 2>&1
    if !errorlevel! == 0 (
        for /f %%V in ('%%P -c "import sys; print(sys.version_info >= (3,11))" 2^>nul') do (
            if "%%V"=="True" set "PYTHON=%%P"
        )
    )
)

if "%PYTHON%"=="" (
    echo.
    echo   ERROR: se requiere Python 3.11 o superior.
    echo   Descargalo en https://www.python.org/downloads/
    echo   Asegurate de marcar "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)

:: Crear entorno virtual si no existe
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo   Creando entorno virtual...
    %PYTHON% -m venv "%VENV_DIR%"
)

set "PIP=%VENV_DIR%\Scripts\pip"
set "PYEXE=%VENV_DIR%\Scripts\python"

:: Instalar dependencias
echo   Instalando dependencias...
"%PIP%" install --quiet --upgrade pip
"%PIP%" install --quiet anthropic "Pillow>=10" "keyring>=25" pillow-heif

:: SDKs opcionales
for %%S in (google-generativeai openai groq) do (
    "%PIP%" install --quiet %%S 2>nul
)

echo   Listo. Iniciando Photo Sorter...
echo.
cd /d "%SCRIPT_DIR%"
"%PYEXE%" photo_sorter_ui.py
