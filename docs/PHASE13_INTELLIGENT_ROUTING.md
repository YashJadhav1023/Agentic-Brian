# Phase 13 — Multi-Factor Intelligent Routing Engine

## 1. Architectural Overview

The Phase 13 Intelligent Routing Engine upgrades `SmartRouter` from simple round-robin or capability matching to a deterministic, multi-factor scoring and routing engine.

It determines:
1. **Which Provider** is best suited for the workload.
2. **Which Account** should execute the task (considering health, cooldown, and quotas).
3. **Which Model** satisfies capability requirements with optimal cost/performance balance.
4. **Which Failover Path** to execute if primary attempts fail.

---

## 2. The 16 Routing Factors

Every eligible candidate (provider + account + model tuple) is evaluated against 16 distinct weighted criteria:

| Factor Name | Range | Description |
| :--- | :--- | :--- |
| `capability_match` | 0.0 - 1.0 | Degree of alignment between required capabilities and candidate capabilities. *(Note: Strictly named `capability_match`, not `capability_overlap`)*. |
| `provider_health` | 0.0 - 1.0 | Operational status of the provider (1.0 = healthy, 0.5 = degraded, 0.0 = down/disabled). |
| `account_health` | 0.0 - 1.0 | Operational status of the account; accounts in cooldown or failing receive 0.0. |
| `model_health` | 0.0 - 1.0 | Verified operational health and availability of the specific model. |
| `latency` | 0.0 - 1.0 | Normalized speed score based on observed p50/p95 response times (lower latency scores higher). |
| `recent_success_rate` | 0.0 - 1.0 | Ratio of successful jobs over the last rolling window. |
| `failure_rate` | 0.0 - 1.0 | Penalty inverse of failure frequency; high failure rate sharply reduces this factor. |
| `account_priority` | 0.0 - 1.0 | Configured account priority tier. |
| `provider_priority` | 0.0 - 1.0 | Configured provider preference tier. |
| `model_priority` | 0.0 - 1.0 | Model preference tier within the provider/account configuration. |
| `quota_remaining` | 0.0 - 1.0 | Percentage of daily/monthly request, token, and budget quota remaining. |
| `estimated_cost` | 0.0 - 1.0 | Cost efficiency score; cheaper models score higher, especially in `cost` mode. |
| `task_complexity` | 0.0 - 1.0 | Alignment between task difficulty (e.g. architecture vs quick question) and model tier. |
| `context_size` | 0.0 - 1.0 | Compatibility with input context length vs model context window. |
| `streaming_support` | 0.0 - 1.0 | Full score if candidate supports streaming when requested; penalized if unsupported. |
| `tool_support` | 0.0 - 1.0 | Score for tool-use and agentic execution compatibility when required. |
| `user_preference` | 0.0 - 1.0 | Explicit bonus if candidate matches user-specified provider/account/model constraints. |

---

## 3. Deterministic Task Classification

Incoming tasks are classified into 10 deterministic domains using pattern matching and keyword heuristics (no external LLM dependency required):

```
                       +-------------------+
                       |    Task Prompt    |
                       +-------------------+
                                 |
                                 v
          +-------------------------------------------------+
          |        TaskClassifier (Regex & Heuristics)      |
          +-------------------------------------------------+
            |             |              |               |
     [coding]      [debugging]     [architecture]     [DevOps]
            |             |              |               |
   [documentation]   [research]     [reasoning]    [quick_question]
            |             |              |               |
     [long_context]  [automation]        +---------------+
```

