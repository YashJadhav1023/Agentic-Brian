# Phase 22 — Universal Account Onboarding & Authentication

Final report (Part 18). Scope: `~/YashDevops/Agentic_shared_memory`. No cloud
environment was touched, nothing was committed, tagged, or pushed, and
`~/YashDevops/Agentic_os` was left byte-identical.

---

## Outcome

| Gate | Result |
|---|---|
| Full test suite | **780 tests, OK** (1 skipped), 269.0s |
| Regression floor | 574 tests before this work → **+206 tests**, zero regressions |
| `scripts/brain.py validate --full` | **18 PASSED / 0 FAILED / 18 checks** |
| Part 14 required test files | **15 of 15 present**, 213 tests total |
| Part 17 automated demo verification | **30 tests, OK** |
| Non-interference | GUI, `~/.gemini`, Agentic_os, and the account registry all verified unchanged |
| Plaintext credentials | none; all credential material referenced by `secret://` URI |

Reproduce with:

```bash
cd ~/YashDevops/Agentic_shared_memory
python3 -m unittest discover -s tests
python3 scripts/brain.py validate --full
```

Individual files run under the same import root discovery uses:

```bash
PYTHONPATH=.:tests python3 -m unittest unit.test_account_onboarding
```

---

## Part 14 — Comprehensive test suite

All 15 files named in the request now exist. Ten were written in this session;
five already existed from Milestone 1.

| Test file | Tests | Covers |
|---|---:|---|
| `test_account_onboarding.py` | 12 | Progressive onboarding walk; illegal skips; failure branches |
| `test_account_authentication.py` | 22 | Antigravity + Cline auth, profile isolation, path escapes |
| `test_account_lifecycle.py` | 9 | 14-state machine (pre-existing) |
| `test_provider_onboarding.py` | 17 | Endpoint resolution, structured validation, registry |
| `test_dashboard_account_api.py` | 17 | `/api/accounts`, `/api/accounts/metrics`, 404 handling |
| `test_dashboard_provider_api.py` | 18 | `/api/providers`, wizard provider/login-method discovery |
| `test_account_health_state.py` | 3 | Health/task decoupling (pre-existing) |
| `test_active_agent_count.py` | 17 | Part 8 root cause; eight independent metrics |
| `test_cline_auth_isolation.py` | 7 | Cline profile isolation (pre-existing) |
| `test_antigravity_auth_safety.py` | 8 | GUI-safe auth (pre-existing) |
| `test_api_provider_credentials.py` | 10 | API credential handling (pre-existing) |
| `test_live_account_events.py` | 23 | Part 10 event names, correlation, redaction, backpressure |
| `test_account_audit.py` | 13 | Part 11 coverage, correlation, redaction |
| `test_account_remove.py` | 17 | Part 13 guards, drain-not-kill, sibling isolation |
| `test_router_account_integration.py` | 20 | Part 12 admission gate and rejection reasons |

---

## Defects found and fixed

Writing the tests surfaced five real problems. Each is a case where the feature
existed but had never been executed.

### 1. `safe_remove_account` crashed on any real directory removal

`providers/registry/account_registry.py` called `shutil.rmtree(...)` and
`logger.debug(...)` inside `safe_remove_account`, but the module imported
neither `shutil` nor `logging` and defined no module-level `logger`. Removal
therefore raised `NameError: name 'shutil' is not defined` the first time it was
asked to delete an account's profile directory, and the subsequent
`except` handler raised a second `NameError` on `logger`, masking the first.

Part 13 had no test that removed a directory, so this never surfaced.

**Fix:** added `import logging`, `import shutil`, and
`logger = logging.getLogger(__name__)` to the module.

### 2. Non-interference tests asserted a hardcoded PID

`test_phase20_mission_brain_e2e.py` and `test_phase21_production_e2e.py` both
ran `ps -p 5854` and asserted the Antigravity IDE GUI was that exact PID. This
was wrong in two independent ways:

* It fails whenever the user legitimately restarts the IDE. Both tests were in
  fact failing at the start of this session for precisely that reason — the
  measured baseline was *574 tests with 2 failures*, not the clean `OK` the
  previous checkpoint recorded.
* PIDs are recycled. By the time this session began, PID 5854 had been
  reassigned to a kernel worker thread (`kworker/u16:16-btrfs-endio`). A bare
  `ps -p 5854` therefore *succeeded* while pointing at something that was never
  the GUI. Pinning a number can silently assert the wrong process.

**Fix:** added `tests/support/gui_guard.py`, which discovers the GUI by
executable path and reports PID and start time as observed evidence. Both tests
now use it and skip — rather than fail — when the IDE is genuinely not running,
because a closed editor is not an interference violation.

### 3. The guard itself could be fooled by its own harness

`pgrep -f /opt/antigravity-ide/antigravity-ide` matches the whole command line,
so any shell command that merely *mentions* the path counts as a match. During
verification this produced 11 matches where only 9 were real GUI processes; the
two extras were the verification harness's own shell.

