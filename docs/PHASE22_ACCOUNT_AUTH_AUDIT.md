# Phase 22 Architecture Audit: Universal Account Onboarding & Authentication

**Document Version:** 1.0.0  
**Audit Date:** 2026-09-08  
**Scope:** Architecture discovery, lifecycle state analysis, authentication mechanisms, credential flow, dashboard endpoints, identified defects, root causes, and proposed implementation design for Phase 22.

---

## 1. Executive Summary

Phase 22 introduces universal account onboarding and authentication management to the Universal AI Mission Control system. The system currently manages multiple isolated accounts across headless agent CLI runtimes (`antigravity`, `kiro`, `cline`), direct native API providers (`openai`, `anthropic`, `gemini`), and local/gateway endpoints (`ollama`, `litellm`).

While the underlying foundation—including `AccountRegistry`, `CredentialManager` with OS Keyring / encrypted fallback, and isolated subprocess runners—is robust and passes 530 automated baseline tests, several structural defects and usability gaps exist:
1. **The "4 Online / Active" Dashboard Metric Disconnect**: The dashboard stat card reports a conflated metric (`4 Active` or `X / 4 Online`) because runtime agent discovery filters against a hardcoded 4-element set, omits direct API providers, and conflates provider totals with account totals.
2. **Stale Task Error Leakage into Account Health**: When the system restarts or reconciles orphaned tasks, the string `"Process terminated or system restarted before task completed"` is attached to task error logs. The dashboard aggregates historical task errors directly into the account health banner, falsely reporting healthy, idle accounts as crashed or unconfigured.
3. **Broken Dashboard Account Creation**: The `POST /api/accounts` endpoint contains fatal syntax and type defects: calling non-existent `store_credential()` on `CredentialManager`, referencing non-existent enum members `AuthenticationType.UNAUTHENTICATED` and `AccountStatus.NOT_CONFIGURED`, and omitting persistence to `config/providers.json`.
4. **Coupled Account States**: Authentication state, process state, health state, task execution state, and availability state are collapsed into a single 10-state `AccountStatus` enum, preventing fine-grained status reporting or recovery.
5. **Missing Onboarding Flows**: Antigravity Google OAuth login, Cline CLI multi-account discovery, and API key validation lack safe, structured onboarding and pre-flight validation workflows.

This audit establishes the empirical baseline and defines the minimal, non-disruptive architecture to resolve these defects while strictly honoring all safety invariants.

---

## 2. Current Account & Provider Lifecycle Architecture

### 2.1 Configuration Subsystem (`config/providers.json` & `providers/registry/config.py`)
The canonical declarative configuration is stored in `config/providers.json` (format version 2.0.0). The registry configuration module `providers/registry/config.py` provides thread-safe, cached access:
- `resolve_config_path()`: Returns `config/providers.json` or respects `BRAIN_PROVIDERS_CONFIG` for test isolation.
- `load_config()`: Cached using `@lru_cache(maxsize=4)`.
- `save_config()`: Atomic file writing (`.tmp` write followed by `os.replace`), invalidating the LRU cache on each mutation.
- Mutators: `add_account_config()`, `remove_account_config()`, `set_account_enabled_config()`.

Currently configured accounts:
- `antigravity`: 3 isolated accounts (`antigravity-account-1`, `antigravity-account-2`, `antigravity-account-3`), each assigned an isolated `app_data_dir` under `~/.gemini`.
- `kiro`: 1 account (`kiro-cli`).
- `cline`: 3 isolated accounts (`cline-account-1`, `cline-account-2`, `cline-account-3`), each using dedicated `--config` and `--data-dir` directories under `~/.mission-control/cline/`.
- `openai`: 1 direct API account (`openai-generic-1`) using `secret://mission-control/openai/generic-1`.

### 2.2 Account Model & Pools (`providers/registry/account_registry.py`)
The `Account` dataclass represents individual accounts:
```python
class AuthenticationType(str, Enum):
    API_KEY = "api_key"
    OAUTH = "oauth"
    SERVICE_ACCOUNT = "service_account"
    ENVIRONMENT = "environment"
    LOCAL = "local"
    CUSTOM = "custom"

class AccountStatus(str, Enum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_ERROR = "AUTH_ERROR"
    OFFLINE = "OFFLINE"
    DISABLED = "DISABLED"
    COOLDOWN = "COOLDOWN"
    CONFIG_ERROR = "CONFIG_ERROR"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    UNKNOWN = "UNKNOWN"
```
Key behaviors:
- `AccountPool`: Manages accounts per provider with `acquire()`/`checkout()`, `release()`, priority-based rotation (`rotate()`), exponential backoff on errors, and automatic cooldown on rate limits (HTTP 429).
- `AccountRegistry`: Global index of accounts and pools. Protects core accounts (`antigravity-account-1`, `antigravity-account-2`, `antigravity-account-3`) from deletion.
- Availability Check (`Account.is_available()`): Fails if account is disabled, in cooldown, or in an error status (`AUTH_ERROR`, `CONFIG_ERROR`, `OFFLINE`, `RATE_LIMITED`, `COOLDOWN`, `QUOTA_EXHAUSTED`).

