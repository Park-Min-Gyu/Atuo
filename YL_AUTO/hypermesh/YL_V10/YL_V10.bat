@echo off
rem YL_V10 : double click -> settings window. YL_V10.ps1 must be in the same folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0YL_V10.ps1"
set "YL_RC=%ERRORLEVEL%"
if not defined YL_JOB_FILE ( echo. & pause )
exit /b %YL_RC%
