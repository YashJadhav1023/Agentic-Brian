# Project: Phase 23 — ECC Integration + Universal Agent Capability Expansion + Local Production Hardening

## Architecture
Universal AI Mission Control Phase 23 establishes a local-only production-hardened boundary and federates capabilities from the canonical Everything Claude Code (ECC) repository into the authoritative MissionBrain architecture without overwriting existing resources or compromising safety gates.

### Core Architectural Tracks:
1. **Local Boundary & Dashboard Security Fabric (R1)**:
   - Enforces strict Bearer token authentication across all sensitive GET endpoints and SSE (`/api/events/stream`), supporting dual-mode authentication (Header `Authorization: Bearer <token>` and query parameter `?token=<token>`).
   - Restricts host binding to local loopback interfaces (`127.0.0.1`, `localhost`), rejecting `0.0.0.0` or external network exposure.
   - Implements local operational controls: `runtime/dashboard.pid` stale PID detection (with non-interference guard protecting running Antigravity IDE GUI PID 3809), `SIGTERM`/`SIGINT` graceful shutdown, friendly port collision error handling, startup health validation, and size-based log rotation (10 MB threshold, 5 backups) for `runtime/*.jsonl`.
2. **ECC Audit & Normalization Layer (R2)**:
   - Performs a non-destructive audit of the canonical ECC repository (v2.2.1), categorizing all candidate components (Agents, Skills, Commands, Hooks, Rules, MCP Definitions, Documentation) into:
     - `A`: Safe direct integration (pure data/markdown, read-only)
     - `B`: Requires adaptation (coupled to Claude Code or specialized tools)
     - `C`: Reference-only (personal workflows, roadmap docs)
     - `D`: Reject (raw shell scripts, unverified hooks, installers, cloud memory sync)
   - Computes provenance fingerprints (SHA-256) and authors `docs/PHASE23_ECC_AUDIT.md`.
3. **Universal Capability Federation Layer (R3)**:
   - Ingests normalized capabilities directly into existing `ResourceRegistry`, `SkillDiscoveryEngine`, `MCPToolCatalog`, and BM25 `KnowledgeIndex` without duplicate registries.
   - Enforces strict 5-tier rule precedence:
     1. Safety & Governance (Priority 100)
     2. Project Steering (Priority 80)
     3. Mission Control Architecture (Priority 60)
     4. Existing Repo Rules (Priority 40)
     5. ECC Imported Rules (Priority 20)
   - Maps ECC functional agent roles into `external_agent_capability` adapters routable to existing local agents (Antigravity, Cline, Kiro CLI, API providers).
   - High-risk hooks default to `DISABLED` with explicit approval gates.
4. **Smart Router & Automatic Capability Discovery (R4)**:
   - Enhances `TaskClassifier` with specialized intent detection (Security Review, Test-First/TDD, Architecture Review).
   - Upgrades `ResourceSelector` in `ContextBuilder` to bridge into `ResourceRegistry` and automatically select and inject relevant ECC-derived skills, tools, and steering rules based on task intent without requiring the user to explicitly say "use ECC".
   - Extends `brain route explain` to display complete selection rationale across agents, providers, capabilities, skills, MCP tools, and steering documents.
   - Implements emergency disable switch (`brain external disable ecc` / `enable`), storing atomic state in `runtime/config/external_capabilities.json` with zero file deletion.
5. **Provider & Account Compatibility (R5)**:
   - Preserves multi-account isolation and safety:
     - Antigravity: isolated profiles via `--app_data_dir`, D-Bus keyring shielding (`DBUS_SESSION_BUS_ADDRESS="/dev/null"`), GUI PID 3809 preserved.
     - Cline: `--config` and `--data-dir` isolation (`cline-account-1, 2, 3`), `--auto-approve false` default, `secret://` credential storage.
     - Kiro CLI: verified single-account limitation (`multi_account = False`).
     - API Providers: safe CredentialManager secret storage (zero plaintext secrets).
     - Ollama: graceful offline fallback when daemon is down.
6. **E2E Testing Track & Verification (R6)**:
   - Comprehensive test suite covering 10 required Phase 23 test suites (~205 new tests), failure injection testing, regression matrix verification (maintaining 780 baseline tests), and authoring final documentation.