### 2.3 Provider Abstraction (`providers/registry/provider_registry.py` & `providers/base.py`)
- `Provider`: High-level provider container managing child `AgentAdapter`s.
- `AIProvider`: Abstract base class for execution.
- `AgentProviderBridge`: Bridges an `AgentAdapter` to the `AIProvider` contract.
- Direct API providers implement `AIProvider` directly: `OpenAICompatibleProvider`, `AnthropicProvider`, `GeminiProvider`, `OllamaProvider`, `LiteLLMGatewayProvider`.

---

## 3. Authentication Mechanisms & Credential Flow

### 3.1 Credential Storage & Secret References (`providers/registry/credential_manager.py`)
Mission Control uses an opaque secret URI scheme to eliminate plaintext secrets:
- URI formats: `secret://mission-control/{provider_id}/{account_id}` and `env://{VARIABLE_NAME}`.
- Storage backends:
  1. `KeyringCredentialStore`: Uses Linux `secret-tool` (`service=mission-control`). Strictly segregated from the Antigravity IDE GUI (`service=gemini`).
  2. `EncryptedFileStore`: Fallback store at `~/.config/agentic_brain/credentials.dat` (permissions `0600`). Encryption uses PBKDF2-HMAC-SHA256 (100,000 rounds) key derivation from machine ID and user salt + HMAC-SHA256 authentication tag.
  3. `EnvCredentialStore`: Direct environment variable resolution.
- `SecretRedactor`:
  - Regex patterns scrub API keys: OpenAI (`sk-...`), Google AI (`AIza...`), GitHub (`ghp_...`), Render (`rnd_...`), and Bearer tokens.
  - `NON_SECRET_KEY_ALLOWLIST`: Allows display of safe reference keys (`credential_reference`, `authentication_type`) while ensuring their values remain redacted if secret strings are accidentally assigned.
  - Redacts dictionaries, lists, and raw strings.

### 3.2 Antigravity Authentication Flow & Isolation
- The `agy` CLI uses Google's `codeassistclient`.
- Headless execution requires passing `--app_data_dir=<name>`, resolving to `~/.gemini/<name>`.
- **D-Bus Keyring Shielding**: By passing `DBUS_SESSION_BUS_ADDRESS="/dev/null"` in the subprocess environment, `agy` is prevented from querying or modifying the system GNOME Keyring (`service=gemini`), which belongs exclusively to the running IDE GUI process (PID 5854). Instead, the CLI falls back to per-profile OAuth token storage (`antigravity-oauth-token`).
- Shallow health check: Verifies binary and readable/writable profile directory.
- Deep health check: Runs `agy --app_data_dir=<dir> models` with 90s timeout (cached for 300s).

### 3.3 Cline Authentication Flow & Isolation
- Cline CLI (version `3.0.61`) supports:
  - Global isolation flags: `--config <dir>` and `--data-dir <dir>`.
  - Subcommand `cline auth [options] [provider]` with flags `-p <provider>`, `-k <key>`, `-m <model>`, `-b <baseurl>`, `--config <dir>`, `--data-dir <dir>`.
- Each Cline account (`cline-account-1`, `cline-account-2`, `cline-account-3`) maintains isolated directories under `~/.mission-control/cline/<account>/config` and `data`.
- API keys are resolved via `CredentialManager` and never written to shell history or logs.

### 3.4 Direct API Providers
Native API providers (`providers/api/`) use standard library `urllib.request` with timeouts:
- `OpenAICompatibleProvider`: Health checks via `GET {base_url}/models` with `Authorization: Bearer <key>`.
- `AnthropicProvider`: Health checks via `POST https://api.anthropic.com/v1/messages` with `x-api-key`; HTTP 400 confirms valid key (invalid message schema), while 401/403 confirms unauthorized.
- `GeminiProvider`: Health checks via `GET https://generativelanguage.googleapis.com/v1beta/models?key={api_key}`.

---

## 4. Dashboard Endpoints & Registry Flow

The dashboard (`ui/dashboard/dashboard.py`) is a multi-threaded HTTP server (`ThreadedHTTPServer`) serving an embedded single-page application and JSON REST API on port 8420.

