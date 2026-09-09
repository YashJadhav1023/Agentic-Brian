# Phase 19: Dynamic Self-Optimization & Universal Agent Execution Fabric

## Overview
Phase 19 provides empirical closed-loop telemetry, dynamic routing weight adjustments, transparent routing explainability, and the universal execution fabric coordinating agents, providers, and tools.

---

## 1. Performance Registry & Empirical Telemetry
Located in [`brain/analytics/performance_registry.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/analytics/performance_registry.py):
- Records empirical execution records (`ExecutionRecord`) to persistent telemetry log (`runtime/analytics/performance.jsonl`).
- Tracks:
  - Task domain and agent combination success rates.
  - Latency, token consumption, and cost estimates.
  - Tool and MCP server reliability ratings.
  - Provider health scores.
- **Dynamic Routing Boosts**:
  - `get_agent_affinity_boost(agent_id, domain)`: Calculates bonus/penalty between `-2.0` and `+2.0` based on historical success rates in that domain.
  - `get_provider_health_score(provider_id)`: Scales provider health multiplier `[0.0 to 1.0]`.

---

## 2. Smart Router Integration & Explainability
Located in [`brain/router/smart_router.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/router/smart_router.py):
- Integrates `PerformanceRegistry` empirical boosts directly into candidate scoring.
- `explain_routing(task)`: Returns an explainability report:
  - Selected agent, provider, account, and model.
  - Full candidate score breakdown across all multi-factor dimensions.
  - Explicit rationale including keyword affinities and empirical bonuses.
  - Recommended MCP servers, tools, and documentation.
  - Complete fallback chain ranking alternatives by fitness.

---

## 3. Universal Execution Fabric
Located in [`brain/orchestrator/execution_fabric.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/orchestrator/execution_fabric.py):
- Serves as the central execution runtime for any task or plan step:
  1. **Safety Enforcer**: Rejects any command matching protected guardrail patterns (e.g. killing GUI PID 5854, mutating root `~/.gemini/`).
  2. **Approval Gate**: Intercepts unapproved destructive actions.
  3. **Context Injection**: Injects tailored `TaskContext` into agent execution.
  4. **Outcome Verification**: Runs `VerificationEngine` against invariants.
  5. **Compensatory Rollback**: Executes `RollbackManager` on failure.
  6. **Telemetry Accounting**: Automatically logs result to `PerformanceRegistry` and tracks tokens/costs.

---

## 4. CLI Verification
```bash
# Deep explainability report for task routing
python3 scripts/brain.py route explain "Refactor auth tokens across services"

# View empirical performance telemetry summary
python3 scripts/brain.py metrics

# JSON telemetry output
python3 scripts/brain.py metrics --json
```