---

## Feature Inventory
Every feature from Phase 23 requirements is enumerated below with its assigned milestone:

| # | Feature | Description | Milestone | Source |
|---|---|---|---|---|
| F01 | Sensitive GET Endpoint Auth | Enforce `Authorization: Bearer <token>` on `/api/accounts`, `/api/overview`, `/api/usage`, `/api/cost`, `/api/router/*`, `/api/steering`, `/api/events`, `/api/audit/*`, `/api/providers/*`, `/api/wizard/*` returning 401 on unauthenticated access | M1 | R1 |
| F02 | Public Allow-List Delineation | Maintain unauthenticated access only for `/`, `/static/*`, `/api/token`, `/api/health`, `/api/status` | M1 | R1 |
| F03 | Dual-Mode SSE Stream Auth | Support Bearer token header and query parameter `?token=<token>` on `/api/events/stream` returning 401 on missing/invalid token | M1 | R1 |
| F04 | Frontend Auth Alignment | Update dashboard JS to fetch with token for all periodic queries and connect EventSource with token | M1 | R1 |
| F05 | Strict Local Loopback Boundary | Enforce `127.0.0.1` / `localhost` binding in server and CLI, raising SecurityError and blocking `0.0.0.0` | M1 | R1 |
| F06 | Stale PID File Tracking | Implement `runtime/dashboard.pid` tracking with stale PID cleanup and explicit safety guard protecting GUI PID 3809 | M1 | R1 |
| F07 | Graceful Shutdown & Signal Handling | Handle `SIGTERM` and `SIGINT` with clean server shutdown, socket closing, and PID removal | M1 | R1 |
| F08 | Friendly Port Collision Handling | Catch `EADDRINUSE` and display friendly remediation guidance without unhandled tracebacks | M1 | R1 |
| F09 | Startup Health Validation | Validate write permissions, token generation, registry initialization, and loopback interface prior to listening | M1 | R1 |
| F10 | Local Log Rotation | Implement size-based rotation (10 MB threshold, 5 backups) for `runtime/*.jsonl` log and audit ledgers | M1 | R1 |
| F11 | Hardening Documentation | Author `docs/LOCAL_DEPLOYMENT.md` and `docs/PHASE23_LOCAL_HARDENING.md` | M1 | R1 |
| F12 | Non-Destructive ECC Repo Audit | Inspect canonical ECC repo v2.2.1, census components, license verification, zero raw script execution | M2 | R2 |
| F13 | Component Categorization (A/B/C/D) | Categorize all agents, skills, commands, rules, hooks, MCPs, docs into A (safe direct), B (adaptation), C (reference), D (reject) | M2 | R2 |
| F14 | ECC Audit Documentation | Author `docs/PHASE23_ECC_AUDIT.md` detailing inventory, licenses, duplicates, and import decisions | M2 | R2 |
| F15 | Universal Capability Normalization | Normalize capabilities with stable ID (`ecc:<type>:<name>`), provenance SHA-256, risk level, required tools, compatible agents, approval requirements | M3 | R3 |
| F16 | Single Registry Federation | Ingest ECC skills and resources directly into `ResourceRegistry`, `SkillDiscoveryEngine`, and `MCPToolCatalog` without duplicate registries | M3 | R3 |
| F17 | Engineering Practices BM25 Indexing | Index code review, test-first, architecture review, security review, and regression analysis into `KnowledgeIndex` | M3 | R3 |
| F18 | Enforced 5-Tier Rule Precedence | Enforce Safety (100) > Project (80) > Architecture (60) > Repo (40) > ECC (20) in `SteeringRegistry` with automatic conflict suppression | M3 | R3 |
| F19 | Agent Role Capability Adapters | Map ECC agent roles to existing local agent adapters (Antigravity, Cline, Kiro CLI, API) based on Capability fit | M3 | R3 |
| F20 | Hook Governance & Default Disabled | Wrap external hooks in governance action descriptors, default to `DISABLED`, require explicit approval tokens | M3 | R3 |
| F21 | Intent-Based Automatic Discovery | Enhance `TaskClassifier` and `ResourceSelector` to automatically match and inject relevant ECC skills/tools/steering by task intent | M4 | R4 |
| F22 | Smart Router Explainability | Extend `brain route explain` in `SmartRouter` and `scripts/brain.py` to detail agent, provider, capability, skill, tool, steering selection rationale | M4 | R4 |
| F23 | Emergency Disable Mechanism | Implement `brain external disable ecc` and `enable` using atomic config in `runtime/config/external_capabilities.json` with zero file deletion | M4 | R4 |
| F24 | Provider Multi-Account Compatibility | Verify and maintain Antigravity profile isolation/keyring shielding, Cline directory isolation, Kiro single-account, API CredentialManager secrets, Ollama fallback | M5 | R5 |
| F25 | Direct-API Capability Guard | Ensure API-only accounts exclude terminal operations and shell execution capabilities | M5 | R5 |
| F26 | GUI Non-Interference Guard | Verify Antigravity IDE GUI process (PID 3809) remains running, untouched, and un-signaled across all operations | M5 | R5 |
| F27 | Unit Test Suites (Suites 1-6) | Implement `test_phase23_ecc.py`, `test_phase23_skill_federation.py`, `test_phase23_resource_routing.py`, `test_phase23_hooks.py`, `test_phase23_provider_compatibility.py`, `test_phase23_security.py` | M6 | R6 |
| F28 | Integration Test Suites (Suites 7-10) | Implement `test_phase23_ecc_integration.py`, `test_phase23_cross_feature_regression.py`, `test_phase23_dashboard.py`, `test_phase23_full_mission.py` | M6 | R6 |
| F29 | Failure Injection Testing | Execute failure injection tests: missing credentials, offline provider outage, malformed workflows/path traversal, unauthenticated dashboard attacks | M6 | R6 |
| F30 | Regression & Safety Verification | Verify 100% of 780 baseline tests pass, `~/.gemini` mtime untouched, `Agentic_os` untouched, uncommitted working tree preserved | M6 | R6 |
| F31 | Final Documentation Suite | Author `docs/PHASE23_REGRESSION_MATRIX.md`, `docs/PHASE23_ECC_INTEGRATION.md`, and `docs/PHASE23_FINAL_REPORT.md` | M6 | R6 |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| M1 | Local Production Hardening & Dashboard Security | Features F01–F11: Sensitive GET auth, public allow-list, dual-mode SSE auth, frontend alignment, loopback boundary (127.0.0.1), stale PID tracking, graceful shutdown, port collision handling, startup health checks, log rotation, `docs/LOCAL_DEPLOYMENT.md`, `docs/PHASE23_LOCAL_HARDENING.md` | none | IN_PROGRESS |
| M2 | ECC Repository Audit & Capability Normalizer | Features F12–F14: Non-destructive audit of ECC v2.2.1, component categorization (A/B/C/D), installer/hook rejection, `docs/PHASE23_ECC_AUDIT.md` | none | IN_PROGRESS |
| M3 | Universal Capability Federation Layer | Features F15–F20: Capability normalization schema, federation into `ResourceRegistry`, BM25 indexing of engineering practices, 5-tier rule precedence in `SteeringRegistry`, agent role mapping to existing adapters, hook governance with default-disabled state | M2 | PLANNED |
| M4 | Smart Router & Automatic Capability Discovery | Features F21–F23: Intent-based automatic skill/tool/steering discovery and injection in `TaskClassifier` & `ResourceSelector`, `brain route explain` expansion, emergency disable mechanism (`brain external disable ecc`) | M3 | PLANNED |
| M5 | Provider & Account Compatibility Verification | Features F24–F26: Multi-account isolation verification (Antigravity, Cline, Kiro, API, Ollama), direct-API capability guard, GUI process PID 3809 non-interference verification | M1, M4 | PLANNED |
| M6 | Final Milestone: E2E Integration, Adversarial Hardening & Verification | Features F27–F31: 10 Phase 23 test suites, failure injection testing, 780 baseline regression verification, full mission integration, `docs/PHASE23_REGRESSION_MATRIX.md`, `docs/PHASE23_ECC_INTEGRATION.md`, `docs/PHASE23_FINAL_REPORT.md` | M1, M2, M3, M4, M5 | PLANNED |

