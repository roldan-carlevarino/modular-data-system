@echo off
REM Script para ejecutar el Search Fund Dashboard

echo.
echo ===============================================
echo  Search Fund Dashboard - Streamlit
echo ===============================================
echo.

REM Verificar si Python está instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python no está instalado o no está en PATH
    pause
    exit /b 1
)

REM Ir al directorio del proyecto
cd /d "%~dp0"

REM Instalar dependencias si es necesario
echo Verificando dependencias...
pip install -r requirements.txt >nul 2>&1

echo.
echo Iniciando dashboard...
echo El navegador se abrirá automáticamente en http://localhost:8501
echo.
echo Para detener el dashboard, presiona Ctrl+C
echo.

REM Ejecutar Streamlit
streamlit run app.py

pause