**Fix:** each candidate PID is confirmed by resolving `/proc/<pid>/exe`, which
reports what the kernel actually loaded and cannot be spoofed by an argument
string. Verified: 11 raw `pgrep` matches filtered to 9 genuine processes.

### 4. The wizard onboarded accounts the router would never use

`WizardManager.register` set display name, priority, models, and endpoint, but
never set `capabilities`. The router rejects any account whose declared
capabilities contain no member of the `Capability` enum
(`AccountRejectionReason.CAPABILITY_MISMATCH`). So the wizard could walk an
account all the way to `ONLINE`, report success, and the account would then
**silently never receive work** — precisely the failure mode Part 12's
rejection reasons exist to make visible.

Found by the Part 17 walkthrough asserting the end state was *routable* rather
than merely registered. No prior test made that assertion.

**Fix:** added `WizardManager._resolve_capabilities`, called during `register`.
Explicit configuration wins but is filtered against the `Capability` enum;
anything left empty falls back to a provider-appropriate default
(`_DEFAULT_CAPABILITIES_API`, `_DEFAULT_CAPABILITIES_ANTIGRAVITY`,
`_DEFAULT_CAPABILITIES_CLINE`). An API account deliberately does not claim
`terminal-operations`, `cloud-read-only`, or `kubernetes-read-only`: a remote
key cannot run a shell. Also added the missing
`from agents.base.adapter import Capability` import.

### 5. `configure` discarded caller-supplied capabilities

`WizardManager.configure` filters incoming config to a non-secret allow-list,
which omitted `capabilities`. A programmatic caller could therefore pass them
and have them silently dropped.

**Fix:** added `capabilities` to the allow-list. It is non-secret, and it is
validated against the `Capability` enum at register time, so an unrecognised tag
can never reach the registry.

---

## Open finding — needs a decision, not yet changed

**The only direct-API account in the registry is not routable.**
`openai-generic-1` declares `capabilities: ['chat', 'streaming']`. Neither string
is a member of the `Capability` enum, so the router discards both, finds an empty
set, and rejects the account with `CAPABILITY_MISMATCH`. Observed directly:

```
antigravity-account-1   admissible=True   admitted
antigravity-account-2   admissible=True   admitted
antigravity-account-3   admissible=True   admitted
kiro-cli                admissible=True   admitted
cline-account-1..3      admissible=True   admitted
openai-generic-1        admissible=False  CAPABILITY_MISMATCH
```

This is **not** fixed here, for two reasons. The values come from configuration
data read by `providers/registry/bootstrap.py` (around lines 86 and 251), not
from code, so it is not a code defect. And correcting it would make an
OpenAI-backed account a live routing candidate for the first time, which changes
routing behaviour and cost exposure. That is a decision for the operator, not a
silent edit.

Suggested correction when approved: replace `['chat', 'streaming']` with real
`Capability` values — `deep-reasoning`, `code-completion`, `code-review`,
`documentation`, `test-scaffolding` — matching the wizard's
`_DEFAULT_CAPABILITIES_API` default so onboarding paths and seeded config agree.

---

## Part 15 — Non-interference verification

Captured before and after the full gate and diffed.

| Property | Before | After | Verdict |
|---|---|---|---|
| GUI processes | 9 (PIDs 3809…13081) | 9, same PIDs | unchanged |
| Earliest GUI start | Wed Sep 9 10:01:18 2026 | identical | never restarted |
| `~/.gemini` root mtime | `1788782284` | `1788782284` | untouched |
| Agentic_os repo HEADs | 9 repos recorded | identical | unchanged |
| Agentic_os dirty files | 2 (`package-lock.json`, `.kiro/`) | same 2 | pre-existing, not ours |
| Plaintext credentials | — | none found | clean |

Two deliberate design choices protect `~/.gemini`:

* Removal tests perform real filesystem operations **only** under
  `~/.mission-control/cline`. Creating even a temporary subdirectory under
  `~/.gemini` would change that root's mtime and break the guarantee above.
  The `~/.gemini` protections are asserted through path validation instead,
  which resolves paths without requiring them to exist.
* Auth tests point their managers at temporary profile roots, so no real
  profile is created, read, or modified.

The `~/.gemini` mtime was additionally asserted unchanged around each removal
and authentication test run, not only around the full gate.

Test fixtures that look like credentials are synthetic and self-describing
(`sk-live-SHOULDNEVERAPPEAR…`, `sk-live-EVENTSHOULDNEVERLEAK…`). The only
`rzp_live_` occurrences are prefix strings *searched for* by leak assertions.

---

## Implementation state by part

Parts 1–13 were already implemented when this session resumed; Parts 14–18 were
completed here.

