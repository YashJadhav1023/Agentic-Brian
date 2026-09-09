# Universal AI Mission Control Architecture (Phases 1–20)

## 1. System Overview
Universal AI Mission Control evolves the shared agentic brain into a multi-agent control plane that unifies tools, MCP servers, AI skills, context federation, DAG planning, dynamic routing, autonomous execution, and immutable governance across local and cloud environments.

```
+-----------------------------------------------------------------------------------+
|                                MISSION CONTROL PLANE                              |
|                          (MissionBrain Facade & CLI)                              |
+-----------------------------------------------------------------------------------+
       |                     |                     |                    |
       v                     v                     v                    v
+---------------+     +---------------+     +---------------+    +------------------+
|  Phase 16:    |     |  Phase 17:    |     |  Phase 18:    |    |  Phase 19 & 20:  |
|  Resource &   |     |  Context &    |     |  DAG Planning |    |  Execution Fabric|
|  Tool Catalog |     |  Knowledge    |     |  & Approval   |    |  & Audit Logger  |
+---------------+     +---------------+     +---------------+    +------------------+
| - 11 MCPs     |     | - Inverted    |     | - PlanStep    |    | - ExecutionFabric|
| - 26 Tools    |     |   BM25 Index  |     |   Dependency  |    | - Performance    |
| - 86 Skills   |     | - Provenance  |     |   DAG         |    |   Registry       |
| - 17 CLIs     |     |   Tracking    |     | - Approval    |    | - Empirical      |
| - Safety      |     | - Secret      |     |   Gates       |    |   Affinity Boost |
|   Classifier  |     |   Redaction   |     | - Rollback    |    | - Immutable      |
| - Resource-   |     | - Agent-Scoped|     |   Manager     |    |   Audit Log      |
|   Registry    |     |   Contexts    |     | - Verification|    | - Explainable    |
|   (232 Items) |     |   (ctx-xxxx)  |     |   Engine      |    |   Routing Engine |
+---------------+     +---------------+     +---------------+    +------------------+
       |                     |                     |                    |
       +---------------------+---------------------+--------------------+
                                     |
                                     v
+-----------------------------------------------------------------------------------+
|                         AGENT & PROVIDER EXECUTION FABRIC                         |
+-----------------------------------------------------------------------------------+
| - Antigravity Account 1 (OAuth / Google signed-in, All Models)                    |
| - Antigravity Account 2 (Headless Refactoring Workhorse)                          |
| - Antigravity Account 3 (Autonomous Synthesis & Verification)                     |
| - Kiro CLI (Terminal Workhorse, Cloud Ops, Docker, Kubernetes)                    |
| - Cline (Focused Headless File-Editing Workhorse)                                 |
+-----------------------------------------------------------------------------------+
```

---

## 2. Core Architectural Principles
1. **Zero IDE Interference**: Active Antigravity IDE GUI (`PID 5854`) is completely isolated and never terminated, signaled, or restarted.
2. **Confidentiality by Design**: Plaintext keys, tokens, and credentials are never printed, logged, or serialized. All credentials exist only as references (`secret://...`).
3. **Deterministic Human Approval**: Any destructive or high-risk operation (`DESTRUCTIVE`, `HIGH_RISK_WRITE`) is blocked on an approval gate until explicit operator confirmation is granted.
4. **Empirical Learning & Self-Optimization**: Dynamic agent routing weights automatically adjust based on historical success rates per domain recorded in `PerformanceRegistry`.
5. **Reversible Mutations**: Side-effect operations register compensatory rollback actions executed in LIFO order upon failure.