### 4.1 Relevant Existing Endpoints
- `GET /api/status`: Computes system overview, task counts, and agent runtime summary.
- `GET /api/providers`: Returns registered providers with accounts and health summaries.
- `GET /api/accounts`: Returns all registered accounts with masked keys and usage statistics.
- `POST /api/accounts`: Registers a new account (defective in baseline).
- `POST /api/accounts/{id}/health`: Executes an account health check.
- `POST /api/accounts/{id}/enable` & `POST /api/accounts/{id}/disable`: Toggles account availability.
- `DELETE /api/accounts/{id}`: Removes an account from memory and deletes its stored credentials.
- `GET /api/events/stream`: Server-Sent Events (SSE) stream backed by `events/bus.py`.

---

## 5. Identified Defects & Root Causes

### Defect 1: Dashboard Showing Only 4 Active/Online Agents
- **Manifestation**: Overview stat card displays `4 Active` or `X / 4 Online` regardless of the actual 8 registered accounts.
- **Root Cause Analysis**:
  1. In `ui/dashboard/dashboard.py:320-321`:
     ```python
     agent_provider_ids = {"antigravity", "kiro", "cline", "openhands"}
     providers = {k: v for k, v in raw_providers.items() if k in agent_provider_ids or v.get("type") == "agent"}
     ```
     This filters out all non-agent providers. Direct API providers like `openai` are dropped.
  2. In `dashboard.py:468`:
     ```python
     active_workers = [a.agent_id for a in registry.list_active_adapters()]
     ```
     `list_active_adapters()` only queries `Provider.adapters`. Direct API providers implement `AIProvider` and reside in `_ai_providers`, completely bypassing `active_workers`.
  3. In `dashboard.py:2005-2011`, the HTML quick-dispatch dropdown hardcodes 4 options: `antigravity-account-1`, `antigravity-account-2`, `kiro-cli`, and `cline`.
  4. In `dashboard.py:3216`, the provider count renders as `${mc.providers_online} / ${mc.providers_total}`, which evaluates to `X / 4` (since exactly 4 providers exist: antigravity, kiro, cline, openai), creating confusion between providers and agents.

### Defect 2: Stale Task Errors Polluting Account Health
- **Manifestation**: Agent and Account cards display: `"Last error: Process terminated or system restarted before task completed"`.
- **Root Cause Analysis**:
  1. When the dashboard starts or reconciles tasks (`tasks/manager.py:438`), orphaned tasks left in `RUNNING` status receive the error:
     `"Process terminated or system restarted before task completed (recovered by runtime reconciler)"`.
  2. In `dashboard.py:361-370` (`_get_agents_runtime()`), the server traverses all tasks ever assigned to an agent, collects all historical errors into `historical_errors`, and assigns `last_error = historical_errors[0]`.
  3. In the frontend JS (lines 3265, 3279), `a.last_error` is rendered directly beneath the agent's health description as if it were an active failure, misleading users into believing the agent or credentials are broken.

### Defect 3: Broken Account Creation (`POST /api/accounts`)
- **Manifestation**: Attempting to add an account via the dashboard throws an unhandled Python exception (HTTP 500).
- **Root Cause Analysis**:
  1. Method name mismatch: Line 1390 calls `get_credential_manager().store_credential(cred_ref, payload["api_key"])`. `CredentialManager` has method `store()`, not `store_credential()`.
  2. Missing enum members: Line 1402 references `AuthenticationType.UNAUTHENTICATED` (does not exist; enum has `API_KEY`, `OAUTH`, etc.) and `AccountStatus.NOT_CONFIGURED` (does not exist; enum has `ONLINE`, `OFFLINE`, etc.).
  3. Lack of persistence: Line 1409 registers the account in memory but never calls `add_account_config()` in `providers/registry/config.py`. The account disappears upon server restart.
  4. Missing audit logging: No audit record is written to `runtime/audit/audit.jsonl`.

### Defect 4: Coupled State Model
- **Manifestation**: Inability to differentiate between an account that is unauthenticated vs. unreachable vs. executing vs. disabled.
- **Root Cause Analysis**: `AccountStatus` mixes lifecycle, process, authentication, health, and task states into a single flat enumeration.

---

## 6. Proposed Architecture & Implementation Design

### 6.1 Part 2 — Universal Account Lifecycle State Machine
Create `providers/registry/lifecycle.py` defining formal decoupled states:
1. **`AccountLifecycleState` (14 states)**:
   - Progressive: `DISCOVERED`, `CONFIGURING`, `AUTHENTICATING`, `AUTHENTICATED`, `VALIDATING`, `READY`.
   - Operational: `ONLINE`, `OFFLINE`, `DISABLED`.
   - Failure: `AUTH_FAILED`, `AUTH_CANCELLED`, `VALIDATION_FAILED`, `PROVIDER_UNAVAILABLE`, `CONFIG_ERROR`.
