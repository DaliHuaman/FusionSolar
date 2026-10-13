@echo off
REM Instalacion unica en la PC de Kallpa (Windows). Requiere Python 3.10+ y Git.
cd /d "%~dp0.."
if not exist .venv ( py -3 -m venv .venv || goto :error )
call .venv\Scripts\activate.bat || goto :error
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
