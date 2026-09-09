# Mission Control UI

```bash
python3 ui/dashboard/dashboard.py     # http://127.0.0.1:3333  (BRAIN_PORT to change)
```

**Security Posture:** The server binds strictly to `127.0.0.1`. Remote network binding (`0.0.0.0`) is prohibited. All mutating actions (`/api/dispatch`, `/api/execute`, `/api/continue`, `/api/tasks/cancel`, `/api/memory/add`, `/api/worktrees/*`) require Bearer token authentication (`Authorization: Bearer <token>`). The session token is automatically generated in `runtime/mission_control.token` (permissions `0600`) and seamlessly bootstrapped by the local web UI via `/api/token`.

---

## Interactive Features & Controls

1. **Live Execution Monitor (`#live-execution-widget`):**
   - Active task tracking with live stopwatch elapsed timer (`mm:ss`).
   - 6-stage progression mapping: `Queued` → `Routing` → `Memory` → `Execution` → `Handoff` → `Complete`.
   - Failover badge indicator and continuation budget counters.
2. **Interactive Kanban Board (`#tab-tasks`):**
   - Categorizes tasks into Ready Queue, Running / Active, Completed, and Terminal / Failed columns.
   - Clicking any task card opens the **Task Detail Modal** (`GET /api/task?task_id=...`) showing title, stage, assigned agent/model, duration, errors, and output results.
   - Individual card action triggers: "Run Now" and "Cancel".
3. **Interactive Routing Simulator (`#tab-routing`):**
   - Form for testing arbitrary user instructions against the 8-factor Smart Router (`POST /api/route`).
   - Displays selected agent, model tier, score, fallback agent, and full candidate breakdown.
4. **Interactive Memory Search & Add (`#tab-memory`):**
   - BM25 full-text keyword search across scoped SQLite memory (`POST /api/memory/search`).
   - Quick-add interface for storing new architectural constraints and decisions (`POST /api/memory/add`).
5. **Worktree Diff Inspection & Approval (`#tab-worktrees`):**
   - Visual inspection of sandbox Git diffs (`GET /api/worktrees/diff?task_id=...`).
   - Non-destructive merge approval (`POST /api/worktrees/apply`) or rejection (`POST /api/worktrees/reject`) with required `confirm: true` guards.

---

## Agent Cards & Runtime Telemetry

Every agent, including Antigravity Account 2, is rendered from unified telemetry:

- Health dot and lifecycle badge: `ONLINE` / `IDLE` / `WORKING` / `FAILED` / `OFFLINE`
- Account ID, provider, execution mode (100% headless via `--app_data_dir=antigravity-ide`)
- Health reason and binary path
- Current task title, stage, and status
- Requested model, and provider-reported model when known
- Duration, success rate, and average latency
- Token telemetry distinguishing **Known Tokens** from **Estimated Tokens**

---

## Complete API Contract

| Endpoint | Method | Auth Required | Description |
|---|:---:|:---:|---|
| `GET /api/token` | `GET` | No (Loopback) | Local session Bearer token for client UI bootstrap |
| `GET /api/status` | `GET` | No | System state, counters, agent runtime blocks, token metrics |
| `GET /api/overview` | `GET` | No | Host specifications, active tasks, task counts, git status |
| `GET /api/agents` | `GET` | No | Registered providers, accounts, and capabilities |
| `GET /api/health` | `GET` | No | Per-agent health diagnostics |
| `GET /api/tasks` | `GET` | No | All task records across queues |
| `GET /api/task?task_id=...` | `GET` | No | Detailed inspection payload for a single task |
| `GET /api/router/history` | `GET` | No | Historical routing decisions and candidate scores |
| `GET /api/memory` | `GET` | No | Stored memory entries and total count |
| `GET /api/handoff` | `GET` | No | Latest Picoschema structured handoff markdown & JSON |
| `GET /api/events` | `GET` | No | Structured event bus stream records |
| `GET /api/git` | `GET` | No | Current branch, commit hash, and dirty file status |
| `GET /api/worktrees` | `GET` | No | Active git worktree sandbox records |
| `GET /api/worktrees/diff` | `GET` | No | Unified git diff for a sandbox task |
| `POST /api/route` | `POST` | No | Simulates 8-factor routing decision without execution |
| `POST /api/memory/search` | `POST` | No | BM25 relevance search query across SQLite memory |
| `POST /api/memory/add` | `POST` | **Bearer Token** | Stores a new memory entry |
| `POST /api/dispatch` | `POST` | **Bearer Token** | Plans, queues, and begins swarm task execution |
| `POST /api/execute` | `POST` | **Bearer Token** | Triggers background swarm execution worker |
| `POST /api/continue` | `POST` | **Bearer Token** | Universal Continue resumed off-thread |
| `POST /api/tasks/cancel` | `POST` | **Bearer Token** | Cancels a queued or executing task |
| `POST /api/worktrees/apply`| `POST` | **Bearer Token** | Merges sandbox branch into `main` (`confirm: true` required) |
| `POST /api/worktrees/reject`| `POST` | **Bearer Token** | Discards sandbox branch and cleans up (`confirm: true` required) |
| `POST /api/worktrees/cleanup`| `POST` | **Bearer Token** | Prunes stale worktrees older than specified threshold |

---

## Live Flow & Reactivity

The pipeline:
$$\text{USER} \longrightarrow \text{MISSION CONTROL UI} \longrightarrow \text{SMART ROUTER} \longrightarrow \text{AGENT EXECUTION} \longrightarrow \text{MEMORY} \longrightarrow \text{HANDOFF} \longrightarrow \text{TELEMETRY}$$
updates dynamically via 2.5-second polling intervals (`refreshData()`). Non-blocking toast notifications alert the operator of successful dispatches, memory writes, or API errors.