---

## Interface Contracts

### 1. Dashboard Authentication ↔ Client
- **GET /api/events/stream**:
  - Headers: `Authorization: Bearer <token>` OR Query Parameter: `?token=<token>` (or `?access_token=<token>`)
  - Success: HTTP 200 `text/event-stream`
  - Failure: HTTP 401 `application/json` `{"error": "Unauthorized", "message": "Valid Bearer token required on /api/events/stream"}` with `WWW-Authenticate: Bearer realm="MissionControl"`
- **Sensitive GET Endpoints**:
  - Header: `Authorization: Bearer <token>`
  - Success: HTTP 200 `application/json`
  - Failure: HTTP 401 `application/json`
- **Public Allow-list Endpoints**:
  - `/`, `/static/*`, `/api/token`, `/api/health`, `/api/status` -> HTTP 200 without Authorization header.

### 2. External Capability Manager ↔ MissionBrain & SmartRouter
- **Module**: `brain/resources/external_manager.py`
  - Class: `ExternalCapabilityManager`
  - Methods:
    - `is_enabled(source: str) -> bool`
    - `disable_source(source: str, reason: str = "") -> bool`
    - `enable_source(source: str) -> bool`
    - `get_status() -> dict[str, Any]`
  - Persistence: `runtime/config/external_capabilities.json`

