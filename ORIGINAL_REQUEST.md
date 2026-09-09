# Original User Request

## Initial Request — 2026-09-08T12:22:38Z

# Teamwork Project: Phase 22 — Universal Account Onboarding & Authentication UI

Working directory: /home/setoo/YashDevops/Agentic_shared_memory
Integrity mode: development

## IMPORTANT SYSTEM & SAFETY GUARDRAILS
This is an existing production-hardened Universal AI Mission Control system covering Phases 1–21.
- DO NOT redesign or rewrite the architecture.
- DO NOT remove existing functionality or weaken existing safety gates.
- DO NOT restart, terminate, or interfere with the currently running Antigravity IDE GUI process.
- DO NOT modify ~/YashDevops/Agentic_os.
- DO NOT commit, tag, deploy, or push anything.
- NEVER display, log, or store credentials in plaintext. All secret material must flow through CredentialManager/keyring using secret:// references.
- 530 automated tests were previously passing. All 530 baseline tests must continue passing.

## CURRENT PROBLEMS
1. Dashboard shows only 4 active/online agents even though the registry contains more accounts/resources.
2. Agent & Account Registry shows errors such as "Last error: Process terminated or system restarted before task completed". These errors may be stale task state, process lifecycle state, incorrect health classification, or incorrect UI aggregation.
3. Provider Registry displays providers but there is no complete usable "Add Account" / "Add Provider" workflow.
4. Account creation from the dashboard is incomplete.
5. Authentication is not exposed as a proper user-friendly dashboard workflow.
6. Antigravity accounts use Google authentication/OAuth.
7. Cline accounts need a safe per-account authentication/profile workflow.
8. API providers such as OpenAI need a secure API-key onboarding workflow.
9. Credentials MUST NEVER be displayed in plaintext.
10. Authentication failures, cancelled login attempts, unavailable providers, stale processes, and partial setup must not corrupt the registry.

---

## REQUIREMENTS & EXECUTION PHASES

### Part 1 — Pre-Implementation Audit & Discovery
- Inspect: providers/, providers/registry/, providers/api/, providers/agents/, scripts/brain.py, brain/, brain/router/, brain/orchestrator/, ui/dashboard/, tests/unit/, tests/integration/, providers.json, account registry, credential manager, adapter base classes, bootstrap, existing dashboard API routes, existing authentication/configuration code.
- Formulate docs/PHASE22_ACCOUNT_AUTH_AUDIT.md containing:
  - current account lifecycle & provider lifecycle
  - authentication mechanisms & credential flow
  - dashboard endpoints & registry flow
  - identified defects & proposed minimal architecture

### Part 2 — Universal Account Lifecycle
- Implement formal account lifecycle:
  DISCOVERED → CONFIGURING → AUTHENTICATING → AUTHENTICATED → VALIDATING → READY → ONLINE / OFFLINE / DISABLED
- Failure states: AUTH_FAILED, AUTH_CANCELLED, VALIDATION_FAILED, PROVIDER_UNAVAILABLE, CONFIG_ERROR
- Every transition must be deterministic, auditable, safe, reversible, and represented in the registry.
- Decouple: authentication state, process state, health state, task state, availability state.
- Stale task errors ("Process terminated...") must NOT pollute active account health or display as current auth errors.

### Part 3 — Antigravity Authentication
- Use official Antigravity authentication mechanism.
- DO NOT extract OAuth tokens, copy cookies, scrape ~/.gemini, or bypass Google login.
- Support: Create isolated account identity/profile runtime, launch official auth flow safely, detect completion, validate, store safe metadata, register, run health check, make available to routing.
- Dashboard actions: [Login], [Check Authentication], [Health Check], [Disable], [Remove]. Do not touch primary Antigravity GUI profile.

