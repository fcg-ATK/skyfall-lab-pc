@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_project.ps1" -Mode Launch %*
set "SKYFALL_EXIT=%ERRORLEVEL%"
if not "%SKYFALL_EXIT%"=="0" pause
exit /b %SKYFALL_EXIT%
