# Mission Control: Local Production Deployment & Operations Guide

## 1. System Architecture & Boundaries

Universal AI Mission Control operates strictly within a **local production boundary**. Designed for zero-trust local workstation environments, it integrates multiple AI orchestrators (Antigravity IDE/CLI, Cline, Kiro CLI, and direct API providers) while maintaining complete isolation between process sandboxes, credential stores, and network interfaces.

### Core Architectural Invariants:
1. **Loopback-Only Interface**: Bound exclusively to local loopback (`127.0.0.1`, `localhost`, `::1`). External network interfaces (including `0.0.0.0` and LAN IPs) are actively blocked at the socket initialization layer with `SecurityError`.
2. **Process Non-Interference**: Strict isolation of external agent processes. In particular, the primary Antigravity IDE GUI process (PID `3809`) is recognized as a protected system entity that is never signaled, inspected destructively, or terminated.
3. **Keyring & Profile Isolation**: User credentials, Google OAuth tokens, and `~/.gemini` profile assets remain read-only. Runtime credentials use isolated profiles or `secret://` references managed by `CredentialManager`.
4. **Zero Cloud Ingress**: The Mission Control server does not expose public open ports. Remote observability or control must traverse authenticated, zero-trust tunnels (e.g. Cloudflare Tunnel with Cloudflare Access).

---

## 2. Configuration & Network Interfaces

### Environment Variables
| Variable | Default | Purpose | Permitted Values |
|---|---|---|---|
| `BRAIN_HOST` | `127.0.0.1` | Network interface to bind HTTP listener | `127.0.0.1`, `localhost`, `::1` |
| `BRAIN_PORT` | `3333` | HTTP port for dashboard server | Any free TCP port (`1024-65535`) |
| `MISSION_CONTROL_AUTH_TOKEN` | (auto-generated) | Pre-shared master Bearer token | Min 32-character string |

### Host Binding Enforcement
Any attempt to launch the dashboard server or execute the CLI with an unauthorized host interface (such as `0.0.0.0` or a public IP) is immediately rejected:
```bash
# Valid execution:
python3 scripts/brain.py dashboard --port 3333
python3 scripts/brain.py dashboard --host 127.0.0.1 --port 3333

# Forbidden execution (exits with code 1):
python3 scripts/brain.py dashboard --host 0.0.0.0
# Output: Error: Non-local host binding '0.0.0.0' is strictly rejected.
```

---

## 3. Authentication & Security Lifecycle

### 3.1 Token Generation & Persistence
- **Storage Location**: `runtime/mission_control.token`
- **File Permissions**: Strictly enforced to `0o600` (read/write by owner only).
- **Generation Logic**: On initial startup, if neither `MISSION_CONTROL_AUTH_TOKEN` nor `runtime/mission_control.token` exists, the server securely generates a 32-byte URL-safe cryptographic token via `secrets.token_urlsafe(32)`.

### 3.2 Authorization Header
All sensitive HTTP GET, POST, PATCH, and DELETE endpoints require the `Authorization` header:
```http
GET /api/overview HTTP/1.1
Host: 127.0.0.1:3333
Authorization: Bearer <your-session-token>
```
Unauthenticated requests receive:
```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
WWW-Authenticate: Bearer realm="MissionControl"

{"error": "Unauthorized", "message": "Valid Bearer token required for /api/overview"}
```

### 3.3 Server-Sent Events (SSE) Authentication
Because browser `EventSource` APIs cannot send custom HTTP headers, `/api/events/stream` supports dual-mode authentication:
1. **HTTP Header**: `Authorization: Bearer <token>` (used by backend daemons and automated test harnesses).
2. **Query Parameter**: `?token=<token>` or `?access_token=<token>` (used by browser EventSource clients).

Example browser connection:
```javascript
const eventSource = new EventSource(`/api/events/stream?token=${encodeURIComponent(authToken)}`);
```

### 3.4 Public Allow-List
The following endpoints are intentionally accessible without authentication:
- `/`: Main dashboard HTML user interface.
- `/static/*`: Static styling, icons, scripts.
- `/api/token`: Secure loopback handshake returning the session token (protected by loopback origin validation).
- `/api/health`: Liveness probe for local process supervisors.
- `/api/status`: Basic health counters.

---

## 4. Local Operational Controls

### 4.1 Stale PID File Tracking (`runtime/dashboard.pid`)
To prevent dual-instance collisions and resource conflicts:
- On startup, `acquire_pid_file()` reads `runtime/dashboard.pid`.
- If an existing process ID is found:
  - If the PID matches protected GUI PID `3809`, the stale PID file is cleaned up without signaling or touching the process.
  - If the process is alive and identified as another running Mission Control instance, the new instance prints an error and exits cleanly.
  - If the process is dead (stale crash artifact), the file is automatically unlinked and recreated with the new PID.
