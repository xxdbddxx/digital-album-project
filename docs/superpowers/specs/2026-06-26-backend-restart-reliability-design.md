# Backend Restart Reliability Design

## Goal

Make `restart_backend.bat` reliably terminate stale backend services, release
ports 8765 and 8888, and start exactly one Flask process and one voice process.

## Root Causes

- The old cleanup matched the virtual-environment executable path, but Windows
  reports the running process as the base Python executable.
- Relative command lines such as `services\voice_server.py` did not include the
  absolute backend directory, so stale processes escaped cleanup.
- Windows Defender Controlled Folder Access is enabled for `Documents`.
  Python could read SQLite files but could not create journal files, causing
  `sqlite3.OperationalError: unable to open database file`.

## Design

- Keep `restart_backend.bat` as the double-click entry point.
- Put process discovery, cleanup, launch, and health checks in
  `scripts/restart_backend.ps1`.
- Treat listeners on ports 8765 and 8888 as authoritative stale backend
  processes. Also clean processes launched by the dedicated backend windows.
- Start both services with absolute Python and script paths and an explicit
  backend working directory.
- Wait for both ports to be released before launching and wait for both ports
  to listen before reporting success.
- Probe SQLite journal creation before launch. If Controlled Folder Access
  blocks it, report the exact Python executables that must be allowed.

## Out Of Scope

- Do not merge or modify `.worktrees/multimodal-user-emotion`.
- Do not rotate credentials automatically.
- Do not change backend application behavior.
