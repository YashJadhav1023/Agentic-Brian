# Agentic Brain Security Architecture & Threat Model

## 1. Core Principles

The Agentic Brain multi-agent orchestration platform enforces defense-in-depth across five security pillars:
1. **Least Privilege by Default**: Agents run with minimal permissions; tool auto-approval is disabled.
2. **Deterministic Isolation**: Code-modifying tasks execute inside throwaway Git worktree sandboxes, never touching the canonical working tree directly.
3. **Local-Only Attack Surface**: Mission Control binds exclusively to loopback (`127.0.0.1`); remote network access is physically blocked by the server configuration.
4. **Strong Authentication & CORS**: Mutating and execution endpoints require cryptographic Bearer tokens. Cross-origin requests from non-local origins are rejected with 403 Forbidden.
5. **Zero-Leak Secret Hygiene**: Tokens, keys, credentials, and raw environment dumps are strictly excluded from git tracking, telemetry logs, and public API responses.

---

## 2. Least-Privilege Execution Model

### Default Restricted State
- All agents (`antigravity-account-1`, `antigravity-account-2`, `kiro-cli`, `cline`) run with:
  ```json
  "dangerously_skip_permissions": false
  ```
- Headless read-only exploration, architecture reviews, and code reasoning execute safely without elevated tool permissions.

### Explicit Escalation Protocol
- When a task genuinely requires tool execution (e.g., executing shell commands, file writing), the operator must explicitly opt in per task:
  ```bash
  python3 scripts/brain.py plan "Refactor service" --allow-tool-permissions
  ```
- Escalation is audited on the event bus:
  - `PERMISSION_ESCALATION_REQUESTED`
  - `PERMISSION_ESCALATION_GRANTED` (or `DENIED`)
- Permissions never silently persist across subsequent tasks.

---

## 3. Git Worktree Sandboxing Architecture

Mutating tasks are automatically recognized and routed into dedicated Git worktree sandboxes:
- **Sandbox Root**: `runtime/sandboxes/agentic-task-<task_id>`
- **Branch**: `agentic/task/<task_id>`
- **Metadata Protection**: Per-task metadata is saved outside the worktree (`runtime/sandboxes/<task_id>.meta.json`) to prevent untracked file contamination.
- **Canonical Dirty Guard**: Merging changes into the canonical repository (`apply()`) is blocked if the user's working tree contains uncommitted edits (`check_canonical_dirty()`).
- **Human Approval Gate**: Merging (`apply()`) or destroying (`reject()`) requires explicit confirmation (`confirm=True`).

---

## 4. Mission Control API Security

### Network Binding
- The HTTP server binds exclusively to `127.0.0.1`. Binds to `0.0.0.0` or wildcard interfaces are forbidden.

### Security Headers
Every response includes:
```http
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self'; img-src 'self' data:;
```

### CORS Policy
- Allowed Origins: `http://127.0.0.1:*`, `http://localhost:*`.
- Any external origin (e.g. `https://malicious-domain.com`) receives `403 Forbidden` on both preflight (`OPTIONS`) and standard requests.

### Bearer Token Authentication
- **Token Source**:
  1. `MISSION_CONTROL_AUTH_TOKEN` environment variable, or
  2. `runtime/mission_control.token` (generated on first boot with `secrets.token_urlsafe(32)` and file permissions `0600`).
- **Protected Endpoints**:
  - `POST /api/dispatch`
  - `POST /api/continue`
  - `POST /api/worktrees/approve`
  - `POST /api/worktrees/reject`
  - `POST /api/worktrees/cleanup`
  - `POST /api/worktrees/recover`
- Requests missing a valid `Authorization: Bearer <token>` header return `401 Unauthorized` and emit `AUTH_FAILURE` events. Valid requests emit `AUTH_SUCCESS`. Constant-time comparison (`hmac.compare_digest`) prevents timing side-channels.

### Rate Limiting
- Execution endpoints (`/api/dispatch`, `/api/continue`) are bounded by a sliding-window rate limiter:
  - Threshold: 30 requests / minute.
  - Exceeding the threshold returns `429 Too Many Requests`.

### Destructive Action Verification
- Worktree modification endpoints require `{"confirm": true}` in the request body. Missing confirmation returns `400 Bad Request`.

---

## 5. Secret Redaction & Zero-Leak Audit

| Category | Protection Mechanism |
|---|---|
| Repository State | `.gitignore` excludes `runtime/`, `*.token`, `*.db`, `*.lock`, `*.jsonl`, `.env` |
| Status Endpoints | `/api/status` and `/api/agents` strip all API keys, bearer tokens, and credentials |
| Telemetry Logs | `events.jsonl` records metadata and paths, never raw authorization headers |
| Shell History | Token files are accessed via filesystem, not passed as plain CLI arguments |
