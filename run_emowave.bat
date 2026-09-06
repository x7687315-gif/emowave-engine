@echo off
rem ============================================================
rem  EmoWave (Heart Tide) one-click launcher
rem  Uses Python 3.9 (PyQt5 5.15.2 compatible).
rem  NOTE: the project .venv is Python 3.14 where PyQt5 is broken
rem  ("no Qt platform plugin"), so we deliberately bypass it.
rem ============================================================
set "PYW=C:\Users\86159\AppData\Local\Programs\Python\Python39\pythonw.exe"
set "HERE=%~dp0"
if not exist "%PYW%" (
    echo [EmoWave] Python 3.9 not found: %PYW%
    pause
    exit /b 1
)
start "" "%PYW%" "%HERE%main_app.py"
