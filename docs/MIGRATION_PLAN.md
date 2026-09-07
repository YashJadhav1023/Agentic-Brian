# Agentic Brain Migration Plan

**Canonical repository:** `~/YashDevops/Agentic_shared_memory/`
**Remote:** `https://github.com/YashJadhav1023/Agentic-Brian.git` (branch `main`)
**Status:** Phase 2 complete. Legacy implementation still in place and untouched.

---

## Two systems currently coexist

| | Legacy | Canonical (this repo) |
|---|---|---|
| Location | `~/YashDevops/Agentic_os/scripts/brain/` (15 Python modules + shell CLI) | `~/YashDevops/Agentic_shared_memory/` |
| Memory | `~/agentic-brain` Markdown knowledge graph via the `brain` MCP server (basic-memory) | `memory/store/shared_memory.db` (SQLite) |
| Entry point | `brain` shell CLI, `brain swarm …` | `python3 scripts/brain.py …` |
| Account 2 | **delivery only** — recorded for a human to drive in the IDE | **headless first-class agent** |
| Sandbox | git worktree on `brain/swarm/<task-id>` | none; tasks run in the workspace |
| Status | in active use, referenced by machine-wide steering docs | Phase 2 verified |

**Nothing has been deleted.** The legacy implementation is the one wired into
`~/.kiro/steering/shared-brain.md` and `AGENTS.md`, so removing it would break
every agent on the machine. It stays until the canonical system is adopted
deliberately.

The two do not interfere: different directories, different stores, different
entry points, different task files.

---

## Migration inventory

| Legacy module | Canonical equivalent | Status |
|---|---|---|
| `agent_adapters.py` | `agents/` + `providers/registry/` | MIGRATED (rebuilt, config-driven) |
| `model_policy.py` | `models/policies/model_policy.py` | MIGRATED (catalogues re-verified) |
| `swarm.py` | `brain/orchestrator/swarm.py` + `tasks/` | MIGRATED (no git-worktree sandbox) |
| `orchestrator.py` | `brain/orchestrator/orchestrator.py` | MIGRATED |
| `execution.py` | `locks/file_locker.py` | MIGRATED (locking only) |
| `lifecycle.py` | `tasks/manager.py` | MIGRATED |
| `cognitive_engine.py` | `memory/retrieval/retriever.py` | MIGRATED (simpler; no 2-hop graph expansion) |
| `dashboard.py`, `dashboard_execution.py`, `static/` | `ui/dashboard/` | MIGRATED |
| `brain` (shell CLI) | `scripts/brain.py` | MIGRATED |
| `orchestrator_mcp.py` | — | NOT MIGRATED (no MCP server here yet) |
| `search_sidecar.py` | — | NOT MIGRATED (no semantic sidecar) |
| `sentinel.py` | — | NOT MIGRATED (no continuous monitor) |
| `install-protocol.py`, `register-brain-mcp.py`, `attach-antigravity-key.sh` | — | NOT MIGRATED (machine-level setup, stays legacy) |
| `test_brain_*.py` | `tests/unit/`, `tests/integration/` | REPLACED (87 tests written against this codebase) |

## Deliberate differences from the legacy system

1. **Account 2 executes.** The legacy system treats `antigravity-ide` as a
   delivery target requiring a human. Verified headless execution makes that
   unnecessary.
2. **No git worktree sandbox.** Legacy headless tasks ran in a throwaway
   worktree because agents ran with approval gates disabled. Here the default is
   least privilege, so tasks cannot mutate files unless escalation is opted into.
   If escalated execution becomes routine, port the sandbox before doing so.
3. **No second LLM, no MCP dependency.** This system is a subprocess orchestrator
   over local CLIs.

## Remaining steps to full adoption

1. Run both systems in parallel and compare outcomes on real work.
2. Port the git-worktree sandbox if escalated execution becomes the norm.
3. Decide whether the SQLite store should sync with the `~/agentic-brain`
   Markdown graph, or replace it.
4. Only then update the machine-wide steering docs and retire
   `Agentic_os/scripts/brain/`.
