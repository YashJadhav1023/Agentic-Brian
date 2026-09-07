# Shared Memory

## One store for every agent

`memory/store/shared_memory.db` (SQLite) is shared by all four agents. Account 2
has no private memory, and neither does anyone else. Whatever one agent records,
the others can retrieve.

Scopes: `GLOBAL`, `PROJECT`, `TASK`, `AGENT`, `SESSION`.

Each entry: `memory_id`, `content`, `scope`, `source_agent`, `task_id`,
`session_id`, `created_at`, `confidence`, `importance` (1–5), `tags`,
`provenance`. Indexed on scope, task, source agent and creation time.

Connections are opened through a context manager that always commits and always
closes, so nothing leaks handles.

## What gets written

On successful execution the swarm records exactly one compact entry:

```
Task '<title>' completed by <agent_id> (account <account_id>): <first 500 chars of response>
tags: [task_result, <agent_id>, <account_id>]   scope: PROJECT   importance: 3
```

The raw payload is never stored. `MEMORY_CREATED` is emitted with the
`memory_id`, and the id is appended to the task's `memory_refs`.

## Retrieval is bounded

`MemoryRetriever.retrieve_context()` scores candidates by importance, exact task
match (+5), keyword overlap (+2 per token) and tag match (+3), then caps the
result at **5 entries / 2048 bytes** by default.

The whole database is never injected into a prompt. A test seeds 30 irrelevant
entries plus 1 relevant one and asserts the relevant content appears while the
prompt stays bounded.

## Never store

- API keys, tokens, passwords, connection strings, kubeconfig contents.
- Raw secret values from environment variables, vaults or logs.
- Full log or payload dumps — record the finding, not the payload.

Refer to secrets by name only.
