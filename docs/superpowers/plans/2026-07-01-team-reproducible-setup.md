# Team Reproducible Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make teammate setup reproducible without committing secrets, virtual environments, local databases, build outputs, or machine-specific `sdkconfig`.

**Architecture:** Keep runtime state local and ignored. Add small PowerShell utilities that write local IP settings into `sdkconfig` and diagnose the expected Windows/ESP-IDF/backend environment. Document the exact clone-to-run workflow in a team handoff file.

**Tech Stack:** PowerShell, ESP-IDF wrapper `scripts/idf.ps1`, Python backend virtualenv `backend/.venv`, unittest.

## Global Constraints

- Do not commit `backend/.env.local`, API keys, `backend/.venv`, `sdkconfig`, `backend/*.db`, upload folders, or `build/`.
- Always use `.\scripts\idf.ps1` for ESP-IDF operations.
- Backend ports are `8765` and `8888`.
- Serial port default is `COM11`.
- Keep existing working behavior; this task is delivery stabilization, not feature development.

---

### Task 1: Backend startup regression coverage

**Files:**
- Modify: `backend/services/voice_server.py`
- Modify: `scripts/restart_backend.ps1`
- Create: `backend/tests/test_voice_import_path.py`

**Interfaces:**
- Produces: `voice_server.py` can import `backend.services.emotion` when launched from `backend`.
- Produces: `restart_backend.ps1` tolerates scalar/array PID collections and already-exited processes.

- [x] Add regression test for importing voice server from `backend` cwd.
- [x] Add project root to `sys.path` before backend package imports.
- [x] Make restart script PID handling robust.
- [x] Run backend tests.

### Task 2: Local machine configuration utilities

**Files:**
- Create: `scripts/configure_local_ip.ps1`
- Create: `scripts/doctor.ps1`
- Create: `scripts/tests/test_configure_local_ip.ps1`

**Interfaces:**
- `configure_local_ip.ps1 [-IpAddress <string>] [-ServerPort <int>] [-VoicePort <int>] [-SdkconfigPath <string>]`
- `doctor.ps1 [-Port <string>]`

- [x] Implement IP detection and `sdkconfig` rewrite.
- [x] Implement environment diagnostics.
- [x] Add a PowerShell test using a temporary `sdkconfig`.

### Task 3: Team setup documentation and commit

**Files:**
- Create: `TEAM_SETUP.md`

**Interfaces:**
- Produces: one handoff doc for teammates explaining what must be local and what is intentionally not committed.

- [x] Document clone, backend setup, IP config, build/flash, startup, and common failure modes.
- [x] Run tests.
- [x] Stage, commit, and push.