| Part | Status | Where |
|---|---|---|
| 1 Audit | done | `docs/PHASE22_ACCOUNT_AUTH_AUDIT.md` |
| 2 Lifecycle | done | `providers/registry/lifecycle.py` (14 states + 4 orthogonal axes) |
| 3 Antigravity auth | done | `agents/antigravity/auth.py` |
| 4 Cline auth | done | `agents/cline/auth.py` |
| 5 API onboarding | done | `providers/api/onboarding.py` |
| 6 Provider Registry UI | done | `/api/providers`, `/api/wizard/providers` |
| 7 Account Manager UI | done | `/api/accounts` |
| 8 Active agent count | done | `_compute_account_metrics`, `/api/accounts/metrics` |
| 9 Add wizard | done | `WizardManager`, `/api/wizard/*` |
| 10 Live event stream | done | `WizardEventStream`, `/api/events/stream` |
| 11 Audit integration | done | `AccountAuditor`, `account_registry._audit_transition` |
| 12 Router admission | done | `SmartRouter.check_account_admissible` |
| 13 Safe removal | done | `providers/registry/account_removal.py` (+ import fix) |
| 14 Test suite | **done** | 15 files, 213 tests |
| 15 Non-interference | **done** | `tests/support/gui_guard.py` + verification above |
| 16 Regression gate | **done** | 780 OK; validate 18/18 |
| 17 Demo verification | **done (automated)** | `tests/integration/test_phase22_demo_verification.py`, 30 tests |
| 18 Final report | **this document** | |

### Part 17 — automated demo verification

Part 17 originally asked for an interactive walkthrough. It was completed
instead as an **automated** verification, by explicit decision: the full
operator walkthrough is driven as real authenticated HTTP against a loopback
server rather than by a human in a browser.

`tests/integration/test_phase22_demo_verification.py` (30 tests) performs:

* the complete 8-step happy path, `start → select-auth → configure →
  authenticate → validate → register → health → complete`, asserting the real
  lifecycle state after each step and `ONLINE` at the end;
* the end state being *usable*, not merely present — the completed account is
  asserted admissible by `SmartRouter.check_account_admissible` and to have
  incremented the granular `online` metric;
* the three UI controls: `Back` returning to the prior step, `Cancel` rolling
  back with no orphaned account, credential, or directory (including after
  registration), and `Retry` clearing a failure so the step can be reissued;
* failure paths: unsupported login method, missing required fields (400),
  duplicate account id (409), unknown session (404), and authenticate with no
  credential landing in `AUTH_FAILED` without half-onboarding;
* Bearer-token enforcement on `start` and on all ten step actions, and that an
  unauthenticated attempt creates no account;
* the live event feed carrying the documented event names in real flow order,
  all sharing one correlation id, with failures visible and no
  `authentication_success` emitted for a failed authentication;
* that no step response, no account record, and no event ever contains the key.

Two constraints make this safe inside the regression gate: validation runs with
`live=False` so no request is ever made to a real provider, and every account id
is a unique throwaway that `tearDown` removes along with its credential. The
audit ledger confirms the cleanup — each demo flow ends in an
`ACCOUNT_REMOVE / SUCCESS` entry with `credential_deleted`, and the registry
holds the same 8 accounts before and after the gate.

**What automated verification does not cover**, stated plainly: browser
rendering, JavaScript execution, CSS layout, and SSE reconnection behaviour in a
real browser. Those need a DOM and are outside an HTTP-level check. Everything
the browser would *ask the server to do* is covered.

---

## Notable behaviours worth knowing

**Response keys must not contain `auth`.** The response-wide secret redactor
blanks any key matching `/auth|token|secret|credential/`. This is why the wizard
payload says `login_methods` and the metrics payload says `signed_in` rather
than `authenticated` — keying them otherwise turns non-secret data into
`***REDACTED***` in the browser. Two tests assert this explicitly, because the
failure is silent and would leave the wizard offering no way to sign in.

**Unknown providers get a generic API-key method.** `auth_methods_for` falls back
to an API-key login for unrecognised provider ids, treating them as
OpenAI-compatible. This is deliberate: refusing unknown ids would make every new
gateway un-onboardable until code shipped a special case. An agent whose CLI is
genuinely absent still reports no methods, so the fallback does not paper over a
missing binary.

**`DEGRADED` accounts remain routable.** Admission accepts `HEALTHY` and
`DEGRADED`; excluding degraded accounts would collapse capacity on a transient
blip. Only `UNHEALTHY` and `UNKNOWN` are refused.

**Removal drains rather than kills.** An account with in-flight concurrency is
left `DISABLED` and not deleted, so live work is never destroyed. The three
`antigravity-account-{1,2,3}` ids are frozen against removal entirely.

**`/api/wizard/providers` is slow.** Cline login methods come from live CLI
capability discovery with 20-second subprocess timeouts, so the provider API
tests take roughly 44 seconds. This is pre-existing endpoint behaviour, not test
overhead.
