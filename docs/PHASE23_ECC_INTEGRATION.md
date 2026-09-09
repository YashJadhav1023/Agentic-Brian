# Phase 23: Universal Capability Federation & ECC Integration

**Status:** Completed & Validated  
**Version:** 1.0.0  
**Date:** 2026-09-09  
**Security Boundary:** Localhost / Loopback (`127.0.0.1`, `localhost`, `::1`) only. Zero public exposure.  
**Dependency Policy:** Pure Python stdlib (`3.14.7`). Zero third-party packages installed.

---

## 1. Executive Summary

Phase 23 establishes the **Universal Capability Federation Layer** within Mission Control, securely integrating the mirrored **Everything-Claude-Code (ECC)** repository (681 components) into the core resource model, smart router, steering registry, and mission orchestration fabric.

Crucially, federation adheres to strict governance principles:
1. **Zero Auto-Activation:** All federated capabilities default to `DISCOVERED` lifecycle state and require explicit operator enablement.
2. **Strict Category D Rejection:** Untrusted shell scripts, raw hooks, and cloud-sync MCPs are cataloged as `UNTRUSTED` and permanently locked in `DISABLED` state.
3. **Unbreakable Rule Precedence:** Existing repository and safety rules always override imported external guidelines.
4. **Instant Kill-Switch:** A global emergency kill-switch (`brain external disable ecc`) disables all 681 capabilities atomically.
5. **Zero Raw Hook Execution:** External scripts are stripped of executable bits (`chmod 0644`) and cannot bypass approval gates.

---

## 2. Architecture Overview

```
                          ┌────────────────────────┐
                          │   external/ecc/        │
                          │   (chmod 0644 mirrors) │
                          └───────────┬────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │  ECCComponentNormalizer   │
                        │  (SHA-256 Provenance)     │
                        └─────────────┬─────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         │                            │                            │
         ▼ (Category A/B/C)           ▼ (Category D)               ▼ (Category A Rules)
┌───────────────────┐        ┌──────────────────┐        ┌────────────────────┐
│  ResourceRegistry │        │ STRICT REJECTION │        │  SteeringRegistry  │
│  State: DISCOVERED│        │ Health: REJECTED │        │  Priority: 15      │
│  Trust: EXTERNAL  │        │ Trust: UNTRUSTED │        │  (Low Precedence)  │
└─────────┬─────────┘        └──────────────────┘        └────────────────────┘
          │
          ▼
┌────────────────────────────────────────────────────────┐
│               ECCFederationManager                     │
│  • Lifecycle Control (enable / disable / disable_all)  │
│  • Persistence: runtime/external_capabilities.json     │
│  • Emergency Kill-Switch                               │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                   SmartRouter                          │
│  • explain_routing() shows matched enabled ECC skills  │
│  • Excludes DISCOVERED and DISABLED resources          │
│  • Provider / Account isolation preserved              │
└────────────────────────────────────────────────────────┘
```

---

## 3. Capability Classification Matrix

The 681 mirrored ECC components are classified into four risk tiers:

| Tier | Category | Count | Trust Level | Default Lifecycle | Governance Policy |
|:---|:---|:---:|:---|:---|:---|
| **A** | Core Capabilities | 403 | `TrustLevel.EXTERNAL` | `DISCOVERED` | Skills, prompts, instructions, agent definitions. Eligible for manual enablement. |
| **B** | Community Integrations | 161 | `TrustLevel.EXTERNAL` | `DISCOVERED` | Safe community tools, doc generators. Eligible for manual enablement. |
| **C** | Informational Guides | 82 | `TrustLevel.EXTERNAL` | `DISCOVERED` | Reference architecture, read-only guidance. Indexed into KnowledgeIndex. |
| **D** | Untrusted / High Risk | 35 | `TrustLevel.UNTRUSTED` | `DISABLED` | Shell scripts, raw lifecycle hooks, cloud sync MCPs. **Permanently blocked from enablement.** |

### Provenance Hashing
Every component is hashed using SHA-256 over its full file contents. Any modification or drift invalidates the component record and flags it for re-audit.

---

## 4. Rule Precedence Hierarchy

Imported ECC steering and guideline files must never override established mission standards. The steering engine enforces the following absolute priority hierarchy:

```
  100  ──  Safety & Governance Rules (Non-negotiable safety constraints)
   60  ──  Project Steering Directives (AGENTS.md, mission guidelines)
   50  ──  Mission Architecture Guidelines (ARCHITECTURE.md, system design)
   40  ──  Existing Repository Rules (Tool settings, linters, local configs)
   15  ──  ECC Imported Rules (Advisory recommendations only)
```

If an ECC rule contradicts any repository rule or safety invariant, the conflict resolver flags the collision and enforces the higher-priority local rule.

---

## 5. Lifecycle Management & Emergency Kill-Switch

Federated capabilities follow a deterministic state machine:

```
[ Unconfigured ] ──(federate)──> [ DISCOVERED ] ──(operator enable)──> [ ENABLED ]
                                         │                                  │
                                         │                                  │
                                         ▼                                  ▼
[ Category D ] ────(lock)──────> [ DISABLED / REJECTED ] <──(kill-switch)───┘
```

### CLI Operations
The `brain external` command group provides operator control:

```bash
# List all federated capabilities with category and status
python3 scripts/brain.py external list

# List only enabled skills
python3 scripts/brain.py external list --type skill --state ENABLED

# Enable a specific approved capability
python3 scripts/brain.py external enable ecc:skill:react-performance

# Disable a specific capability
python3 scripts/brain.py external disable ecc:skill:react-performance

# EMERGENCY KILL-SWITCH: Disable all 681 ECC capabilities immediately
python3 scripts/brain.py external disable ecc

# Check overall federation health and status
python3 scripts/brain.py external status
```

---

## 6. Smart Router Explainability

When an ECC capability is enabled, `SmartRouter.explain_routing(task)` detects keyword and semantic affinity, surfacing the capability as an advisory recommendation:

```json
{
  "task": "Profile and optimize React virtual DOM re-renders",
  "selected_agent": "antigravity-account-2",
  "selected_account": "account-2",
  "recommended_ecc_skills": [
    "ecc:skill:react-performance"
  ],
  "ecc_relevance_rationale": "Matched 1 enabled federated ECC skill(s)"
}
```

If the capability is in `DISCOVERED` or `DISABLED` state, it is completely omitted from the recommendation set.

---

## 7. Security Hardening & Safety Verification

1. **Permissions Stripped:** All mirrored files in `external/ecc/` have permissions set to `0644` (`chmod -R 0644 external/ecc/`). No file is executable.
2. **Approval Gate Enforcement:** Destructive commands (`rm -rf`, purge, drop table) triggered in mission dispatch are stamped with `requires_approval=True` and blocked until explicit operator release.
3. **No Third-Party Dependencies:** Zero `pip` packages installed. Pure Python stdlib (`3.14.7`).
4. **Loopback Only:** Dashboard and API bindings strictly enforce `127.0.0.1` / `localhost` / `::1`.
