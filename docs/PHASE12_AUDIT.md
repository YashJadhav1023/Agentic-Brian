# Phase 12 Audit: Universal Provider Control Plane

## Existing Architecture
Based on the review of the current implementation (Phase 9 & 11 docs, plus current repo state):
- **Universal Provider Abstraction (`AIProvider`)**: Exists in `providers/base.py`, defining capabilities, health, models, and execution contracts for AGENT, IDE, API, LOCAL_MODEL, and GATEWAY.
- **Agent Providers**: Antigravity, Kiro, Cline, and OpenHands are bridged via `AgentAdapter` / `AgentProviderBridge`.
- **Account Abstraction**: `AccountRegistry` manages accounts with zero plaintext secrets (using `secret://` references).
- **Credential Architecture**: `CredentialManager` interacts with the `secret-tool` OS Keyring under `service=mission-control` and an encrypted local store.
- **Routing**: `SmartRouter` uses `capability_match`, and `JobManager` handles explicit failover.
- **Dashboard**: A local daemon running on `http.server` handles UI.

## Current Limitations & Provider Constraints
- **Cline Isolation**: Phase 11 implemented isolation via `--config` and `--data-dir` for multiple accounts under `~/.mission-control/cline/account-*`.
- **Kiro Limitations**: Verified in Phase 11 as lacking a mechanism for isolated application-data directories. It must remain single-account to prevent false multi-account configuration or session bleed.
- **Antigravity Safety**: Active GUI runs on PID 5854 (start time 12:11). Keyring uses `service=gemini`. `CredentialManager` correctly isolates secrets under `service=mission-control`.
- **Missing Functionality for Phase 12**:
  - `brain accounts add` missing robust CLI flow for adding varied credentials.
  - Granular API execution (streaming, timeout, retry, usage extraction) for OpenAI-compatible providers, Anthropic, Gemini.
  - Native `Ollama` daemon discovery and fallback mechanism.
  - UI extensions for dynamic provider addition, testing, and secret redaction.

## Proposed Implementation
- Enhance `brain.py` CLI for `accounts add`, `providers discover`, `accounts inspect/test`.
- Create `providers/api/openai_compatible.py`, `anthropic_native.py`, `gemini_native.py` bridging standard HTTP directly.
- Create `providers/api/ollama_local.py` for `LOCAL_MODEL`.
- Extend `SmartRouter` with `fastest`, `cheapest`, `quality`, and `round-robin` policies.
- Extend `Dashboard` in `ui/dashboard/dashboard.py` to support real-time provider lists and account addition logic.

## Safety Check
- **Risk Assessment**: ZERO risk to the active Antigravity session if `CredentialManager` boundaries remain strictly enforced and tests run without mutating `~/.gemini/antigravity-account-*`.
- **Proceed**: YES, the audit clears the way for Phase 12 implementation.
