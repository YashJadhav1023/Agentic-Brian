# Operations Runbook

Project root: `~/YashDevops/Agentic_shared_memory/`. Run everything from there.

## Daily checks

```bash
python3 scripts/brain.py agents                  # fleet, profiles, capabilities
python3 scripts/brain.py health                  # shallow, no network
python3 scripts/brain.py health --deep           # session probe per account
python3 scripts/brain.py status                  # recent tasks
python3 scripts/brain.py sessions                # conversation mapping
python3 -m unittest discover -s tests            # 87 tests
```

`health` exits `1` if any agent is unhealthy, so it works in a cron or a
pre-flight check.

Shallow health = binary resolvable, profile exists, readable and writable.
Deep health = `agy --app_data_dir=<profile> models`, which proves the session is
usable **without spending a model request**. Deep results are cached for 300 s.

## Running work

```bash
# let the brain choose the agent
python3 scripts/brain.py plan "Review and refactor this service"
python3 scripts/brain.py execute

# pin the agent and model
python3 scripts/brain.py plan "Refactor telemetry" \
  --agent antigravity-account-2 --model gemini-3.1-pro-high
python3 scripts/brain.py execute

# a task that needs to read files (opt-in tool escalation)
python3 scripts/brain.py plan "Audit the retry logic" --allow-tool-permissions
python3 scripts/brain.py execute

# resume
python3 scripts/brain.py continue --dry-run
python3 scripts/brain.py continue
```

`execute` runs at most `MAX_CONCURRENT_AGENTS` (2) tasks per invocation. Run it
again to drain the rest of the queue.

## Resource envelope

Host: CachyOS, Intel i7, 2 cores, 16 GB. Bounded concurrency is 2, set in
`config/providers.json` under `concurrency` and overridable:

```bash
MAX_CONCURRENT_AGENTS=1 python3 scripts/brain.py execute
```

Do not raise it above 2 on this host. There are no daemons, no containers and no
database server; state is files on disk.

## Recovery

```bash
# return interrupted RUNNING tasks to the queue after a crash or reboot
python3 -c "from tasks.manager import TaskManager; print(TaskManager().recover_orphaned_tasks())"

# clear a stale file lock (locks carry a TTL and self-reclaim)
ls locks/
```

## State locations

| What | Where | Tracked in git |
|---|---|---|
| Tasks | `tasks/{queue,active,completed,failed}/*.json` | no (ephemeral) |
| Sessions | `sessions/session_registry.json` | no |
| Memory | `memory/store/shared_memory.db` | no |
| Events | `runtime/logs/events.jsonl` | no |
| Handoffs | `handoffs/current.{md,json}` + `archive/` | current only |
| Config | `config/providers.json` | yes |

## Mission Control

```bash
python3 ui/dashboard/dashboard.py        # 127.0.0.1:3333
BRAIN_PORT=4444 python3 ui/dashboard/dashboard.py
```

Loopback only. Its POST endpoints are unauthenticated — see `docs/UI.md` before
changing the bind address.
