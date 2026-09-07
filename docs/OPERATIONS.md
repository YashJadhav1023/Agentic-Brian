# Operations Runbook

Project root: `~/YashDevops/Agentic_shared_memory/`. Run all commands from this directory.

---

## 1. Daily Health & Verification Checks

```bash
python3 scripts/brain.py agents                  # Fleet, profiles, execution modes, and capabilities
python3 scripts/brain.py health                  # Shallow check (binary, profiles, permissions)
python3 scripts/brain.py health --deep           # Deep health probe per account (zero-cost model listing)
python3 scripts/brain.py status                  # Recent tasks and execution states
python3 scripts/brain.py sessions                # Task -> session -> conversation mapping
python3 scripts/brain.py worktree status         # List active isolated git worktree sandboxes
python3 -m unittest discover -s tests            # Run full test suite (116 passing tests)
```

`health` exits with code `0` when all agents are healthy, or `1` if any agent is degraded or unhealthy.

---

## 2. Running Work

```bash
# 1. Let the Smart Router choose the optimal agent automatically
python3 scripts/brain.py plan "Review and refactor this service"
python3 scripts/brain.py execute

# 2. Pin an explicit agent and model
python3 scripts/brain.py plan "Refactor telemetry helpers" \
  --agent antigravity-account-2 --model gemini-3.8-flash-medium
python3 scripts/brain.py execute

# 3. Mutating task with explicit tool permission escalation
python3 scripts/brain.py plan "Create module tests" \
  --file tests/unit/test_module.py --allow-tool-permissions
python3 scripts/brain.py execute

# 4. Universal Continue (resumes from task + session + handoff + memory)
python3 scripts/brain.py continue --dry-run
python3 scripts/brain.py continue
```

`execute` processes at most `MAX_CONCURRENT_AGENTS` (2) tasks per invocation to respect hardware boundaries.

---

## 3. Git Worktree Sandbox Management

When an agent executes a mutating task, changes are quarantined inside `runtime/sandboxes/agentic-task-<task_id>`. Use the `worktree` subcommands to inspect and manage them:

```bash
# View all sandbox sandboxes and their status
python3 scripts/brain.py worktree status

# View unified diff for a specific task sandbox
python3 scripts/brain.py worktree diff <task_id>

# Approve and merge sandbox branch into canonical repository (requires --confirm)
python3 scripts/brain.py worktree approve <task_id> --confirm

# Reject and destroy sandbox worktree and delete its branch (requires --confirm)
python3 scripts/brain.py worktree reject <task_id> --confirm

# Recover an existing branch into the active registry
python3 scripts/brain.py worktree recover <task_id>

# Prune stale worktrees older than 24 hours
python3 scripts/brain.py worktree cleanup --confirm --max-age-hours 24
```

---

## 4. Mission Control Operations

### Starting the Server
```bash
python3 ui/dashboard/dashboard.py                 # Default: 127.0.0.1:3333
BRAIN_PORT=4444 python3 ui/dashboard/dashboard.py  # Custom port
```

### Security & Authentication
Mission Control is strictly bound to `127.0.0.1`.
All mutating endpoints (`/api/dispatch`, `/api/continue`, `/api/worktrees/*`) require Bearer token authentication:
- The token is retrieved from `MISSION_CONTROL_AUTH_TOKEN` env var or `runtime/mission_control.token`.
- To query or test authenticated endpoints via `curl`:
  ```bash
  TOKEN=$(cat runtime/mission_control.token)

  # Dispatch a task
  curl -X POST http://127.0.0.1:3333/api/dispatch \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"instruction": "Run code audit"}'

  # Approve a worktree merge
  curl -X POST http://127.0.0.1:3333/api/worktrees/approve \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"task_id": "task-xxxx", "confirm": true}'
  ```

---

## 5. State & Directory Layout

| Entity | Storage Path | Tracked in Git | Description |
|---|---|---|---|
| Tasks | `tasks/{queue,active,completed,failed}/*.json` | No | Task records and status lifecycle |
| Sessions | `sessions/session_registry.json` | No | Agent-to-conversation session mapping |
| Worktrees | `runtime/sandboxes/` | No | Isolated git worktrees and metadata |
| Tokens | `runtime/mission_control.token` | No | Cryptographic bearer auth token |
| Memory | `memory/store/shared_memory.db` | No | SQLite cross-agent memory store |
| Logs/Events | `runtime/logs/events.jsonl` | No | Structured append-only audit bus |
| Handoffs | `handoffs/current.{md,json}` + `archive/` | Current only | Live baton and historical checkpoints |
| Config | `config/providers.json` | Yes | Agent, model, and concurrency config |
