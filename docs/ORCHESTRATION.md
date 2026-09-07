# Orchestration & Task Lifecycle

## Commands

```bash
python3 scripts/brain.py agents                       # fleet + health
python3 scripts/brain.py health [--agent ID] [--deep]  # exit 1 if unhealthy
python3 scripts/brain.py route "<instruction>"         # routing decision, no execution
python3 scripts/brain.py plan  "<instruction>"         # route + persist + queue
python3 scripts/brain.py execute                       # run the next ready batch
python3 scripts/brain.py continue [--dry-run]          # resume where work stopped
python3 scripts/brain.py status  [--limit N]
python3 scripts/brain.py sessions [--limit N]          # task -> session -> conversation
```

`plan` flags: `--agent`, `--model`, `--file` (repeatable, locked during
execution), `--allow-tool-permissions` (opt-in tool escalation for that task
only).

## Lifecycle

```
CREATED/READY -> (health pre-flight) -> RUNNING -> COMPLETED
                                              \-> FAILED
                                              \-> BLOCKED   (unhealthy agent, or lock contention)
                     -> CANCELLED  (a decision; excluded from continue)
```

Directories: `tasks/queue/` (READY, BACKLOG), `tasks/active/` (RUNNING,
PLANNING, REVIEW), `tasks/completed/`, `tasks/failed/` (FAILED, BLOCKED,
CANCELLED). Because several statuses share a directory, `list_tasks(status=...)`
filters on the record's own status, not on its location.

## Persisted per task

`task_id`, `title`, `description`, `status`, `priority`, `complexity`,
`assigned_agent`, `assigned_account`, `assigned_model`, `requested_model`,
`actual_model`, `session_id`, `conversation_id`, `created_at`, `started_at`,
`completed_at`, `duration_seconds`, `files`, `memory_refs`, `handoffs`,
`errors`, `execution_options`, and the full normalized `result`.

`Task.from_dict` ignores unknown keys, so older task files still load.

## Swarm execution

Per task, in order:

1. Resolve the adapter; fall back to any active adapter if the assignment is gone.
2. Health pre-flight. Unhealthy -> `BLOCKED`, emit `AGENT_HEALTH` + `TASK_FAILED`.
3. Mark `RUNNING`; record the session mapping **before** execution so an
   interrupted run is still resumable.
4. Emit `TASK_STARTED` and `MODEL_SELECTED` (plus the Account 2 twins).
5. Acquire file locks; failure -> `BLOCKED`, locks released.
6. Build the prompt with relevance-filtered memory (≤5 entries, ≤2048 bytes).
7. Execute with least-privilege options.
8. Normalize, verify the model honestly, update the session mapping with the
   conversation id.
9. Success: store memory, write the handoff, mark `COMPLETED`, emit
   `TASK_COMPLETED` + `TASK_HANDOFF` (+ Account 2 twins).
   Failure: mark `FAILED`, emit `TASK_FAILED` (+ twin).
10. Release locks in a `finally` block, emitting `FILE_UNLOCKED`.

## Concurrency

`MAX_CONCURRENT_AGENTS=2`, from `config/providers.json` (`concurrency`) and
overridable by the environment variable of the same name. Sized for a 2-core /
16 GB host. `run_queue()` takes at most that many READY tasks per invocation and
runs them in a thread pool.

## Restart recovery

`TaskManager.recover_orphaned_tasks()` returns interrupted `RUNNING` tasks to
`READY` and appends a note explaining why. Task, session, handoff and event
state are all plain files, so nothing is lost when a shell closes.
