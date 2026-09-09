# Universal AI Mission Control — Production Operational Runbook

This runbook provides complete operational guidance for managing, operating, troubleshooting, and recovering the **Universal AI Mission Control** plane.

---

## 1. System Overview & Key Paths

| Component | Path / Location | Purpose |
|---|---|---|
| Master Control Plane | `brain/mission_brain.py` | Unified facade coordinating all operations |
| Operator CLI | `scripts/brain.py` | Command-line interface for operators |
| Swarm Workspaces | `runtime/sandboxes/` | Guarded Git worktrees for headless runs |
| Analytics & Telemetry | `runtime/analytics/performance.jsonl` | Empirical execution performance data |
| Governance Audit Log | `runtime/audit/audit.jsonl` | Tamper-evident append-only audit trail |
| Plan Repository | `runtime/plans/` | Decomposed task DAGs and approval states |
| MCP Server Registry | `runtime/mcp/registry.json` | Auto-discovered tools and servers |
| Credential Store | OS Keyring (`service=mission-control`) | Secure credential backend |

---

## 2. Daily Health & Monitoring Commands

### 2.1 Deep Cluster Health Check
Performs end-to-end diagnostic verification across all providers, MCP servers, adapters, and databases:
```bash
python3 scripts/brain.py health --deep
```
Expected output status: `HEALTHY`.
If any MCP server reports `UNKNOWN`, verify that its binary (`node`, `npx`, `uv`, `python3`) is present on the system `PATH`.

### 2.2 Performance Metrics & Telemetry
Inspect aggregate latency, token consumption, and agent success rates:
```bash
python3 scripts/brain.py metrics
```

### 2.3 Governance & Audit Inspection
Review recent system actions, agent decisions, or security interventions:
```bash
# View last 20 audit events
python3 scripts/brain.py audit --limit 20

# Filter by execution events in JSON format
python3 scripts/brain.py audit --category EXECUTION --json

# Filter by safety gate and approval interventions
python3 scripts/brain.py audit --category APPROVAL
python3 scripts/brain.py audit --category SECURITY
```

---

## 3. Task Routing & Plan Execution

### 3.1 Explainable Route Simulation
Inspect how the Smart Router evaluates candidate agents, models, and tools for a task without executing it:
```bash
python3 scripts/brain.py route explain "Analyze Azure SQL database latency and optimize queries"
```
The output details:
- Selected agent and model
- Matched capabilities
- Empirical affinity score breakdown
- Recommended MCP servers and tools
- Full fallback failover chain

### 3.2 Plan Generation & DAG Inspection
Decompose a complex natural-language goal into a dependency-ordered plan:
```bash
# Preview plan generation
python3 -c "
from brain.mission_brain import MissionBrain
brain = MissionBrain()
plan = brain.create_plan('Refactor user authentication service across repositories')
print(f'Plan ID: {plan.plan_id}, Steps: {len(plan.steps)}, Requires Approval: {plan.requires_approval}')
"
```

### 3.3 Handling Approval Gates
Destructive operations (e.g. commands containing `delete`, `drop`, `purge`, `rm -rf`) enter `BLOCKED_ON_APPROVAL` status:
```bash
python3 -c "
from brain.mission_brain import MissionBrain
brain = MissionBrain()
plan = brain.create_plan('Delete obsolete test databases and purge cache')
print(f'Initial Status: {plan.status}')

# To approve as human operator:
brain.approve_plan(plan.plan_id, approver='operator@company.com')
print(f'Approved Status: {brain.get_plan(plan.plan_id).status}')
"
```

---

## 4. Account & Credential Management

### 4.1 Listing Accounts & Health
```bash
python3 -c "
from providers.registry.account_registry import get_account_registry
reg = get_account_registry()
for acc in reg.list_accounts():
    print(f'{acc.id}: {acc.agent_type} [{acc.status.value}] (ref: {acc.credential_reference})')
"
```

### 4.2 Safe Key Rotation
When rotating an API key:
1. Update the credential in the secure backend:
   ```bash
   python3 -c "
   from providers.registry.credential_manager import get_credential_manager
   cm = get_credential_manager()
   cm.store_secret('openai/prod-key', 'sk-new-secret-here')
   "
   ```
2. Verify the account references `secret://mission-control/openai/prod-key`.
3. Test account connectivity:
   ```bash
   python3 -c "
   from providers.registry.account_registry import get_account_registry
   reg = get_account_registry()
   print(reg.test_account_health('openai-account-1'))
   "
   ```

---

## 5. Antigravity IDE GUI Session Safety Protocol

The Antigravity IDE GUI runs as an active desktop process (`PID 5854`). Operators and automated scripts must strictly adhere to the following rules:

1. **NEVER execute `pkill -f antigravity` or `killall antigravity`**:
   Doing so will terminate the developer's live editor and destroy unsaved state.
2. **Always scope headless executions**:
   Headless CLI tasks must pass a dedicated `--app_data_dir` (e.g. `~/.gemini/antigravity-account-yash`) and never point to `~/.gemini/antigravity-cli`.
3. **Keyring Isolation**:
   The Antigravity GUI uses `service=gemini`. Mission Control strictly uses `service=mission-control`. Never store or delete credentials under `service=gemini`.

---

## 6. Disaster Recovery & Emergency Procedures

### 6.1 Stuck Headless Worktrees
If a background agent execution was aborted abruptly, leaving a dirty worktree:
```bash
# Inspect worktree registry
cat runtime/sandboxes/worktrees_registry.json

# Clean up stale worktrees
git worktree prune
```

### 6.2 Plan Execution Rollback
If a multi-step execution fails midway:
1. Execution Fabric automatically invokes registered rollback commands in LIFO order.
2. Check `runtime/audit/audit.jsonl` for category `ROLLBACK`:
   ```bash
   python3 scripts/brain.py audit --category ROLLBACK --limit 10
   ```
3. If manual cleanup is required, inspect the plan's rollback metadata and execute compensatory scripts.

### 6.3 State Reset (Non-Destructive)
To reset runtime caches without touching credentials or repositories:
```bash
rm -rf runtime/plans/*.json
rm -rf runtime/analytics/performance.jsonl
# Note: DO NOT delete runtime/audit/audit.jsonl in production environments.
```
