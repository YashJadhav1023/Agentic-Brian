# Observability & Telemetry

## Event Bus

All significant events are emitted through `EventBus` and persisted to `runtime/logs/events.jsonl`.

### Standard Event Types
- `AGENT_STARTED` / `AGENT_STOPPED` / `AGENT_HEARTBEAT`
- `TASK_CREATED` / `TASK_STARTED` / `TASK_COMPLETED` / `TASK_FAILED` / `TASK_ASSIGNED` / `TASK_HANDOFF`
- `MEMORY_CREATED` / `MEMORY_ACCESSED`
- `MODEL_SELECTED` / `MODEL_SWITCHED`
- `FILE_LOCKED` / `FILE_UNLOCKED`
- `GIT_EVENT` / `ERROR`
