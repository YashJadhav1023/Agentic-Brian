# Full-System Validation Report (Parts 0–32)

**Date:** 2026-09-08 · **Branch:** `main` (working tree, uncommitted) · **Scope:** Mission Control full validation per operator mandate.

## Executive summary

Full regression + isolation + safety validation executed. Final state: **unit 429/429, integration 101/101, master gate 18/18 PASS**. Four defects were found and fixed this session (BUG-002..BUG-005); BUG-001 was fixed earlier in the same session. No commits were made. The Antigravity GUI, `~/.gemini`, and the `service=gemini` keyring were not disturbed (Part 22/32 snapshot re-verified at end of session).

## Bugs found this session (all fixed, all regression-tested)

### BUG-002 — Destructive instruction auto-executed from shared swarm queue (P0, FIXED)
- **Root cause:** `swarm.run_queue`/`execute_task` had no approval gate; destructive instructions dispatched via `plan_and_dispatch` entered READY and could auto-execute.
- **Fix:** `requires_approval`/`approval_reason` on Task + `DESTRUCTIVE_INSTRUCTION_PATTERN` stamping in orchestrator; BLOCKED_ON_APPROVAL flip in swarm; `APPROVAL_REQUIRED` event; `approve`/`reject` CLI. (Enriched by a concurrent agent session; merged.)
- **Regression tests:** `tests/unit/test_approval_gate.py` (10), `tests/integration/test_approval_gate.py` (7). Live lifecycle verified: gated dispatch → `execute` refuses → `approve` → READY → `reject` → REJECTED terminal.

### BUG-003 — Integration tests drained the repo's live task queue (P1, FIXED)
- **Root cause:** `test_api_security.py` spawned the real dashboard server against the module-level TaskManager bound to `PROJECT_ROOT/tasks`; dashboard job endpoints spawn `orchestrator.execute_next()` threads — tests could execute production tasks. `test_mission_control_security.py` isolated `dashboard.task_manager` and `orchestrator._task_manager` but **not** `orchestrator._swarm._task_manager` (the component that actually executes).
- **Fix:** full isolation harness in both files (dashboard.task_manager + orchestrator + swarm redirected to tmp TaskManager; `MISSION_CONTROL_AUTH_TOKEN` pinned via env so the repo's `runtime/mission_control.token` is never read/written; restored in tearDownClass).
- **Regression test:** the suites themselves; verified 17/17 (api_security + mission_control_security) post-fix.

### BUG-004 — `brain validate` command lost from CLI (P2, FIXED)
- **Root cause:** concurrent-agent rewrite of `scripts/brain.py` (≈2,077 added lines) dropped the argparse subparser, handler mapping, and module-level `tempfile`/`shutil` imports for the Part 28 `cmd_validate` implementation.
- **Fix:** re-registered `validate` subparser (`--json`, `--full`), handler entry, imports. Verified: `validate --full` runs; JSON mode emits machine-readable summary; exit 0 on all-pass, 1 on any failure.

### BUG-005 — Cline multi-account collapse in registry bootstrap (P1, FIXED)
- **Root cause:** `providers/registry/bootstrap.py` built every `ClineAdapter` without `agent_id`/`account_id`, so the class defaults (`cline`/`cli`) applied and `Provider.add_adapter()` keyed all three configured cline accounts under one dict entry. The first-class AccountRegistry listed all 8 accounts, but the adapter/routing/execution surface collapsed to one — degrading account isolation, telemetry attribution, and candidate scoring.
- **Fix:** bootstrap now passes `agent_id` and `account_id` from the config view for cline.
- **Regression test:** `tests/unit/test_account_registry.py::TestBootstrapClineAccountIdentity` (adapter keys == AccountRegistry IDs == the 3 configured accounts). Four legacy tests updated to the account-aware contract (test_smart_router ×2, test_agent_adapters, test_phase6b_runtime_truth, test_agent_sandboxes, test_phase20_mission_brain_e2e) — expectations strengthened, none weakened/deleted.
- **Verification:** 71/71 targeted + full suites green (see below).

## Validator API-drift corrections (BUG-004 follow-up, in gate code only)
`cmd_validate` initially used wrong APIs (import `brain.performance.registry` → actual `brain.analytics.performance_registry`; `rank` → `rank_documents`; `candidates` → `candidate_scores`; catalog needs `sync_with_registry`; account count now sourced from first-class AccountRegistry). All corrected; gate now 18/18.

## Final verified results

| Suite | Result |
|---|---|
| Unit (`python3 -m unittest discover -s tests/unit`) | **429/429 OK** (exit 0) |
| Integration (`discover -s tests/integration`) | **101/101 OK** (exit 0) |
| Security isolation suites (api_security + mc_security) | 17/17 OK |
| Approval-gate suites (unit + integration) | 17/17 OK |
| Master gate `python3 scripts/brain.py validate --full` | **18/18 PASS** (exit 0, JSON: `/tmp/validate_final.json`) |

Gate highlights: 8 accounts / 4 providers · 7 healthy agents · 11 MCP servers (8 healthy, 3 unknown, 0 offline) · 26 tools (15 read-only, 2 destructive-classified, gated) · 87 docs indexed (BM25 returning 10) · routing scoring 7 candidates · audit free of plaintext secrets.

## Live verification boundaries (NOT VERIFIED — stated explicitly)

- **Part 19 live GUI demo mission:** NOT performed this session (dashboard not left running; no listener on :3333 at session end). NOT VERIFIED.
- **MCP live invocation:** registry health + catalog enumeration verified in-process; no live tool call against external MCP services. NOT VERIFIED.
- **Provider live execution:** no real Antigravity/Cline/Kiro execution was triggered during validation (safety mandate); E2E missions covered via the integration suites' sandboxed/mock execution.
- **Keyring:** fingerprint check via secretstorage unavailable in this environment (`keyring-check-skipped`); no keyring reads/writes performed.

## Non-interference (Parts 22/32)

- GUI PIDs verified stable: agy hub **4904** (start 12:07:43), IDE **5854** (12:11:16). No signals sent, no restarts.
- `~/.gemini`: no writes by this session (recent mtimes belong to the live IDE's own logs).
- `~/YashDevops/Agentic_os`: **zero writes by this session** (verified). Same-day git object/log activity exists in that repo from a source external to this session — flagged for operator awareness, not caused here.
- No commits, tags, or pushes. All changes are uncommitted working-tree state for operator review.

## Production readiness

**READY WITH LIMITATIONS** — suite and gate are green; live-execution and live-GUI-demo paths remain operator-supervised activities per mandate.
