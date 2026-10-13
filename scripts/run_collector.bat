@echo off
REM Lo ejecuta el Programador de tareas cada 30 min.
cd /d "%~dp0.."
call "%LOCALAPPDATA%\FusionSolar\venv\Scripts\activate.bat"
if not exist "%LOCALAPPDATA%\FusionSolar\state\logs" mkdir "%LOCALAPPDATA%\FusionSolar\state\logs"
python run.py >> "%LOCALAPPDATA%\FusionSolar\state\logs\tarea.log" 2>&1
