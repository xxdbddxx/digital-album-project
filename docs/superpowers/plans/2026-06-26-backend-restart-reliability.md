# Backend Restart Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably restart both backend services despite stale relative-path processes and Windows Controlled Folder Access.

**Architecture:** A PowerShell implementation owns process cleanup, port waits,
SQLite write checks, absolute-path launch, and health checks. The BAT file is
only a stable double-click wrapper.

**Tech Stack:** Windows PowerShell 5.1, Python 3.10, Flask, WebSockets, SQLite.

## Global Constraints

- Do not disable Windows Defender.
- Do not modify or merge the multimodal emotion worktree.
- Do not kill unrelated Python processes that do not own backend ports or
  belong to the dedicated backend process tree.

---

### Task 1: Testable Process Matching

**Files:**
- Create: `scripts/restart_backend.ps1`
- Create: `scripts/tests/test_restart_backend.ps1`

- [ ] Add failing assertions for absolute and relative backend command lines.
- [ ] Run the test and confirm the implementation is missing.
- [ ] Implement command-line matching and port-owner discovery.
- [ ] Run the test and confirm all assertions pass.

### Task 2: Reliable Restart Entry Point

**Files:**
- Modify: `restart_backend.bat`
- Modify: `scripts/restart_backend.ps1`

- [ ] Stop dedicated backend process trees and current listeners on 8765/8888.
- [ ] Wait until both ports are free.
- [ ] Launch absolute `server.py` and `services\voice_server.py` paths.
- [ ] Wait until both ports listen and return a nonzero exit code on failure.

### Task 3: Controlled Folder Access Compatibility

**Files:**
- Modify: `scripts/restart_backend.ps1`

- [ ] Probe SQLite journal creation before launch.
- [ ] Print an actionable Defender allowance command when the probe fails.
- [ ] Re-run the write probe and fail before launching if SQLite remains
  blocked.

### Task 4: Verification

- [ ] Run PowerShell regression tests.
- [ ] Force a SQLite write transaction successfully.
- [ ] Execute `restart_backend.bat`.
- [ ] Verify exactly one listener on 8765 and one listener on 8888.
- [ ] Verify HTTP 8765 responds and the voice process remains alive.
