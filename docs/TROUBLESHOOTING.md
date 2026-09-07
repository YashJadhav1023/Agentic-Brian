# Troubleshooting

## 1. Worktree Sandbox Issues

### Cannot apply sandbox changes: canonical working tree has uncommitted user changes
**Symptom** — `worktree approve` or `POST /api/worktrees/approve` fails with:
```
Cannot apply sandbox changes: canonical working tree has N uncommitted user changes.
Commit or stash your changes in ... before applying.
```
**Cause** — The safe sandbox guard enforces that sandbox merges never clobber local developer edits in progress.
**Fix** — Stash or commit your uncommitted changes in the canonical repository, then retry:
```bash
git stash
python3 scripts/brain.py worktree approve <task_id> --confirm
git stash pop
```

### Stale or orphaned worktrees
**Symptom** — Unfinished worktrees remain in `runtime/sandboxes/`.
**Fix** — Inspect worktree status, view diff, and clean or recover:
```bash
python3 scripts/brain.py worktree status
python3 scripts/brain.py worktree diff <task_id>
python3 scripts/brain.py worktree reject <task_id> --confirm   # if unwanted
python3 scripts/brain.py worktree cleanup --confirm            # prune stale >24h
```

---

## 2. Mission Control Security & API Issues

### Mutating endpoint returns 401 Unauthorized
**Symptom** — `POST /api/dispatch`, `/api/continue`, or `/api/worktrees/*` returns:
```json
{"error": "Unauthorized", "message": "Valid Bearer token required"}
```
**Cause** — Missing or invalid `Authorization: Bearer <token>` header.
**Fix** — Include the bearer token generated in `runtime/mission_control.token` (or set via `MISSION_CONTROL_AUTH_TOKEN`):
```bash
TOKEN=$(cat runtime/mission_control.token)
curl -X POST http://127.0.0.1:3333/api/dispatch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"instruction": "..."}'
```

### API returns 403 Forbidden
**Symptom** — Any request with an external `Origin` header returns `403 Forbidden`.
**Cause** — Strict CORS protection rejects origins other than `http://127.0.0.1:*` and `http://localhost:*`.
**Fix** — Connect directly from localhost/loopback or verify your browser origin.

### API returns 429 Too Many Requests
**Symptom** — `/api/dispatch` or `/api/continue` returns:
```json
{"error": "Too Many Requests", "message": "Execution rate limit exceeded (30 req/min). Try again later."}
```
**Cause** — Sliding-window rate limiter triggered (maximum 30 execution requests per minute).
**Fix** — Back off and wait for the window to slide (up to 60 seconds).

---

## 3. Account 2 & Agent Execution

### Account 2 task succeeds but returns nothing
**Symptom** — Exit `0`, `status: SUCCESS`, empty response, and stderr containing:
```
no output produced — a tool required the "command" permission that headless mode
cannot prompt for, so it was auto-denied.
```
**Cause** — Headless print mode cannot prompt for tool permissions interactively, so the tool was auto-denied.
**Fixes, least privileged first**:
1. Re-run the mutating task with `--allow-tool-permissions`:
   ```bash
   python3 scripts/brain.py plan "Write test" --allow-tool-permissions
   ```
2. Add a scoped rule under `permissions.allow` in `~/.gemini/antigravity-ide/settings.json`.

### `actual_model` is always `unknown`
**Working as intended**. The Antigravity CLI output format does not report the serving model identifier, and neither Kiro nor Cline report one. `requested_model` records what was requested; fabricating `actual_model` would corrupt audit logs.

### Agent reported OFFLINE
```bash
python3 scripts/brain.py health --agent antigravity-account-2 --deep
```
- *binary not found* — check `command` in `config/providers.json`; verify `which agy`.
- *profile directory missing / not writable* — confirm `~/.gemini/antigravity-ide` permissions.
- *session probe timed out* — network or provider outage; deep probe results are cached for 300 s.

### Task stuck in BLOCKED
Check file locks or health pre-flight failure:
```bash
ls locks/
python3 scripts/brain.py status
```
Locks carry a TTL and auto-reclaim upon expiration.

### Task stuck in RUNNING after a crash
```bash
python3 -c "from tasks.manager import TaskManager; print(TaskManager().recover_orphaned_tasks())"
```
