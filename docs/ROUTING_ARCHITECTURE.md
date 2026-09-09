# Routing Architecture & Orchestration Lifecycle

## 1. Executive Summary

The Universal AI Mission Control Routing Engine provides deterministic, multi-factor dispatching across heterogeneous AI runtimes (Antigravity workers, Cline coding agent, Kiro CLI, OpenAI/Anthropic/Gemini APIs, and local Ollama models).

---

## 2. Core Architecture & Lifecycles

### 2.1 Provider Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Discovered: config/providers.json or Discovery CLI
    Discovered --> Registered: ProviderRegistry.register()
    Registered --> HealthCheck: Periodic/On-demand Health Ping
    HealthCheck --> Healthy: Ping OK (HTTP 200 / CLI exists)
    HealthCheck --> Degraded: Latency spike / partial error
    HealthCheck --> Offline: Connection refused / missing CLI
    Healthy --> ModelDiscovery: Query models (live tags or config)
    Degraded --> HealthCheck: Re-check
    Offline --> HealthCheck: Re-check
    Healthy --> [*]: Disabled by Operator
```

### 2.2 Account Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Registered: Account added (with secret:// reference)
    Registered --> Active: Health Verified
    Active --> Cooldown: 429 Rate Limit / Transient 5xx
    Cooldown --> Active: Cooldown Timer Expired (e.g. 60s)
    Active --> Degraded: 90% Quota or High Error Rate
    Active --> Suspended: 100% Quota Exhausted
    Suspended --> Active: Quota Reset (Daily/Monthly)
    Active --> Disabled: Operator Action
    Disabled --> [*]: Removed (Phase 8 Antigravity Protected)
```

### 2.3 Routing Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client / Dashboard
    participant Router as SmartRouter
    participant Classifier as TaskClassifier
    participant Pool as AccountPool & QuotaManager
    participant Candidate as Candidate Scoring Engine
    participant Exec as Execution Engine (JobManager)

    Client->>Router: Submit Task(prompt, mode, constraints)
    Router->>Classifier: Classify Task(prompt)
    Classifier-->>Router: TaskDomain (e.g. "coding") + required capabilities
    Router->>Pool: Fetch Eligible Candidates(capabilities, streaming)
    Pool-->>Router: Candidate Tuples (Provider, Account, Model)
    loop For each candidate
        Router->>Candidate: Score Candidate (16 factors)
        Candidate-->>Router: Weighted Score (0-100) + breakdown
    end
    Router->>Router: Sort candidates & select highest score
    Router->>Exec: Dispatch Primary Winner
    alt Primary Succeeds
        Exec-->>Router: Success(result, tokens, latency)
        Router->>Client: Job Completed + Routing Explanation
    else Primary Fails (Retryable)
        Router->>Exec: Trigger Failover to Next Candidate
    end
```

### 2.4 Failover Lifecycle

```mermaid
flowchart TD
    Start[Primary Execution Fails] --> CheckError{Error Type?}
    CheckError -->|Non-Retryable: Auth / Context / Syntax| Abort[Fail Job Immediately]
    CheckError -->|Retryable: 429 / 5xx / Timeout / OOM| CheckAttempts{Attempts < Max?}
    CheckAttempts -->|No| Exhausted[Job Failed: All Failovers Exhausted]
    CheckAttempts -->|Yes| RecordFallback[Record Failover Event in Decision History]
    RecordFallback --> ApplyPenalty[Apply Cooldown / Health Penalty to Failing Account]
    ApplyPenalty --> NextCandidate[Select Next Highest Candidate in Ranked List]
    NextCandidate --> ExecuteNext[Dispatch Next Candidate]
    ExecuteNext --> Success{Succeeded?}
    Success -->|Yes| Done[Return Result with Failover Trail]
    Success -->|No| Start
```

---

## 3. Scoring Formula & Factor Weights

For any candidate $C = (P, A, M)$, the aggregate score $S(C)$ is:

$$S(C) = 100 \times \sum_{i=1}^{16} w_i \cdot F_i(C)$$

Where:
- $\sum_{i=1}^{16} w_i = 1.0$ (normalized mode weights)
- Each $F_i(C) \in [0.0, 1.0]$

### Weight Distributions by Mode

| Factor $F_i$ | Balanced | Performance | Cost | Reliability |
| :--- | :---: | :---: | :---: | :---: |
| `capability_match` | 0.25 | 0.30 | 0.15 | 0.15 |
| `provider_health` | 0.10 | 0.05 | 0.05 | 0.20 |
| `account_health` | 0.10 | 0.05 | 0.05 | 0.20 |
| `model_health` | 0.05 | 0.05 | 0.05 | 0.10 |
| `latency` | 0.10 | 0.20 | 0.05 | 0.05 |
| `recent_success_rate` | 0.05 | 0.05 | 0.05 | 0.10 |
| `failure_rate` | 0.05 | 0.05 | 0.05 | 0.10 |
| `account_priority` | 0.05 | 0.05 | 0.05 | 0.02 |
| `provider_priority` | 0.05 | 0.05 | 0.05 | 0.02 |
| `model_priority` | 0.05 | 0.05 | 0.05 | 0.02 |
| `quota_remaining` | 0.05 | 0.02 | 0.15 | 0.02 |
| `estimated_cost` | 0.05 | 0.01 | 0.25 | 0.01 |
| `task_complexity` | 0.02 | 0.04 | 0.01 | 0.01 |
| `context_size` | 0.01 | 0.01 | 0.01 | 0.00 |
| `streaming_support` | 0.01 | 0.01 | 0.01 | 0.00 |
| `tool_support` | 0.01 | 0.01 | 0.01 | 0.00 |
