# Usage, Cost, Quotas & Security Lifecycles

## 1. Executive Summary

Universal AI Mission Control enforces strict multi-dimensional usage tracking, cost accounting, quota governance, and OS-level credential protection across all agentic and API workloads.

---

## 2. Core Architecture & Lifecycles

### 2.1 Credential Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor Operator as Operator / UI Modal
    participant CM as CredentialManager
    participant OS as OS Keyring (service=mission-control)
    participant Enc as Encrypted File Store (0600)
    participant Config as config/providers.json
    participant Provider as Provider Execution Layer

    Operator->>CM: Store Credential(provider, account, api_key)
    alt secret-tool Available
        CM->>OS: Store secret in system keyring
    else Fallback Storage
        CM->>Enc: Store PBKDF2 encrypted secret
    end
    CM-->>Config: Save reference "secret://mission-control/<provider>/<account>"
    Note over Config: Plaintext secret is NEVER stored in config
    Provider->>CM: Resolve Credential("secret://...")
    CM->>OS: Retrieve secret
    OS-->>CM: Secret Value
    CM-->>Provider: Decrypted Secret for in-memory request execution
    Note over Provider: Scrubber cleans secret from all logs and traces
```

### 2.2 Usage Lifecycle

```mermaid
flowchart TD
    Complete[Job Completes] --> Parse[Extract Token Usage & Latency]
    Parse --> RecordEvent[UsageTracker.record_event()]
    RecordEvent --> UpdateRolling[Update Daily & Monthly Aggregates]
    UpdateRolling --> ProviderDim[Increment Provider Usage]
    UpdateRolling --> AccountDim[Increment Account Usage]
    UpdateRolling --> ModelDim[Increment Model Usage]
    UpdateRolling --> CostCalc[CostTracker: Calculate USD Cost]
    CostCalc --> QuotaEval[QuotaManager: Evaluate Quota Thresholds]
    QuotaEval --> Persist[Persist Event to Partitioned Storage]
```

### 2.3 Quota Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Normal: Quota < 50%
    Normal --> NominalNotice: Quota >= 50%
    NominalNotice --> WarningState: Quota >= 75%
    WarningState --> CriticalThrottled: Quota >= 90% (Routing Penalized)
    CriticalThrottled --> Suspended: Quota >= 100% (Excluded from Candidates)
    Suspended --> Normal: Quota Reset (Cron / CLI reset)
    CriticalThrottled --> Normal: Quota Reset
    WarningState --> Normal: Quota Reset
```

### 2.4 Data Storage & Retention Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Ingest: New usage / routing event recorded
    Ingest --> ActiveStore: Stored in runtime/analytics/YYYY-MM-DD.json
    ActiveStore --> RetentionCheck: Nightly / Periodic Retention Evaluation
    RetentionCheck --> Retained: Age <= 30 Days
    RetentionCheck --> Purged: Age > 30 Days (Automatic Cleanup)
    Purged --> [*]: Deleted from Disk
```

---

## 3. Data Integrity & Redaction Policy

1. **Strict Key Masking**: Any API endpoint or CLI command querying account information exposes at most the last 4 characters of an API key (e.g. `************abcd`).
2. **Deterministic Pricing**: If pricing per 1M tokens is not verified, it is marked as `unknown`, preventing false budget accounting.
3. **Loopback Isolation**: Web dashboard and API run strictly on loopback interface with mandatory bearer authentication.
