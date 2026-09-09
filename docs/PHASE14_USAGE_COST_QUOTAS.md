# Phase 14 — Usage, Cost, Quotas & Performance Analytics

## 1. Overview & Architecture

Phase 14 introduces a provider-agnostic observability and economics engine within Universal AI Mission Control. It ensures complete operational visibility, economic forecasting, and quota enforcement across Antigravity, Cline, Kiro, API providers, and local models.

```
       Job Completed (tokens, latency, status, provider, account, model)
                                     |
                                     v
+-------------------------------------------------------------------------+
|                              UsageTracker                               |
|        Records events, aggregates rolling daily/monthly summaries       |
+-------------------------------------------------------------------------+
        |                            |                           |
        v                            v                           v
+----------------+          +----------------+          +----------------+
|  CostTracker   |          |  QuotaManager  |          |AnalyticsEngine |
| Input/Output   |          | Enforces limits|          | p50, p95, p99  |
| cost models    |          | 50/75/90/100%  |          | latency & rates|
+----------------+          +----------------+          +----------------+
        |                            |                           |
        +----------------------------+---------------------------+
                                     |
                                     v Feeds
+-------------------------------------------------------------------------+
|                  SmartRouter (Scoring & Candidate Pool)                 |
+-------------------------------------------------------------------------+
```

---

## 2. Usage Tracking Engine (`UsageTracker`)

Usage is recorded per individual job execution and aggregated along multiple dimensions:
- **Hierarchical Dimensions**:
  - `provider` (e.g. `antigravity`, `cline`, `openai`, `ollama`)
  - `account` (e.g. `cline-account-1`, `antigravity-account-1`)
  - `model` (e.g. `gemini-3.8-flash-medium`, `deepseek/deepseek-v4-flash`)
  - `job_id`
  - `day` (`YYYY-MM-DD`)
  - `month` (`YYYY-MM`)
- **Metrics Tracked**:
  - `total_requests`, `successful_requests`, `failed_requests`
  - `input_tokens`, `output_tokens`, `total_tokens`
  - `total_latency_ms`, `mean_latency_ms`
  - `retries_count`, `failovers_count`

---

## 3. Provider-Independent Cost Model (`CostTracker`)

The system establishes transparent pricing models without ever inventing synthetic prices:
- **Pricing Categories**:
  - **`free`**: Explicitly verified free providers (e.g. local Ollama instances, Cline free OAuth tiers). Cost = $0.00.
  - **`paid`**: Known pricing models defined with:
    - `input_cost_per_1m`: Cost per 1,000,000 input tokens (USD)
    - `output_cost_per_1m`: Cost per 1,000,000 output tokens (USD)
  - **`custom`**: Operator-configured pricing overrides in `config/pricing.json`.
  - **`unknown`**: Where token pricing cannot be authoritatively established, cost is strictly recorded as `unknown` (or omitted from monetary totals) to ensure accounting integrity.

---

## 4. Multi-Tier Quotas & Budget Alerts (`QuotaManager`)

Quota rules can be defined globally, per provider, per account, or per model:
- **Configurable Limits**:
  - `daily_requests`, `monthly_requests`
  - `daily_tokens`, `monthly_tokens`
  - `daily_cost_limit`, `monthly_cost_limit`
- **Budget Alert Thresholds**:
  - `50%` (Info): Logged as nominal milestone.
  - `75%` (Warning): Notification emitted; advisory logging.
  - `90%` (Critical): Routing score heavily penalized in `SmartRouter` to preserve remaining headroom.
  - `100%` (Exhausted): Account/provider marked unavailable; automatically excluded from candidates, routing all traffic to peer accounts or fallback providers.

---

## 5. Performance Analytics (`AnalyticsEngine`)

Calculates operational metrics across configurable rolling time windows:
- **Latency Percentiles**:
  - `p50` (Median latency): Baseline speed rating.
  - `p95` (Tail latency): Measures stability under load.
  - `p99` (Worst-case tail): Identifies persistent stalls.
- **Reliability Rates**:
  - `success_rate`: $Successful / Total$
  - `failure_rate`: $Failed / Total$
  - `retry_rate`: $Retries / Total$
  - `failover_rate`: $Failovers / Total$
- **Feedback Loop**:
  - Analytics feed directly into `SmartRouter`'s 16 scoring factors (`latency`, `recent_success_rate`, `failure_rate`), enabling the system to route around degrading providers automatically.

---

## 6. Storage & Data Retention (`RetentionManager`)

- **Lightweight Storage**: Backed by partitioned local JSON storage under `runtime/analytics/`, preserving standard-library independence.
- **Configurable Retention**:
  - Default retention: `retention_days = 30`.
  - Automated pruner purges records older than the cutoff threshold.
  - CLI support: `python3 scripts/brain.py quota reset` and retention maintenance routines.
