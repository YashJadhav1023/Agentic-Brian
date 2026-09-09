# Phase 12 — Mission Control Dashboard UI

## 1. Overview & Architecture

Phase 12 delivers the local web-based Universal AI Mission Control dashboard for orchestrating providers, accounts, models, jobs, and analytics across all integrated environments.

Following the core architecture constraints of the project, the dashboard is implemented without external web framework dependencies (no FastAPI or Flask required), extending the standard library `http.server` implementation in `ui/dashboard/dashboard.py` with:
- Strict local loopback binding (`127.0.0.1:8765`)
- Bearer token authentication via `X-Mission-Control-Token` / `Authorization: Bearer <token>`
- Safe API response sanitization via `SecretRedactor` (preventing token leakage)
- Modern, responsive, dark-mode styling with vanilla CSS and rich glassmorphism aesthetics

```
+-----------------------------------------------------------------------+
|                     Mission Control Web UI (Vanilla JS/CSS)           |
+-----------------------------------------------------------------------+
|  [Overview]  [Providers]  [Accounts]  [Models]  [Jobs]  [Usage]       |
+-----------------------------------------------------------------------+
                                  |
                                  v HTTP / JSON API (Bearer Auth)
+-----------------------------------------------------------------------+
|                 ui/dashboard/dashboard.py (http.server)               |
+-----------------------------------------------------------------------+
       |                  |                 |                 |
       v                  v                 v                 v
+--------------+  +---------------+  +--------------+  +---------------+
|   Provider   |  |    Account    |  |    Model     |  |  SmartRouter  |
|   Registry   |  |   Registry    |  |   Registry   |  | & JobManager  |
+--------------+  +---------------+  +--------------+  +---------------+
       |                  |                 |                 |
       +------------------+-----------------+-----------------+
                                  |
                                  v
+-----------------------------------------------------------------------+
|                     Analytics & Quota Engine                          |
|         (UsageTracker, CostTracker, QuotaManager, Analytics)          |
+-----------------------------------------------------------------------+
```

---

## 2. Dashboard Modules & Capabilities

### 2.1 Overview Panel
The summary view provides immediate visibility into cluster-wide status:
- **System Status**: Global operational health and active configuration version.
- **Provider Status**: Total configured vs online/healthy providers (Agent, IDE, API, Local).
- **Account Status**: Total registered accounts, healthy vs degraded/cooldown accounts.
- **Model Catalogue**: Total models discovered across active providers and workers.
- **Job Execution**: Active running jobs, completed jobs, failed jobs, and retried executions.
- **Usage & Economics**: Rolling 24h token throughput and estimated cost tracking.
- **Recent Routing Decisions**: Real-time log of multi-factor routing outputs with candidate score summaries.

### 2.2 Provider Management
Supports lifecycle operations for all universal provider categories:
- **Categories**: `AGENT` (Antigravity, Cline, Kiro), `IDE`, `API` (OpenAI, Anthropic, Gemini), `LOCAL_MODEL` (Ollama), `GATEWAY` (LiteLLM proxy).
- **Operations**:
  - Provider enumeration (`GET /api/providers`)
  - Provider registration (`POST /api/providers`)
  - Enable / Disable toggling (`PATCH /api/providers/{id}`)
  - Live health checks (`POST /api/providers/{id}/health`)
  - Dynamic model discovery (`POST /api/providers/{id}/discover`)
- **Metadata displayed**: Provider ID, name, category, health status, failure rate, account count, model count, and declared capabilities.

### 2.3 Account Management & Credential Security
- **Multi-Account View**: Inspects accounts per provider with status (healthy, cooldown, offline), priority, success/failure counts, and token usage.
- **Lifecycle Operations**:
  - Add account modal (`POST /api/accounts`)
  - Enable / Disable account (`PATCH /api/accounts/{id}`)
  - Remove account (`DELETE /api/accounts/{id}`) with protection for frozen Phase 8 Antigravity accounts
  - Account health check trigger (`POST /api/accounts/{id}/health`)
- **Credential Storage**:
  - Plaintext API keys submitted through UI modals are immediately delegated to `CredentialManager` and stored in OS keyring (`service=mission-control`) or encrypted store (`0600`).
  - Configuration references store `secret://mission-control/<provider>/<account>`.
  - Keys returned in API and UI payloads are strictly masked (e.g., `************abcd`), never exposing the plaintext secret.

### 2.4 Model Management
- Inspects models registered across all providers.
- Displays context window limits, speed rating, cost metrics (input/output per 1M tokens), health status, and capability tags (`coding`, `reasoning`, `planning`, `debugging`, `long_context`, `vision`, `tool_use`, `fast`, `cheap`).
- Supports priority overrides, capability tag modification, and enabling/disabling models.

### 2.5 Job Submission & Smart Routing
- **Submission Modal**:
  - Task description / prompt
  - Provider selector (`auto` or specific provider)
  - Account selector (`auto` or specific account)
  - Model selector (`auto` or specific model)
  - Routing mode: `balanced`, `performance`, `cost`, `reliability`, `manual`
  - Execution options: Timeout (s), Priority (1-10), Streaming required, Failover enabled
- **Routing Explanation UI**:
  - For `auto` routing, the UI renders the decision explanation breakdown: selected provider, selected account, winning candidate score, and multi-factor breakdown (e.g. `capability_match +30`, `provider_health +20`, `latency +15`).

### 2.6 Live Job Monitoring
- Tracks jobs across states: `queued`, `running`, `completed`, `failed`, `retrying`, `failover`.
- Displays real-time details: Job ID, task summary, assigned provider/account/model, duration, token usage, retry count, failover history, and error logs.

---

## 3. Security & Safety Compliance

1. **GUI Antigravity Session Isolation**:
   - The UI runs completely independent of the running Antigravity IDE (PID `4904`).
   - Does not touch `~/.gemini/antigravity-account-*` or GNOME keyring `service=gemini`.
2. **Secret Redaction**:
   - Outgoing JSON responses pass through `SecretRedactor` which scrubs patterns matching API keys, OAuth tokens, and bearer credentials.
   - The authentication token generation endpoint (`/api/token`) safely provides the session token without applying destructive redaction to itself.
3. **No Unnecessary Dependencies**:
   - Operates on standard library `http.server`, `urllib.request`, and `json`.
