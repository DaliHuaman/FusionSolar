@echo off
REM Instalacion unica en la PC de Kallpa (Windows). Requiere Python 3.10+ y Git.
REM El codigo puede quedar dentro de OneDrive (carpeta Indicadores); el entorno Python y el estado
REM local se crean en %LOCALAPPDATA%\FusionSolar para que OneDrive no sincronice miles de archivos.
cd /d "%~dp0.."
set "BASE=%LOCALAPPDATA%\FusionSolar"
if not exist "%BASE%" mkdir "%BASE%"
if not exist "%BASE%\venv" ( py -3 -m venv "%BASE%\venv" || goto :error )
call "%BASE%\venv\Scripts\activate.bat" || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requirements.txt || goto :error
python -m playwright install chromium || goto :error
if not exist .env ( copy .env.example .env >nul & echo Se creo .env: ABRELO y completa usuario/clave de FusionSolar y el token de GitHub. )
echo.
echo Instalacion lista. Siguiente paso: completar .env, probar con scripts\probar.bat y luego ejecutar scripts\instalar_tarea.ps1
exit /b 0
:error
echo ERROR en la instalacion.
exit /b 1
