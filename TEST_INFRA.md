# E2E Test Infra: Phase 23 Universal AI Mission Control

## Test Philosophy
- Opaque-box, requirement-driven, non-interfering. Derived strictly from `ORIGINAL_REQUEST.md` (Follow-up 2026-09-09T05:53:20Z).
- Methodology: Category-Partition + Boundary Value Analysis + Pairwise Combinatorial Testing + Real-World Workload Testing.
- Strict Non-Interference: Running Antigravity IDE GUI process (PID 3809) must never be signaled or terminated. `~/.gemini` root and `Agentic_os` remain 100% untouched.

## Feature Inventory Mapping
| # | Feature | Requirement | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Pairwise) | Tier 4 (Scenario) |
|---|---|---|:---:|:---:|:---:|:---:|
| F01 | Sensitive GET Endpoint Auth | R1 | 5 | 5 | ✓ | ✓ |
| F02 | Public Allow-List Delineation | R1 | 5 | 5 | ✓ | ✓ |
| F03 | Dual-Mode SSE Stream Auth | R1 | 5 | 5 | ✓ | ✓ |
| F04 | Frontend Auth Alignment | R1 | 5 | 5 | ✓ | ✓ |
| F05 | Strict Local Loopback Boundary | R1 | 5 | 5 | ✓ | ✓ |
| F06 | Stale PID File Tracking | R1 | 5 | 5 | ✓ | ✓ |
| F07 | Graceful Shutdown & Signal Handling | R1 | 5 | 5 | ✓ | ✓ |
| F08 | Friendly Port Collision Handling | R1 | 5 | 5 | ✓ | ✓ |
| F09 | Startup Health Validation | R1 | 5 | 5 | ✓ | ✓ |
| F10 | Local Log Rotation | R1 | 5 | 5 | ✓ | ✓ |
| F11 | Hardening Documentation | R1 | 5 | 5 | ✓ | ✓ |
| F12 | Non-Destructive ECC Repo Audit | R2 | 5 | 5 | ✓ | ✓ |
| F13 | Component Categorization (A/B/C/D) | R2 | 5 | 5 | ✓ | ✓ |
| F14 | ECC Audit Documentation | R2 | 5 | 5 | ✓ | ✓ |
| F15 | Universal Capability Normalization | R3 | 5 | 5 | ✓ | ✓ |
| F16 | Single Registry Federation | R3 | 5 | 5 | ✓ | ✓ |
| F17 | Engineering Practices BM25 Indexing | R3 | 5 | 5 | ✓ | ✓ |
| F18 | Enforced 5-Tier Rule Precedence | R3 | 5 | 5 | ✓ | ✓ |
| F19 | Agent Role Capability Adapters | R3 | 5 | 5 | ✓ | ✓ |
| F20 | Hook Governance & Default Disabled | R3 | 5 | 5 | ✓ | ✓ |
| F21 | Intent-Based Automatic Discovery | R4 | 5 | 5 | ✓ | ✓ |
| F22 | Smart Router Explainability | R4 | 5 | 5 | ✓ | ✓ |
| F23 | Emergency Disable Mechanism | R4 | 5 | 5 | ✓ | ✓ |
| F24 | Provider Multi-Account Compatibility | R5 | 5 | 5 | ✓ | ✓ |
| F25 | Direct-API Capability Guard | R5 | 5 | 5 | ✓ | ✓ |
| F26 | GUI Non-Interference Guard | R5 | 5 | 5 | ✓ | ✓ |
| F27 | Unit Test Suites (Suites 1-6) | R6 | 5 | 5 | ✓ | ✓ |
| F28 | Integration Test Suites (Suites 7-10) | R6 | 5 | 5 | ✓ | ✓ |
| F29 | Failure Injection Testing | R6 | 5 | 5 | ✓ | ✓ |
| F30 | Regression & Safety Verification | R6 | 5 | 5 | ✓ | ✓ |
| F31 | Final Documentation Suite | R6 | 5 | 5 | ✓ | ✓ |

## Test Architecture
- Test Runner: Python stdlib `unittest` via `python3 -m unittest discover -s tests/unit` and `python3 -m unittest discover -s tests/integration`.
- Test Case Format: Self-contained unittest test cases subclassing `unittest.TestCase` with ephemeral sandboxes, mock directories, and tearDown cleanup.
- Test Layout:
  - `tests/unit/test_phase23_ecc.py` (~25 tests)
  - `tests/unit/test_phase23_skill_federation.py` (~20 tests)
  - `tests/unit/test_phase23_resource_routing.py` (~25 tests)
  - `tests/unit/test_phase23_hooks.py` (~20 tests)
  - `tests/unit/test_phase23_provider_compatibility.py` (~25 tests)
  - `tests/unit/test_phase23_security.py` (~20 tests)
  - `tests/integration/test_phase23_ecc_integration.py` (~15 tests)
  - `tests/integration/test_phase23_cross_feature_regression.py` (~20 tests)
  - `tests/integration/test_phase23_dashboard.py` (~20 tests)
  - `tests/integration/test_phase23_full_mission.py` (~15 tests)

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|---|---|---|
| S1 | End-to-End Security Audit & Remediation: User requests security review; SmartRouter detects intent, auto-selects ECC security skill and steering, maps to Antigravity, passes approval gate, and outputs audit report. | F01, F15, F16, F17, F18, F19, F21, F22 | High |
| S2 | Local Production Daemon Lifecycle: Dashboard starts on 127.0.0.1:3333, verifies startup health, tracks PID, serves authenticated GET & SSE stream, handles SIGTERM gracefully with PID cleanup. | F01, F03, F05, F06, F07, F09 | High |
| S3 | Emergency Capability Deactivation: Operator executes `brain external disable ecc`, instantly deactivating all ECC skills and steering rules from SmartRouter without deleting files. | F16, F21, F23 | Medium |
| S4 | Multi-Account Provider Compatibility Under Load: Dispatches parallel tasks across Antigravity, Cline, Kiro, and API accounts, verifying profile isolation and offline fallback for Ollama. | F24, F25, F26, F28 | High |
| S5 | Adversarial Malformed Workflow Quarantine: Injects malformed YAML, path traversal payload, and raw hook script; system catches and quarantines all unsafe elements. | F13, F15, F20, F29 | High |

## Coverage Thresholds
- Baseline Preservation: 100% of 780 Phase 22 tests pass.
- New Phase 23 Tests: >= 200 new automated tests.
- Total Test Count: >= 980 tests.
- Safety Invariants: PID 3809 alive and unmodified, `~/.gemini` untouched, `Agentic_os` untouched.
