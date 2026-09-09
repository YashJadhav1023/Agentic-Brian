# Phase 18: Autonomous Task Planning & Execution Graphs

## Overview
Phase 18 introduces structured multi-step planning, dependency Directed Acyclic Graphs (DAGs), human-in-the-loop approval gates, outcome verification invariants, and compensatory rollbacks.

---

## 1. Plan and Execution Graph Models
Located in [`brain/planner/plan_model.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/planner/plan_model.py):
- **`PlanStep`**: Represents an individual atomic action with dependencies, assigned agent, model, provider, risk level (`READ_ONLY`, `LOW_RISK_WRITE`, `HIGH_RISK_WRITE`, `DESTRUCTIVE`), verification rules, and rollback action.
- **`Plan`**: Represents an inspectable, dependency-linked sequence of steps.
- **`ExecutionGraph`**:
  - Implements cycle detection via Kahn's algorithm / Depth-First Search.
  - Determines topological execution order.
  - Identifies independent steps capable of concurrent execution.

---

## 2. Task Decomposition & Approval Gates
Located in [`brain/planner/task_decomposer.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/planner/task_decomposer.py):
- Decomposes high-level instructions into deterministic phases:
  1. **Inspection**: Read-only discovery and environment sanity check.
  2. **Execution**: Core modification or build action.
  3. **Verification**: Invariant checks ensuring expected outputs and zero error signals.
- **Approval Gates**: Any task containing destructive keywords (e.g. `delete`, `drop`, `purge`, `destroy`, `rm -rf`) is automatically flagged with `requires_approval=True` and initial status `BLOCKED_ON_APPROVAL`. Execution cannot proceed until explicit human approval is recorded via `approve_plan()`.

---

## 3. Verification Engine & Rollback Manager
Located in [`brain/planner/verification_engine.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/planner/verification_engine.py):
- **`VerificationEngine`**:
  - Validates exit codes (`returncode == 0`).
  - Asserts expected output files exist on disk.
  - Evaluates semantic rules (e.g. `no_errors` ensuring stdout/stderr contain no fatal or exception signals).
- **`RollbackManager`**:
  - Maintains a LIFO stack of registered compensatory rollback actions for each completed mutation.
  - If any subsequent step or verification check fails, rollback actions are executed in reverse order to restore previous state.

---

## 4. CLI Verification
```bash
# Create and inspect an autonomous plan
python3 scripts/brain.py plan "Delete old test logs and compile report"

# List stored plans
python3 scripts/brain.py plan list

# Inspect plan DAG and approval status
python3 scripts/brain.py plan inspect plan-xxxxxxxx

# Approve a blocked plan
python3 scripts/brain.py plan approve plan-xxxxxxxx

# Execute the plan
python3 scripts/brain.py plan execute plan-xxxxxxxx
```
