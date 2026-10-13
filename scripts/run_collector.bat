@echo off
REM Lo ejecuta el Programador de tareas cada 30 min.
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
if not exist state\logs mkdir state\logs
python run.py >> state\logs\tarea.log 2>&1
