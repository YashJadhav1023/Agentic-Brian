# Agentic Brain Migration Plan

**Date:** 2026-09-07  
**Target Canonical Repository:** `~/Yashdevops/Agentic_shared_memory/`  
**Remote:** `https://github.com/YashJadhav1023/Agentic-Brian.git`  
**Status:** IN PROGRESS

---

## Migration Inventory & Tracking Matrix

| Current Location | Target Location | File | Purpose | Dependencies | Risks | Backup | Rollback | Status |
|---|---|---|---|---|---|---|---|---|
| `scripts/brain/agent_adapters.py` | `agents/` & `providers/` | `agent_adapters.py` | Agent capability & adapter registry | Python stdlib, model_policy | Breaking adapter imports | `brain_backup_*` | Restore from backup | IN PROGRESS |
| `scripts/brain/model_policy.py` | `models/policies/` | `model_policy.py` | Deterministic model routing & tiers | Python stdlib | Inverted tiers, model rejection | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/swarm.py` | `brain/orchestrator/` & `tasks/` | `swarm.py` | Swarm task distribution & execution | Git worktrees, CLI agents | Account isolation, model pass-thru | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/orchestrator.py` | `brain/orchestrator/` | `orchestrator.py` | Task classification & planning | model_policy | Discarded model selection | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/orchestrator_mcp.py` | `mcp/servers/` | `orchestrator_mcp.py` | FastMCP stdio interface | fastmcp, orchestrator | fastmcp missing in system python | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/execution.py` | `locks/` & `brain/state/` | `execution.py` | File locking and sandbox setup | Python fcntl / locks | Stale locks blocking runs | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/lifecycle.py` | `brain/state/` | `lifecycle.py` | Agent/task lifecycle states | Python stdlib | State desynchronization | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/cognitive_engine.py` | `memory/retrieval/` | `cognitive_engine.py` | Context expansion & relevance | basic-memory MCP | Large memory dump overhead | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/search_sidecar.py` | `mcp/servers/` | `search_sidecar.py` | Persistent semantic search | SQLite, loopback HTTP | Port conflict on 3334 | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/sentinel.py` | `brain/state/` | `sentinel.py` | Continuous self-healing | basic-memory MCP | Unchecked loop resources | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/dashboard.py` | `ui/dashboard/` | `dashboard.py` | Mission Control Web UI (:3333) | HTTP server, static assets | Agent card hardcoding | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/dashboard_execution.py` | `ui/dashboard/` | `dashboard_execution.py` | UI execution executor | subprocess, swarm | Background thread exhaustion | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/static/` | `ui/dashboard/static/` | `static/` | Vendored web assets (vis.js, etc.) | Browser | None | `brain_backup_*` | Restore from backup | PENDING |
| `scripts/brain/brain` | `scripts/` | `brain` | Shell CLI entrypoint | bash, jq | PATH resolution | `brain_backup_*` | Restore from backup | PENDING |
| `tests/test_brain_*.py` | `tests/unit/` & `tests/integration/` | `test_brain_*.py` | 168+ brain tests | Python unittest | Missing module imports | `brain_backup_*` | Restore from backup | PENDING |

---

## Migration Phasing

1. **Phase A (Active):** Scaffold directory hierarchy, initialize Git at canonical root, create `.gitignore` and `.env.example`, copy audit, establish migration and progress trackers.
2. **Phase B:** Build generic `AgentAdapter` and `ProviderRegistry`. Concrete adapters for `kiro`, `cline`, `antigravity-account-1`, and `antigravity-account-2`. Remove `gemini-api` from active pool.
3. **Phase C:** Fully wire Antigravity Account 2 as a first-class headless worker with `--app_data_dir=antigravity-ide`.
4. **Phase D:** Fix model routing bug in `orchestrator.py` & `swarm.py`. Correct model cost tiers in `model_policy.py`. Add actual model verification.
5. **Phase E:** Implement persistent task queue (`tasks/queue/`, `active/`, `completed/`, `failed/`).
6. **Phase F:** Memory scoping and relevant context retrieval.
7. **Phase G:** Structured handoffs and Universal Continue.
8. **Phase H:** Robust file locking.
9. **Phase I:** Structured event bus and heartbeats.
10. **Phase J:** Modernized Mission Control UI with dynamic provider cards and Account 1 / Account 2 views.
11. **Phase K:** Bounded concurrency configuration (`MAX_CONCURRENT_AGENTS=2`).
12. **Phase L:** Comprehensive test suite execution.
13. **Phase M:** Full documentation suite.
14. **Phase N:** Pre-push secret audit, commit, and push to GitHub.
