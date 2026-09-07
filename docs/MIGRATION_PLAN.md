# Agentic Brain Migration Plan

**Canonical repository:** `~/YashDevops/Agentic_shared_memory/`
**Remote:** `https://github.com/YashJadhav1023/Agentic-Brian.git` (branch `main`)
**Status:** Phase 3 complete (Safe Sandboxed Execution + Mission Control Security). Legacy implementation untouched.

---

## 1. Two systems currently coexist

| Dimension | Legacy | Canonical (this repo) |
|---|---|---|
| Location | `~/YashDevops/Agentic_os/scripts/brain/` (15 Python modules + shell CLI) | `~/YashDevops/Agentic_shared_memory/` |
| Memory | `~/agentic-brain` Markdown knowledge graph via `brain` MCP server | `memory/store/shared_memory.db` (SQLite) |
| Entry point | `brain` shell CLI, `brain swarm …` | `python3 scripts/brain.py …` |
| Account 2 | Delivery only — recorded for human editor execution | Headless first-class production agent |
| Sandbox | `git worktree` on `brain/swarm/<task-id>` | Safe isolated `git worktree` on `agentic/task/<task-id>` with human approval gate |
| Security | Loopback only, unauthenticated POST | Loopback only + Bearer token auth + CORS restriction + rate limiting + security headers |
| Status | Legacy in active use in Agentic_os | Phase 3 verified (116 tests passing) |

The legacy system in `~/YashDevops/Agentic_os/` remains untouched and functional.

---

## 2. Migration Inventory

| Component | Canonical Implementation | Phase 3 Status |
|---|---|---|
| Agent Adapters | `agents/` + `providers/registry/` | COMPLETED |
| Model Policies | `models/policies/model_policy.py` | COMPLETED |
| Swarm Worker Pool | `brain/orchestrator/swarm.py` | COMPLETED (Sandbox integrated) |
| Git Worktree Sandboxing | `brain/worktree/worktree_manager.py` | COMPLETED (Phase 3 Objective A) |
| File Locking & Concurrency | `locks/file_locker.py` | COMPLETED |
| Mission Control & Security | `ui/dashboard/dashboard.py` | COMPLETED (Phase 3 Objective B) |
| CLI Interface | `scripts/brain.py` | COMPLETED (Added `worktree` subcommands) |
| Telemetry & Audit | `events/bus.py` | COMPLETED (Phase 3 security & worktree events) |
| Test Suite | `tests/unit/`, `tests/integration/` | 116 passing tests (0 failures, 0 errors) |

---

## 3. Deliberate Architectural Decisions

1. **Headless Account 2 Execution**: Proven with `--app_data_dir=antigravity-ide`. No human driving required.
2. **Safe Isolated Worktrees**: Agents modifying files execute in a sandboxed git worktree. Canonical repository working tree is protected and never dirty-merged.
3. **Defense-in-Depth Mission Control**: Mutating endpoints require Bearer auth and confirmation flags; CORS is locked to local origins.
4. **Least Privilege by Default**: Tool permissions remain off unless explicitly requested per task.

---

## 4. Remaining Steps to Full Adoption

1. Validate live workflows across all 4 agent accounts.
2. Decide whether SQLite store should synchronize with `~/agentic-brain` Markdown graph or remain separate.
3. Update machine-wide steering docs and deprecate `Agentic_os/scripts/brain/` when ready.
