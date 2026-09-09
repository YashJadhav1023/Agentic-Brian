# Phase 11 Audit: Production Hardening + Kiro/Cline Integration

## 1. What Already Exists and Works
- **Universal Provider Abstraction (`AIProvider`)**: Well-defined base class contract in `providers/base.py` for handling execution, capabilities, models, and health checks across diverse AI backends.
- **Agent Adapters**: `AgentAdapter` bridge wrapping CLI agents (Antigravity, Kiro, Cline, OpenHands) into the universal provider interface.
- **Account Registry & Pooling**: `AccountRegistry` and `AccountPool` (`providers/registry/account_registry.py`) track first-class accounts (with enabled status, health, priority, and concurrency limits). It supports account rotation, rate limit handling, and failure tracking.
- **Credential Security**: `CredentialManager` stores secrets securely in OS Keyring (`service=mission-control`) or an encrypted file (`0600` PBKDF2 machine-salted), ensuring zero plain-text leaks in `providers.json`. `SecretRedactor` is active.
- **Smart Routing & Fallbacks**: The `SmartRouter` uses `capability_match` and model complexity to select appropriate accounts. Fallback chaining is explicitly configured in `JobManager`.
- **CLI Commands**: `brain.py` implements commands for `accounts` (add, list, inspect, enable/disable, health, rotate), `providers` (list, add, health), and `job` (submit, list, inspect).

## 2. What is Missing
- **Kiro Multi-Account Support**: Kiro is currently a single-account adapter boundary (`kiro-cli`). Profile isolation needs verification to safely implement `kiro-account-1..3`.
- **Cline Multi-Account Support**: Cline is integrated as a single, read-only configuration observer. While the CLI supports `--config` and `--data-dir`, multi-account adapter isolation is pending.
- **Provider Discovery**: A `brain providers discover` command is needed to dynamically scan the system for installed tools (Antigravity, Kiro, Cline, Ollama) and report their status securely.
- **Extended Smart Routing**: The smart router needs to integrate rate limits, cooldowns, latency, health, and user preferences into routing decisions, preserving the `capability_match` score.
- **Tests**: Comprehensive testing for all additions (Kiro/Cline adapter, account isolation, universal account/api management, provider discovery, failover, routing, and safety/security guarantees).

## 3. Kiro Installation & Status
- **Installation**: Executable found at `~/.local/bin/kiro-cli`.
- **IDE Process**: Primarily a headless terminal CLI agent.
- **Authentication/Session**: The authentication architecture isn't fully exposed via public source. Currently, the adapter uses the global system state.
- **Isolation Viability**: Needs deep `--help-all` inspection. If `--app_data_dir` or equivalent does not exist to isolate configurations safely, multi-account isolation will be aborted for Kiro to prevent credential bleed or global logout.

## 4. Cline Installation & Status
- **Installation**: Executable found at `~/.local/bin/cline`.
- **IDE Process**: Runs as a CLI.
- **Authentication/Session**: Stores credentials typically in VSCode's SecretStorage (`keytar` backend).
- **Isolation Viability**: The CLI explicitly exposes `--config <path>` (Configuration directory, default `~/.cline`) and `--data-dir <path>` (Use isolated local state, default `~/.cline/data`). This indicates that **safe profile isolation is possible** for concurrent Cline accounts.

## 5. Security & Isolation Risks
- **GUI Safety**: The active Antigravity GUI and `service=gemini` keyring must not be touched. Using `SecretManager` correctly ensures this.
- **Account Bleed**: Running concurrent agents (e.g., Kiro) on the same underlying profile risks session corruption, race conditions, and unauthorized rate-limit consumption. 
- **Secret Leaks**: CLI additions (`brain accounts add`, `brain providers discover`) must strictly avoid echoing secrets to stdout or writing them to standard logs.

## 6. Proposed Implementation Plan

**Part 1 - Kiro**: 
1. Determine if isolation is possible via environment variables (e.g., `KIRO_HOME`) or CLI flags.
2. If safe, create `providers/ide/kiro/` with KiroAdapter, KiroAccount, KiroHealthCheck supporting multiple accounts.
3. If unsafe, document "Multi-account isolation unavailable" and retain the single-account wrapper.

**Part 2 - Cline**:
1. Implement isolated accounts via `providers/ide/cline/` and `adapters/cline/` using `--config` and `--data-dir` arguments.
2. Ensure accounts run in strict isolation.

**Part 3 & 4 - Universal Account & API Account Addition**:
1. Extend `scripts/brain.py` to seamlessly handle new providers (`openai-compatible`, `anthropic`, `gemini`, etc.) via the secure `CredentialManager`.

**Part 5 & 6 - Provider Discovery & Health System**:
1. Implement `brain providers discover` via subprocess checks and config inspection.
2. Extend `health` commands for granular states (`ONLINE`, `AUTH_REQUIRED`, `RATE_LIMITED`, etc.).

**Part 7 & 8 - Smart Routing & Failover**:
1. Enhance `SmartRouter` in `brain/router/smart_router.py` to include cooldown and health tracking, maintaining `capability_match`.
2. Enhance `JobManager` failover logic to prevent cross-contamination.

**Part 10 & 11 - Testing & Verification**:
1. Develop extensive unit and integration tests for all components.
2. Execute live verification checks ensuring the Antigravity IDE and Phase 8 profiles remain untouched.

**Part 12 - Documentation**:
1. Produce final reports: `PHASE11.md`, `KIRO_INTEGRATION.md`, `CLINE_INTEGRATION.md`, `API_ACCOUNT_MANAGEMENT.md`, and `ACCOUNT_ISOLATION_SECURITY.md`.
