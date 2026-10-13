@echo off
REM Prueba manual: abre el navegador (visible), extrae, guarda en Excel y NO publica.
cd /d "%~dp0.."
call "%LOCALAPPDATA%\FusionSolar\venv\Scripts\activate.bat"
python run.py --visible --sin-publicar
pause
