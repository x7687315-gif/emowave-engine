@echo off
rem ============================================================
rem  EmoWave diagnostic launcher - console stays visible so
rem  tracebacks are readable if the app crashes on startup.
rem ============================================================
set "PY=C:\Users\86159\AppData\Local\Programs\Python\Python39\python.exe"
set "HERE=%~dp0"
"%PY%" "%HERE%main_app.py"
if errorlevel 1 pause
