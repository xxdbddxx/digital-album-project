@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart_backend.ps1"
if errorlevel 1 (
    echo.
    echo [ERROR] Backend restart failed.
    pause
    exit /b 1
)

endlocal
exit /b 0
