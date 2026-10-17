@echo off
rem ============================================================
rem  EmoWave (Heart Tide) one-click launcher
rem  Uses Python 3.9 (PyQt5 5.15.2 compatible).
rem  NOTE: use the system Python 3.9 on purpose — CI runs 3.9 too.
rem  (PyQt5 5.15.11 has no wheel for 3.14; a 3.14 venv fails with
rem   "no Qt platform plugin".)
rem ============================================================
set "PYW=C:\Users\86159\AppData\Local\Programs\Python\Python39\pythonw.exe"
set "HERE=%~dp0"
if not exist "%PYW%" (
    echo [EmoWave] Python 3.9 not found: %PYW%
    pause
    exit /b 1
)
start "" "%PYW%" "%HERE%main_app.py"
