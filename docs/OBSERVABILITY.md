# Observability & Event Telemetry

## One event log

`runtime/logs/events.jsonl` — append-only JSONL, thread-safe, no second logging
system. Each event: `event_type`, `timestamp` (UTC ISO-8601), `agent_id`,
`task_id`, `session_id`, `metadata`.

Every event's metadata carries `account_id` and `provider`, so a run can always
be attributed to the account that served it.

## Event types

**Agent:** `AGENT_STARTED`, `AGENT_STOPPED`, `AGENT_HEARTBEAT`, `AGENT_HEALTH`

**Task:** `TASK_CREATED`, `TASK_ASSIGNED`, `TASK_STARTED`, `TASK_COMPLETED`,
`TASK_FAILED`, `TASK_HANDOFF`

**Context & routing:** `MEMORY_CREATED`, `MEMORY_ACCESSED`, `MODEL_SELECTED`,
`MODEL_SWITCHED`, `TOOL_CALLED`, `TOOL_FAILED`, `FILE_LOCKED`, `FILE_UNLOCKED`,
`GIT_EVENT`, `ERROR`

**Account 2 (dedicated):** `ANTIGRAVITY_ACCOUNT2_STARTED`,
`ANTIGRAVITY_ACCOUNT2_COMPLETED`, `ANTIGRAVITY_ACCOUNT2_FAILED`,
`ANTIGRAVITY_ACCOUNT2_HEALTH`, `ANTIGRAVITY_ACCOUNT2_MODEL_SELECTED`,
`ANTIGRAVITY_ACCOUNT2_HANDOFF`

Account-specific events are emitted **alongside** the generic event, never
instead of it, so provider-neutral consumers keep working. The mapping lives in
`ACCOUNT_EVENTS` in `brain/orchestrator/swarm.py`, which keeps the swarm free of
per-agent branching.

## Reading the log

```bash
python3 -c "from events.bus import EventBus; [print(e.event_type.value, e.agent_id, e.task_id) for e in EventBus().get_recent_events(20)]"
grep -o '"event_type": "ANTIGRAVITY_ACCOUNT2[A-Z_]*"' runtime/logs/events.jsonl | sort | uniq -c
```

Or `GET /api/events` from Mission Control.

## What is logged

Recorded: timestamps, task ids, agent and account ids, event types, requested and
reported models, durations, exit codes, token counts, conversation ids, memory
ids, handoff paths, error text (truncated).

Never recorded: prompts in argv (the recorded command has the prompt replaced
with `<prompt redacted>`), tokens, keys, passwords, or raw payload dumps. A test
asserts the API surface contains no credential-shaped keys.

## Session telemetry

`sessions/session_registry.json` maps `task_id -> session_id ->
conversation_id`, with `agent_id`, `model`, timestamps and metadata
(`account_id`, `provider`, `exit_code`). It is written before execution and
updated after, so an interrupted run still leaves a resumable trace.

```bash
python3 scripts/brain.py sessions --limit 20
```