### Part 4 — Cline Authentication
- Use existing verified Cline isolation (--config, --data-dir). Each account must remain isolated (cline-account-1, cline-account-2, ...).
- Run safe discovery (cline --help, cline auth --help, cline --version) and dynamically expose ONLY authentication methods verified to work on the installed version.
- API keys go directly into CredentialManager/keyring (secret:// references). Zero plaintext keys in json, logs, or UI responses.

### Part 5 — API Provider Onboarding
- Generic API provider onboarding framework supporting OpenAI, Anthropic, Gemini API, OpenAI-compatible endpoints.
- Structured validation reporting: CONNECTED, UNAUTHORIZED, RATE_LIMITED, NETWORK_ERROR, INVALID_CONFIGURATION, UNAVAILABLE.

### Part 6 — Provider Registry UI
- Provider cards: Provider, Status, Accounts, Models, Health, Capabilities.
- Actions: [+ Add Account], [Refresh], [Health Check]. Provider-specific: [Authenticate], [Configure], [Disable], [Remove].

### Part 7 — Account Manager UI
- Distinguish: ACCOUNT STATUS, PROCESS STATUS, AUTH STATUS, HEALTH STATUS, TASK STATUS.
- Isolate historical task failures from current account health status.

### Part 8 — Dashboard Active Agent Count
- Trace the root cause of the "4 Online" value.
- Break down into granular metrics: Registered, Authenticated, Healthy, Online, Busy, Idle, Offline, Disabled. Add regression tests.

### Part 9 — Account Add Wizard
- Step 1: Select Provider → Step 2: Select Auth → Step 3: Configure → Step 4: Authenticate → Step 5: Validate → Step 6: Register → Step 7: Health Check → Step 8: Complete.
- Support [Back], [Cancel], [Retry], [Validate], [Finish]. Cancel must clean up and leave no corrupted state.

### Part 10 — Live Event Stream
- Live redacted account and auth lifecycle event stream in the dashboard with correlation IDs, timestamps, safe messages, and zero secrets.

### Part 11 — Audit Integration
- Audit events via existing AuditLogger: ACCOUNT_CREATE, ACCOUNT_UPDATE, ACCOUNT_AUTH_START, ACCOUNT_AUTH_SUCCESS, ACCOUNT_AUTH_FAILURE, ACCOUNT_AUTH_CANCELLED, ACCOUNT_VALIDATE, ACCOUNT_ENABLE, ACCOUNT_DISABLE, ACCOUNT_REMOVE, PROVIDER_CREATE, PROVIDER_UPDATE, PROVIDER_HEALTH_CHECK.

### Part 12 — Smart Router Integration
- SmartRouter admits account only when registered, enabled, authenticated, healthy, and capability-compatible.
- Unhealthy accounts report explicit reasons: OFFLINE, AUTH_REQUIRED, AUTH_FAILED, DISABLED, QUOTA_EXHAUSTED, PROVIDER_UNAVAILABLE.

### Part 13 — Remove Account Safely
- [Remove] with confirmation dialog detailing what will be removed.
- Disable routing, terminate worker safely, remove registry entry, remove account config and credentials from CredentialManager, audit action. Never touch other accounts or primary GUI profile.

### Part 14 — Comprehensive Test Suite
- Implement minimum test suites:
  test_account_onboarding.py, test_account_authentication.py, test_account_lifecycle.py, test_provider_onboarding.py, test_dashboard_account_api.py, test_dashboard_provider_api.py, test_account_health_state.py, test_active_agent_count.py, test_cline_auth_isolation.py, test_antigravity_auth_safety.py, test_api_provider_credentials.py, test_live_account_events.py, test_account_audit.py, test_account_remove.py, test_router_account_integration.py.

### Part 15 — Non-Interference Verification
- Record and verify: GUI PID, GUI start time, ~/.gemini root mtime, git status in Agentic_os (zero changes), zero plaintext credentials.

### Part 16 — Regression Gate
- Run:
  python3 -m unittest discover -s tests/unit
  python3 -m unittest discover -s tests/integration
  python3 scripts/brain.py validate --full
  GUI safety suite.
- 530 baseline tests + all new tests must pass.

### Part 17 — Manual Demo Verification
- Validate Demos 1 through 6 interactively.

### Part 18 — Final Report
- Generate docs/PHASE22_ACCOUNT_AUTHENTICATION.md covering architecture, supported methods, account lifecycle, dashboard state, API endpoints, credential flow, test results, demo results, and limitations.

---

## Acceptance Criteria
- [ ] 530 baseline tests continue passing with zero regression.
- [ ] docs/PHASE22_ACCOUNT_AUTH_AUDIT.md and docs/PHASE22_ACCOUNT_AUTHENTICATION.md completed.
- [ ] Universal account lifecycle state machine implemented and tested.
- [ ] Dashboard active agents count traced and granularized.
- [ ] Stale task errors decoupled from account health.
- [ ] Antigravity, Cline, and API provider onboarding flows verified.
- [ ] Zero plaintext secrets exposed or logged.
- [ ] Running Antigravity IDE GUI PID and start time preserved.
- [ ] Zero files modified in ~/YashDevops/Agentic_os.


## Follow-up — 2026-09-09T05:53:20Z

# Teamwork Project: Phase 23 — ECC Integration + Universal Agent Capability Expansion + Local Production Hardening

Working directory: /home/setoo/YashDevops/Agentic_shared_memory
Integrity mode: development

## Project Overview
Execute Phase 23 for Mission Control in `~/YashDevops/Agentic_shared_memory`, establishing a local-only production-hardened boundary and federating capabilities from the official Everything Claude Code (ECC) repository (`https://github.com/affaan-m/ECC.git`) into the authoritative MissionBrain architecture without overwriting existing resources or compromising safety gates.

## NON-NEGOTIABLE SAFETY & GOVERNANCE RULES
1. **GUI Non-Interference:** The running Antigravity IDE GUI process (PID `3809`, started Wed Sep 9 10:01:18 2026) must NOT be terminated, signaled, reloaded, or restarted under any circumstances.
2. **Profile & Keyring Isolation:** The root `~/.gemini` directory, OAuth credentials, and the `service=gemini` keyring namespace must remain strictly read-only and untouched.
3. **Workspace Isolation:** `~/YashDevops/Agentic_os` must remain 100% untouched.
4. **Local Boundary:** Mission Control and its dashboard must remain strictly local, bound to `127.0.0.1` / `localhost`. No public exposure, no tunnels, and no cloud-hosted instances.
5. **Zero Deployment:** Do NOT commit, tag, push, or deploy any changes. All changes must remain uncommitted in the working tree for operator review.
6. **No Raw Hook Execution:** External ECC hooks, scripts, and MCPs must never execute raw or unverified. All external capabilities must flow through Mission Control's governance, approval gates, and execution fabric.

## Requirements

### R1. Local Production Hardening & Dashboard Security
- Enforce strict authentication (`Authorization: Bearer <token>`) across all sensitive dashboard API surfaces, including GET endpoints and SSE (`/api/events/stream`).
- Secure `/api/accounts`, `/api/overview`, `/api/usage`, `/api/cost`, `/api/router/*`, `/api/steering`, `/api/events`, `/api/audit/*`, `/api/providers/*`, and `/api/wizard/*` from unauthenticated data exposure.
- Enforce explicit local-only network boundary (`127.0.0.1` / `localhost`) with automated regression tests that fail if exposure to `0.0.0.0` or external interfaces is attempted.
- Implement robust local operational controls: graceful shutdown, startup health validation, stale PID tracking, port collision detection, and local log rotation.
- Author `docs/LOCAL_DEPLOYMENT.md` and `docs/PHASE23_LOCAL_HARDENING.md`.

### R2. ECC Repository Audit & Capability Normalizer
- Perform a thorough, non-destructive audit of the canonical ECC repository (`https://github.com/affaan-m/ECC.git`) without running installer scripts.
- Categorize all candidate components (Agents, Skills, Commands, Hooks, Rules, MCP Definitions, Documentation) into:
  - `A`: Safe direct integration
  - `B`: Requires adaptation
  - `C`: Reference-only
  - `D`: Reject
- Author `docs/PHASE23_ECC_AUDIT.md` detailing component inventory, licenses, duplicate capabilities, security considerations, and import decisions.

### R3. Universal Capability Federation Layer
- Integrate ECC skills, tools, and workflows into the existing `skill_discovery.py`, `resource_registry.py`, and `tool_catalog.py` without creating duplicate registries.
- Normalize each imported capability with stable ID, name, description, provenance hash, risk level, required tools/MCPs, compatible agents, and approval requirements.
- Import ECC engineering practices (code review, test-first workflows, architecture review, security review, regression analysis) into the BM25 knowledge index and steering federation.
- Map ECC agent roles into `external_agent_capability` adapters routable to existing local agents (Antigravity, Cline, Kiro CLI, API providers) based on capability fit.
- Establish rule precedence: Safety & Governance > Project Steering > Mission Control Architecture > Existing Repo Rules > ECC Imported Rules.

### R4. Automatic Capability Discovery & Smart Router Integration
- Upgrade MissionBrain and Smart Router to automatically select and inject relevant ECC-derived skills, MCP tools, and steering documents based on task intent without requiring the user to say "use ECC".
- Extend `brain route explain` to display why each capability, skill, documentation item, and agent was selected.
- Provide an emergency disable mechanism (`brain external disable ecc`) to instantly disable all external ECC capabilities without deleting them.

### R5. Provider & Account Compatibility
- Preserve and verify Phase 11/12/22 multi-account functionality:
  - Antigravity: isolated account profiles, official auth verification, GUI safety.
  - Cline: verified `--config` and `--data-dir` per-account isolation (`cline-account-1`, `cline-account-2`, `cline-account-3`).
  - Kiro CLI: explicit single-account limitation.
  - API Providers: safe CredentialManager secret storage (zero plaintext secrets).
  - Ollama: graceful offline fallback when daemon is uninstalled/stopped.

### R6. Comprehensive Testing, Failure Injection & Verification
- Implement unit and integration suites:
  - `tests/unit/test_phase23_ecc.py`
  - `tests/unit/test_phase23_skill_federation.py`
  - `tests/unit/test_phase23_resource_routing.py`
  - `tests/unit/test_phase23_hooks.py`
  - `tests/unit/test_phase23_provider_compatibility.py`
  - `tests/unit/test_phase23_security.py`
  - `tests/integration/test_phase23_ecc_integration.py`
  - `tests/integration/test_phase23_cross_feature_regression.py`
  - `tests/integration/test_phase23_dashboard.py`
  - `tests/integration/test_phase23_full_mission.py`
- Execute failure injection tests (missing credentials, offline providers, malformed ECC workflows, unauthenticated requests).
- Author `docs/PHASE23_REGRESSION_MATRIX.md`, `docs/PHASE23_ECC_INTEGRATION.md`, and `docs/PHASE23_FINAL_REPORT.md`.

## Acceptance Criteria

### Security & Local Boundary
- [ ] All sensitive dashboard GET and SSE endpoints return `401 Unauthorized` when unauthenticated.
- [ ] Valid Bearer token requests succeed across all protected endpoints.
- [ ] Dashboard server strictly binds to `127.0.0.1` / `localhost`; regression test verifies non-local binding is blocked.
- [ ] Zero plaintext secrets exposed in logs, runtime, dashboard responses, or audit records.

### Capability Federation & Routing
- [ ] Canonical ECC repository audited with complete classification in `docs/PHASE23_ECC_AUDIT.md`.
- [ ] Normalized ECC skills discoverable via `skill_discovery.py` and `resource_registry.py`.
- [ ] High-risk ECC hooks default to `DISABLED` and enforce explicit approval gates.
- [ ] Smart Router automatically selects ECC-derived capabilities for security, review, and debugging tasks without explicit operator keywords.
- [ ] Emergency disable command immediately deactivates ECC routing while keeping baseline capabilities intact.

### Non-Interference & Regression Integrity
- [ ] Antigravity IDE GUI process (PID `3809`) start time and PID preserved unchanged.
- [ ] Root `~/.gemini` directory mtime and `service=gemini` keyring namespace untouched.
- [ ] Zero modifications in `~/YashDevops/Agentic_os`.
- [ ] 100% of baseline unit and integration tests continue passing with zero regressions.
- [ ] All new Phase 23 unit and integration tests pass cleanly (zero failures, zero errors).
- [ ] All changes remain uncommitted in the local working tree.
