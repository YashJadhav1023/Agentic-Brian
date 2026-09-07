# Troubleshooting Guide

### 1. Account 2 Executable Error
- **Symptom:** `Antigravity CLI binary not found`
- **Fix:** Ensure `/home/setoo/.gemini/bin/agy` or `~/.local/bin/antigravity` is executable.

### 2. Account 2 Data Directory Missing
- **Symptom:** `Data directory ~/.gemini/antigravity-ide does not exist`
- **Fix:** Open Antigravity IDE once to initialize the session data directory.

### 3. Task Blocked on File Lock
- **Symptom:** Task enters `BLOCKED` status with `Failed to acquire lock for file`.
- **Fix:** Check `locks/` for unreleased `.lock` files. Stale locks expire after 600 seconds.

### 4. Port 3333 Already In Use
- **Fix:** Run dashboard on custom port:
  ```bash
  BRAIN_PORT=3335 python3 ui/dashboard/dashboard.py
  ```