### 3. ResourceRegistry ↔ Capability Normalizer
- **Normalized Resource Schema**:
  - `id`: `ecc:<type>:<name>` (e.g. `ecc:skill:security-review`, `ecc:agent:architect`)
  - `type`: `ResourceType.SKILL`, `ResourceType.STEERING`, `ResourceType.AGENT`, `ResourceType.MCP_SERVER`
  - `source`: `"ecc"`
  - `fingerprint`: SHA-256 digest of source content
  - `metadata`: `{"origin": "https://github.com/affaan-m/ECC.git", "version": "2.2.1", "risk_level": "LOW_RISK" | "HIGH_RISK", "required_tools": list[str], "compatible_agents": list[str], "approval_required": bool}`

### 4. SteeringRegistry ↔ 5-Tier Rule Precedence
- **Precedence Hierarchy**:
  - Tier 1: Safety & Governance (`priority: 100`)
  - Tier 2: Project Steering (`priority: 80`)
  - Tier 3: Mission Control Architecture (`priority: 60`)
  - Tier 4: Existing Repo Rules (`priority: 40`)
  - Tier 5: ECC Imported Rules (`priority: 20`)
- **Conflict Resolution**:
  - Higher-tier rule takes precedence; conflicting lower-tier rule suppressed with reason recorded in `TaskContext.steering_conflicts`.

---

## Code Layout

### Source Code Boundaries
- Dashboard & Network: `ui/dashboard/dashboard.py`
- Resource Registry & Normalization: `brain/resources/resource_registry.py`, `brain/resources/resource_model.py`, `brain/resources/skill_discovery.py`, `brain/resources/external_manager.py`
- Knowledge Index & Steering: `brain/knowledge/knowledge_index.py`, `brain/knowledge/steering_registry.py`
- Context Builder & Selector: `brain/context/context_builder.py`
- Smart Router & Classifier: `brain/router/smart_router.py`, `brain/router/classification.py`
- CLI Commands: `scripts/brain.py`
- Agent Adapters: `agents/base/adapter.py`, `agents/antigravity/`, `agents/cline/`, `agents/kiro/`, `providers/api/`
- Operational Controls: `brain/analytics/log_rotator.py`, `ui/dashboard/dashboard.py`

### Test Code Boundaries
- Unit Tests: `tests/unit/test_phase23_*.py`
- Integration Tests: `tests/integration/test_phase23_*.py`
- Existing Baseline Tests: `tests/unit/`, `tests/integration/` (must maintain 100% pass rate on all 780 tests)
- GUI Safety Assertions: `tests/support/gui_guard.py`

### Documentation Boundaries
- `docs/LOCAL_DEPLOYMENT.md`
- `docs/PHASE23_LOCAL_HARDENING.md`
- `docs/PHASE23_ECC_AUDIT.md`
- `docs/PHASE23_REGRESSION_MATRIX.md`
- `docs/PHASE23_ECC_INTEGRATION.md`
- `docs/PHASE23_FINAL_REPORT.md`