- Permissions of `runtime/dashboard.pid` are set to `0o600`.
- On graceful termination or process exit, `release_pid_file()` unlinks the file.

### 4.2 Graceful Shutdown & Signal Handling
- The server registers signal handlers for `SIGINT` (Ctrl+C) and `SIGTERM`.
- When a termination signal is received:
  1. The server initiates `server.shutdown()` in a background worker thread.
  2. Active HTTP connections are drained.
  3. The listening socket is cleanly closed.
  4. `runtime/dashboard.pid` is released.
  5. The shutdown process logs termination cleanly without traceback noise.

### 4.3 Friendly Port Collision Handling
When the configured port (default 3333) is already in use by another service:
- Catches `errno.EADDRINUSE` (errno 98).
- Displays clear operator remediation instructions:
  ```
  Error: Port 3333 is already in use on 127.0.0.1. Please select another port via BRAIN_PORT or --port.
  ```
- Prevents raw Python tracebacks.

### 4.4 Startup Health Validation
Prior to binding the network socket, `run_startup_checks()` verifies:
1. **Network Interface**: Confirms host is within `ALLOWED_LOOPBACK_HOSTS`.
2. **Filesystem Permissions**: Confirms `runtime/`, `runtime/logs/`, and `runtime/audit/` exist and are writable.
3. **Security Token**: Validates that the authentication token is present, non-empty, and stored with `0o600` permissions.
4. **Registry Initialization**: Confirms that AI providers and accounts are loaded in the registry.

---

## 5. Runtime Log Rotation (`brain/analytics/log_rotator.py`)

High-volume append-only ledgers in `runtime/` are protected against unbounded growth:
- **Target Files**:
  - `runtime/logs/routing_history.jsonl`
  - `runtime/logs/events.jsonl`
  - `runtime/audit/audit.jsonl`
  - `runtime/logs/token_telemetry.jsonl`
- **Rotation Parameters**:
  - Threshold: 10 MB (`10,485,760` bytes).
  - Backup Generations: 5 generations (`.jsonl.1` through `.jsonl.5`).
- **Atomic Cycling**:
  When a file exceeds 10 MB, older backups are shifted (`.4` -> `.5`, `.3` -> `.4`, etc.), the current file is moved to `.1`, and a fresh empty file is created with `0o600` mode.
- **Manual / Scheduled Invocation**:
  ```bash
  # Execute log rotation via CLI
  python3 scripts/brain.py maintenance rotate-logs

  # Emit machine-readable JSON summary
  python3 scripts/brain.py maintenance rotate-logs --json
  ```

---

## 6. Secure Remote Ingress via Cloudflare Tunnel (Optional)

If remote access to Mission Control is required across devices, **never bind to 0.0.0.0 or open firewall ports**. Use Cloudflare Tunnel with Cloudflare Access.

### Ingress Architecture:
```
[Authorized User] 
       │ HTTPS
       ▼
[Cloudflare Edge + Zero Trust Access Policy]
       │ Encrypted Outbound Tunnel
       ▼
[cloudflared daemon on local workstation]
       │ HTTP Loopback
       ▼
[Mission Control on 127.0.0.1:3333]
```

### Setup Steps:
1. Install `cloudflared` on the local machine:
   ```bash
   sudo apt-get install cloudflared
   ```
2. Authenticate and create a named tunnel:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create mission-control-tunnel
   ```
3. Configure `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: <tunnel-uuid>
   credentials-file: /home/setoo/.cloudflared/<tunnel-uuid>.json

   ingress:
     - hostname: mission-control.internal.yourdomain.com
       service: http://127.0.0.1:3333
     - service: http_status:404
   ```
4. Run the tunnel service:
   ```bash
   cloudflared tunnel run mission-control-tunnel
   ```
5. In the Cloudflare Zero Trust Dashboard, attach an Access Application with multi-factor authentication (MFA / Google Workspace) protecting `mission-control.internal.yourdomain.com`.

---

## 7. Troubleshooting & Operator Runbook

| Symptom | Probable Cause | Remediation |
|---|---|---|
| `HTTP 401 Unauthorized` on API call | Missing or invalid Bearer token | Inspect `runtime/mission_control.token` or set `Authorization: Bearer <token>` |
| `SecurityError: Binding to non-local interface...` | Server configured with external host | Use `--host 127.0.0.1` or unset `BRAIN_HOST` |
| `Error: Mission Control dashboard is already running` | Another instance active or stale PID | Check `ps aux \| grep dashboard.py`. If dead, delete `runtime/dashboard.pid` |
| `Error: Port 3333 is already in use` | Port collision | Check `ss -tulpn \| grep 3333`. Run with `--port 3334` |
| SSE stream disconnects in browser | Token expired or missing in URL | Refresh page to re-handshake with `/api/token` |
| `runtime/logs/*.jsonl` exceeds 10MB | Automatic rotation pending | Run `python3 scripts/brain.py maintenance rotate-logs` |
