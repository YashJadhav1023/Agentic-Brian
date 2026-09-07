# Multi-Agent Orchestration & Swarm

## Execution Lifecycle

1. **Plan & Dispatch:**
   - Instruction received via CLI (`brain plan "..."`) or UI.
   - Heuristic classification assigns optimal agent and model.
   - Persistent task created in `tasks/queue/task-<id>.json`.
2. **File Locking:**
   - Task target files are locked via `FileLocker` to prevent concurrent write collisions.
3. **Execution:**
   - Worker pool invokes the designated `AgentAdapter`.
   - Output captured, duration timed, actual model verified.
4. **Handoff Generation:**
   - Standardized Picoschema Markdown record generated in `handoffs/`.
5. **Event Emission:**
   - Telemetry emitted to `events/bus.py` and persisted in `runtime/logs/events.jsonl`.
