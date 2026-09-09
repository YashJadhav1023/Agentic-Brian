# Universal AI Mission Control Architecture
**Phase 9: Multi-IDE + Multi-Account + Multi-API Provider Orchestration**

---

## 1. Architectural Overview

Mission Control serves as the universal orchestration plane for heterogeneous AI providers across two distinct categories:

```
                    MISSION CONTROL
                           │
             ┌─────────────┴─────────────┐
             │                           │
       AGENT PROVIDERS              API PROVIDERS
             │                           │
     ┌───────┼────────┐        ┌─────────┼─────────┐
     │       │        │        │         │         │
     ▼       ▼        ▼        ▼         ▼         ▼
  Antigravity Kiro  Cline   OpenAI    Gemini   Anthropic
  (A1,A2,A3) (CLI)  (CLI)  (Direct)   (REST)   (Messages)
             │                 │         │         │
         OpenHands        OpenRouter  Bedrock    Azure
                               │
                        LiteLLM Gateway
                               │
                        Ollama / Local
```

---

## 2. Core Abstractions

### 2.1 Universal Provider Abstraction (`AIProvider`)
File: [`providers/base.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/providers/base.py)

All providers implement the `AIProvider` contract:
- `provider_id`: Unique identifier (e.g. `openai`, `gemini-api`, `antigravity`).
- `provider_type`: Enum (`AGENT`, `IDE`, `API`, `LOCAL_MODEL`, `GATEWAY`).
- `capabilities()`: Declared set of `Capability` flags.
- `health()`: Connectivity and credential verification returning `(healthy: bool, reason: str)`.
- `list_models()`: Catalog of `ModelMetadata`.
- `execute(job: Job)`: Synchronous execution returning `TaskExecutionResult`.
- `execute_stream(job: Job, on_event)`: Streaming execution with SSE chunk parsing.
- `validate_config()`: Structural parameter checks without network I/O.
- `get_usage()` & `get_limits()`: Usage statistics and quota tracking.

Legacy agents (Antigravity, Kiro, Cline) are bridged via `AgentProviderBridge` (`providers/adapters/bridge.py`).

### 2.2 First-Class Account Abstraction & Pools
File: [`providers/registry/account_registry.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/providers/registry/account_registry.py)

Each provider supports multiple isolated accounts:
- **`Account`**: Represents an isolated identity with `id`, `provider_id`, `account_name`, `authentication_type`, `credential_reference`, `status`, `enabled`, and concurrency limits.
- **`AccountPool`**: Manages account availability, active concurrency checkout/release, exponential cooldown backoff upon failure, and rate-limit tracking.
- **Zero-Secret Guarantee**: Secrets are NEVER stored on the `Account` object or in `providers.json`. Only uniform URI references (e.g. `secret://mission-control/openai/personal`) are retained.

### 2.3 Secure Credential Manager
File: [`providers/registry/credential_manager.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/providers/registry/credential_manager.py)

- **OS Keyring Store**: Uses `/usr/bin/secret-tool` under dedicated namespace `service="mission-control"`. This completely avoids touching or conflicting with `service="gemini"` (Antigravity GUI).
- **Encrypted File Store**: Fallback for headless environments; stores AES-encrypted credentials at `~/.config/agentic_brain/credentials.dat` with mode `0600`, keyed via PBKDF2 with a machine-specific salt.
- **Environment Store**: Resolves `env://VARIABLE_NAME` references dynamically.
- **`SecretRedactor`**: Automatic redaction of API keys, bearer tokens, and credentials from logs, terminal outputs, and audit events.

### 2.4 Model Registry
File: [`providers/registry/model_registry.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/providers/registry/model_registry.py)

Maintains an indexed catalog of models across all providers with context window lengths, capability flags, and cost estimates.

### 2.5 Unified Job Model & Deterministic Execution
Files: [`brain/orchestrator/job.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/orchestrator/job.py), [`brain/orchestrator/job_manager.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/orchestrator/job_manager.py)

- **`Job`**: Unified dataclass tracking `id`, `provider`, `provider_type`, `account`, `worker`, `model`, `task`, `status`, `created_at`, `duration`, `retry_count`, `result`, and `error`.
- **Deterministic Routing**: Phase 9 routing is strictly deterministic via explicit arguments (`--provider`, `--account`, `--model` or `--worker`).
- **Explicit Failover Chains**: Configured fallbacks (e.g. Primary: OpenAI account-1 -> Fallback: OpenAI account-2 -> Fallback: Gemini account-1) are tried sequentially upon rate limit or failure. Mission Control NEVER silently switches to an unconfigured account.

---

## 3. Command Line Interface (CLI)

### 3.1 Account Management
```bash
# List all accounts across all providers (safe metadata only)
python3 scripts/brain.py accounts list

# Inspect an account in detail
python3 scripts/brain.py accounts inspect antigravity-account-1

# Add a new account (secure password prompt or argument)
python3 scripts/brain.py accounts add \
  --provider openai \
  --name personal \
  --auth-type "API Key" \
  --secret "sk-..." \
  --models "gpt-4o,gpt-4o-mini"

# Enable or disable an account
python3 scripts/brain.py accounts disable openai-personal
python3 scripts/brain.py accounts enable openai-personal

# Check health of accounts
python3 scripts/brain.py accounts health

# Remove an account
python3 scripts/brain.py accounts remove openai-personal --confirm
```

### 3.2 Provider Management
```bash
# List all registered providers (Agent, IDE, API, Gateway)
python3 scripts/brain.py providers list

# Add an arbitrary OpenAI-compatible provider
python3 scripts/brain.py providers add \
  --name my-gateway \
  --type "OpenAI-compatible" \
  --base-url "https://api.mygateway.com/v1" \
  --auth "API Key" \
  --secret "my-secret-key" \
  --models "llama-3-70b,mistral-large"

# Check provider health
python3 scripts/brain.py providers health
```

### 3.3 Job Execution
```bash
# Submit a deterministic job to an Agent worker
python3 scripts/brain.py job submit \
  --provider antigravity \
  --worker antigravity-account-2 \
  --task "Refactor login view"

# Submit a deterministic job to an API provider with failover
python3 scripts/brain.py job submit \
  --provider openai \
  --account personal \
  --model gpt-4o \
  --task "Analyze code complexity" \
  --failover "openai:backup,gemini:main"

# List and inspect tracked jobs
python3 scripts/brain.py job list
python3 scripts/brain.py job inspect <job-id>
```

---

## 4. Safety & Coexistence Guarantees

1. **Antigravity GUI Protection:**
   - The active Antigravity IDE GUI (`/opt/antigravity-ide/antigravity-ide`) was continuously monitored and remained fully operational.
   - Dedicated profile roots (`antigravity-account-jadhav`, `antigravity-account-3`, `antigravity-account-yash`) isolate headless workers from the GUI session.
2. **Keyring Isolation:**
   - Existing GNOME Keyring credentials for Antigravity (`service=gemini`) were left completely untouched.
   - Mission Control stores API credentials strictly under `service=mission-control`.
3. **Zero Plaintext Secrets:**
   - `config/providers.json` stores only metadata and safe `secret://` or `env://` URI references.
   - Secrets are masked from stdout, stderr, logs, and tracebacks by `SecretRedactor`.
