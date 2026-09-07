# Mission Control UI

```bash
python3 ui/dashboard/dashboard.py     # http://127.0.0.1:3333  (BRAIN_PORT to change)
```

**Security posture:** the server binds `127.0.0.1` only, so it is not reachable
from the network. Its `POST` endpoints (`/api/route`, `/api/dispatch`,
`/api/continue`) are **unauthenticated** and dispatch real agent work, so anyone
with local access to the machine can trigger a task. Do not bind it to `0.0.0.0`
or expose it through a tunnel without adding authentication first.

## Agent cards

Every agent, including Antigravity Account 2, is rendered from the same data
path — no agent is special-cased. Each card shows:

- health dot and lifecycle badge: `ONLINE` / `IDLE` / `WORKING` / `FAILED` / `OFFLINE`
- account id, provider, execution mode
- profile path (`~/.gemini/antigravity-ide` for Account 2)
- health reason
- current task title and status
- requested model, and the reported model when the provider named one
- session id and conversation id
- duration and last activity
- task counts: total / completed / failed
- most recent error, if any
- model catalogue size and the first few ids

Status is derived from real state: `OFFLINE` when unhealthy, `WORKING` with a
running task, `FAILED` when the newest terminal task failed, `IDLE` with history,
`ONLINE` when idle with none.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/status` | counters + full agent runtime block |
| `GET /api/agents` | providers -> accounts, enriched with live task/session/event state |
| `GET /api/health` | per-agent health, profile, skip-permissions flag, last session |
| `GET /api/sessions` | task -> session -> conversation mapping |
| `GET /api/tasks` | every task record |
| `GET /api/events` | last 100 events |
| `GET /api/memory` | memory entries + count |
| `GET /api/handoff` | current handoff markdown |
| `GET /api/git` | branch, commit, status |
| `POST /api/route` | routing decision, no execution |
| `POST /api/dispatch` | plan + queue a task |
| `POST /api/continue` | Universal Continue, executed off-thread |

## Task history per agent

`recent_tasks` on each agent carries the last 10 tasks with status, requested and
reported model, conversation id, duration, files and handoff paths — enough for
an Account 2 → Tasks view without another endpoint.

## Live flow

`USER -> BRAIN -> ROUTER -> ANTIGRAVITY ACCOUNT 2 -> agy -> EXECUTION -> RESULT
-> MEMORY -> HANDOFF` is observable by polling `/api/status` (the UI refreshes
every 3 s) or by tailing `/api/events`.

The existing tab layout, styling and static assets were preserved; only the data
path and the card contents changed.
