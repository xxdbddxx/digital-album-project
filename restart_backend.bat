@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "BACKEND_DIR=%PROJECT_ROOT%backend"
set "PYTHON_EXE=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "WEB_WINDOW=Digital Album - Web Backend"
set "VOICE_WINDOW=Digital Album - Voice Backend"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Backend Python not found:
    echo %PYTHON_EXE%
    pause
    exit /b 1
)

echo Closing existing backend PowerShell windows...
taskkill /F /T /FI "WINDOWTITLE eq %WEB_WINDOW%*" >nul 2>&1
taskkill /F /T /FI "WINDOWTITLE eq %VOICE_WINDOW%*" >nul 2>&1

echo Stopping orphaned digital album backend processes...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$backend=[IO.Path]::GetFullPath('%BACKEND_DIR%');" ^
  "$targets=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {" ^
  "  $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and (" ^
  "    ($_.CommandLine -like '*server.py*' -or $_.CommandLine -like '*voice_server.py*') -and (" ^
  "      $_.ExecutablePath -eq '%PYTHON_EXE%' -or $_.CommandLine -like ('*' + $backend + '*')" ^
  "    )" ^
  "  )" ^
  "};" ^
  "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Start-Sleep -Milliseconds 800"

echo Starting Flask backend on port 8765...
start "%WEB_WINDOW%" /D "%BACKEND_DIR%" powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -Command ^
  "$Host.UI.RawUI.WindowTitle='%WEB_WINDOW%'; & '%PYTHON_EXE%' 'server.py'"

echo Starting voice backend on port 8888...
start "%VOICE_WINDOW%" /D "%BACKEND_DIR%" powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -Command ^
  "$Host.UI.RawUI.WindowTitle='%VOICE_WINDOW%'; & '%PYTHON_EXE%' 'services\voice_server.py'"

echo.
echo Backend restart completed.
echo Web UI: http://127.0.0.1:8765
powershell.exe -NoProfile -Command "Start-Sleep -Seconds 3"
endlocal
exit /b 0
