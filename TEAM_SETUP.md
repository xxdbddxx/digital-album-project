# Team Setup Guide

This repository does not commit machine-specific runtime state. Each teammate must configure their own backend secrets, local IP, Python environment, and ESP32 serial port before flashing.

## What is intentionally not committed

- `backend/.env.local`: API keys and local secrets.
- `backend/.venv/`: local Python virtual environment.
- `sdkconfig`: local firmware config containing the current PC IP.
- `backend/*.db`, `backend/upload_photos/`, `backend/services/user_records/`, `backend/output/`: local runtime data.
- `build/`: ESP-IDF build output.
- Large local media/model assets such as `backend/music/*.mp3` and Piper voices.

If a teammate needs demo photos/music, share a separate sanitized demo asset package instead of committing personal runtime data or API keys.

## First-time setup

From the repository root:

```powershell
cd C:\Users\<you>\Documents\esp_projects\digital-album-project
```

Create and populate `backend/.env.local` from `backend/.env.example`:

```powershell
Copy-Item backend\.env.example backend\.env.local
notepad backend\.env.local
```

Install backend dependencies into the local venv:

```powershell
cd backend
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..
```

## Configure firmware for this computer's IP

The ESP32 firmware must connect to the backend running on the teammate's own PC. Run:

```powershell
.\scripts\configure_local_ip.ps1
```

This rewrites local `sdkconfig` values:

```text
CONFIG_SERVER_URL="http://<this-pc-ip>:8765"
CONFIG_VA_WS_URI="ws://<this-pc-ip>:8888"
```

If auto-detection picks the wrong adapter, pass the IP explicitly:

```powershell
.\scripts\configure_local_ip.ps1 -IpAddress 192.168.1.23
```

Do not commit `sdkconfig`; it is intentionally ignored because every teammate has a different IP.

## Build and flash

Always use the repository wrapper:

```powershell
.\scripts\idf.ps1 build
.\scripts\idf.ps1 -p COM11 flash monitor
```

If your board is on a different serial port:

```powershell
.\scripts\idf.ps1 -p COM12 flash monitor
```

Do not use PlatformIO for this project.

## Start backend services

Start or restart both Flask and Voice backends:

```powershell
.\restart_backend.bat
```

Expected ports:

- Web UI / Flask: `8765`
- Voice WebSocket: `8888`

Open:

```text
http://127.0.0.1:8765
```

## Diagnose a teammate machine

Run:

```powershell
.\scripts\doctor.ps1
```

For a non-default serial port:

```powershell
.\scripts\doctor.ps1 -Port COM12
```

Common failures:

- `sdkconfig IP matches first LAN IP = FAIL`: run `.\scripts\configure_local_ip.ps1`, then rebuild and flash.
- `Port 8765 listening = FAIL`: run `.\restart_backend.bat`.
- `Port 8888 listening = FAIL`: check `backend/.env.local`, Python venv, and voice backend terminal logs.
- `Backend .env.local = FAIL`: create it from `backend/.env.example`.
- ESP-IDF compile write errors under Documents: Windows Defender Controlled Folder Access may block CMake/Ninja/GCC writes. Build in a non-protected directory or allow the ESP-IDF tools in Defender.

## Quick teammate checklist

```powershell
Copy-Item backend\.env.example backend\.env.local
notepad backend\.env.local
cd backend
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..
.\scripts\configure_local_ip.ps1
.\scripts\idf.ps1 build
.\scripts\idf.ps1 -p COM11 flash monitor
.\restart_backend.bat
.\scripts\doctor.ps1
```