1. **`coding`**: Code implementation, refactoring, algorithms, unit tests, scripts.
2. **`debugging`**: Bug analysis, stack traces, crash dumps, exception diagnostics.
3. **`architecture`**: System design, data models, protocol specifications, trade-off analysis.
4. **`DevOps`**: CI/CD pipelines, Docker, Kubernetes, deployment scripts, server configuration.
5. **`documentation`**: Markdown docs, API references, architecture decision records (ADRs).
6. **`research`**: Literature review, technological evaluation, comparative benchmark study.
7. **`reasoning`**: Mathematical logic, multi-step deductive problem solving, algorithmic puzzles.
8. **`quick_question`**: Concise queries, syntax checks, definition lookups.
9. **`long_context`**: Multi-file repository ingestion, large log dumps, extensive file comparisons.
10. **`automation`**: Repetitive file manipulations, batch conversions, workflow scripts.

---

## 4. Routing Modes

The router dynamically adjusts factor weighting based on selected `RoutingMode`:

- **`balanced` (Default)**: Harmonious weighting prioritizing capability match (30%), health & reliability (25%), latency (15%), and cost (10%).
- **`performance`**: Heavy weighting on model capability tier (40%), speed/latency (25%), and reasoning power; cost penalties minimized.
- **`cost`**: Maximizes cost efficiency (40%) and quota preservation; prioritizes local models (Ollama) or free/cheap API tiers.
- **`reliability`**: Prioritizes provider/account health (40%), recent success rate (25%), and lowest failure rates; bypasses recently degraded targets.
- **`manual`**: Strictly executes the explicitly requested provider, account, and model tuple, validating basic health.

---

## 5. Account Rotation & Health Penalties

To prevent over-saturating a single account and to ensure even quota distribution:
1. **Health Verification**: Accounts marked `offline` or with active error flags are excluded from candidate pools.
2. **Cooldown Enforcement**: Accounts experiencing 429 (Rate Limit) or transient 5xx errors enter a configurable cooldown period (e.g. 60–300s). Cooldown accounts receive score 0.0 and are never selected.
3. **Usage Levelling**: Total tokens consumed and requests processed during the current window serve as dampeners, rotating load across identical peer accounts (e.g., rotating between `cline-account-1`, `cline-account-2`, and `cline-account-3`).
4. **Quota-Aware Degradation**: Accounts approaching 90% or 100% quota threshold receive score deductions and route failovers to peer accounts with remaining quota.

---

## 6. Deterministic Failover Engine

If the winning candidate encounters an execution failure:

```
[Candidate 1: Antigravity account-1]
           |
           x (Transient Rate Limit / Timeout)
           |
           v Failover
[Candidate 2: Antigravity account-2]
           |
           x (Provider Degraded)
           |
           v Failover
[Candidate 3: Cline account-1]
           |
           x (Execution Failure)
           |
           v Failover
[Candidate 4: OpenAI API Provider]
           |
           +--> Success!
```

### Failover Rules
- **Non-Retryable Errors**: Authentication invalid, context window exceeded, invalid prompt syntax. Failover is aborted immediately to avoid cascading errors.
- **Retryable Errors**: HTTP 429, HTTP 500/502/503, connection timeouts, subprocess exit code 137 (OOM).
- **Audit Logging**: Every failover step records:
  - `original_provider`, `original_account`, `original_model`
  - `error_type` and `error_message`
  - `next_provider`, `next_account`, `next_model`
  - `reason` and failover attempt index
  - Bounded by `max_attempts` (default: 3).

---

## 7. Explainability & History

Every routing decision generates a transparent, auditable breakdown:
```json
{
  "selected_provider": "cline",
  "selected_account": "cline-account-2",
  "selected_model": "deepseek/deepseek-v4-flash",
  "final_score": 92.4,
  "factor_scores": {
    "capability_match": 0.30,
    "provider_health": 0.20,
    "account_health": 0.15,
    "latency": 0.12,
    "cost": 0.10,
    "quota_remaining": 0.05
  },
  "rationale": "High coding capability match, low latency, account-1 rotated, quota available"
}
```

Decisions are persisted to the routing history store and queryable via:
- CLI: `brain routing history` and `brain routing inspect <job-id>`
- API: `GET /api/routing/history` and `GET /api/routing/history/{id}`
- Mission Control Web Dashboard