2. **Orthogonal State Enums**:
   - `AuthState`: `UNAUTHENTICATED`, `AUTHENTICATING`, `AUTHENTICATED`, `AUTH_FAILED`, `AUTH_CANCELLED`, `EXPIRED`.
   - `HealthState`: `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `UNKNOWN`.
   - `ProcessState`: `IDLE`, `WORKING`, `STOPPED`, `TERMINATED`, `UNKNOWN`.
   - `TaskState`: `IDLE`, `ASSIGNED`, `EXECUTING`, `COMPLETED`, `FAILED`, `CANCELLED`.
3. **State Transition Engine (`AccountLifecycleStateMachine`)**:
   - Explicit allowed transition graph.
   - Validation on every transition raising `InvalidLifecycleTransitionError` on illegal jumps.
   - Decoupled `Account` attributes in `providers/registry/account_registry.py` while maintaining 100% backward compatibility with existing tests referencing `AccountStatus`.
   - Complete isolation of historical task errors from `HealthState`.

### 6.2 Part 3 — Antigravity CLI Authentication Engine
Implement `AntigravityAuthManager` in `agents/antigravity/auth.py` (and expose via `providers/agents/antigravity/`):
- `create_isolated_profile(account_id: str) -> Path`: Sets up `~/.gemini/antigravity-account-<id>` with `0700` permissions.
- `launch_auth(account_id: str, timeout_seconds: int = 120)`: Safely initiates official Google auth targeting the isolated `--app_data_dir`, ensuring `DBUS_SESSION_BUS_ADDRESS="/dev/null"` to protect GNOME Keyring `service=gemini`.
- `check_auth_status(account_id: str) -> bool`: Checks for valid OAuth token file in profile directory.
- `validate_session(account_id: str) -> tuple[bool, str, list[str]]`: Executes `agy --app_data_dir=<dir> models` with D-Bus isolation to prove connectivity without incurring model token usage.
- Integration with `AccountRegistry` and `config/providers.json`.

### 6.3 Part 4 — Cline Multi-Account Authentication Engine
Implement `ClineAuthManager` in `agents/cline/auth.py` (and expose via `providers/agents/cline/`):
- `discover_cli_capabilities() -> dict[str, Any]`: Dynamically executes `cline --version` and `cline auth --help` to extract supported flags (`-p`, `-k`, `-m`, `-b`, `--config`, `--data-dir`).
- `setup_account_isolation(account_id: str) -> tuple[Path, Path]`: Prepares isolated `config` and `data` directories under `~/.mission-control/cline/<account_id>/`.
- `configure_credentials(account_id: str, provider: str, api_key: str, model_id: str | None = None, base_url: str | None = None) -> bool`:
  - Stores secret directly in `CredentialManager` under `secret://mission-control/cline/{account_id}/api_key`.
  - Executes `cline auth -p <provider> -k <key> ... --config <dir> --data-dir <dir>`.
  - Zero plaintext keys stored in JSON or logs.

### 6.4 Part 5 — Generic API Provider Onboarding Framework
Implement `APIProviderOnboarder` in `providers/api/onboarding.py`:
- Supported providers: OpenAI, Anthropic, Google Gemini API, and generic OpenAI-compatible gateways.
- `ValidationStatus` Enum: `CONNECTED`, `UNAUTHORIZED`, `RATE_LIMITED`, `NETWORK_ERROR`, `INVALID_CONFIGURATION`, `UNAVAILABLE`.
- `ValidationReport` Dataclass:
  - `status: ValidationStatus`
  - `provider_id: str`
  - `models_discovered: list[str]`
  - `latency_ms: float`
  - `error_message: str | None`
- Lightweight network ping validating endpoints and API keys with short timeouts (5–10s) before persisting to `config/providers.json`.

---

## 7. Non-Interference Verification Baseline

All Phase 22 implementations must preserve these non-interference baselines:
1. **Running Antigravity IDE GUI**:
   - Main Process: PID `5854`, start time `Tue Sep 8 12:11:16 2026`.
   - Keyring service: `service=gemini` must remain untouched.
   - Profile directory: `~/.gemini/antigravity-ide` must never be modified by CLI accounts.
2. **Workspace Polyrepo**:
   - Directory `~/YashDevops/Agentic_os` must have zero files created, modified, or deleted.
3. **Automated Baseline**:
   - 429 unit tests + 101 integration tests = 530 tests must continue passing without regression.
   - Full-system gate `python3 scripts/brain.py validate --full` must continue passing 18/18 checks.
4. **Secret Security**:
   - Zero plaintext secrets in `config/providers.json`, runtime logs, audit events, or API responses.

