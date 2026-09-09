"""Performance Analytics Engine for Mission Control.

Calculates percentile latencies (p50, p95, p99), success/failure rates,
retry rates, and failover rates across providers, accounts, and models.
Directly influences the SmartRouter scoring engine.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from brain.analytics.usage_tracker import UsageTracker, get_usage_tracker


@dataclass
class PerformanceMetrics:
    """Statistical summary of operational performance."""
    target_id: str
    target_type: str
    sample_size: int = 0
    success_rate: float = 100.0
    failure_rate: float = 0.0
    retry_rate: float = 0.0
    failover_rate: float = 0.0
    avg_latency: float = 0.0
    p50_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _percentile(values: list[float], pct: float) -> float:
    """Deterministic percentile calculation without external dependencies."""
    if not values:
        return 0.0
    k = (len(values) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(values[int(k)], 3)
    d0 = values[int(f)] * (c - k)
    d1 = values[int(c)] * (k - f)
    return round(d0 + d1, 3)


class AnalyticsEngine:
    """Computes statistical metrics from historical usage records."""

    def __init__(self, usage_tracker: UsageTracker | None = None) -> None:
        self._usage_tracker = usage_tracker or get_usage_tracker()
        self._recorded_metrics: list[dict[str, Any]] = []
        self._recorded_failovers: int = 0

    def record_metric(self, latency_ms: float = 0.0, success: bool = True, **kwargs: Any) -> None:
        """Record sample metric for live/local analysis."""
        self._recorded_metrics.append({
            "latency_ms": latency_ms,
            "success": success,
            **kwargs,
        })

    def record_failover(self) -> None:
        """Increment failover event count."""
        self._recorded_failovers += 1

    def get_latency_percentiles(self) -> dict[str, float]:
        """Compute p50, p95, and p99 percentiles."""
        if self._recorded_metrics:
            latencies = sorted([m["latency_ms"] for m in self._recorded_metrics if m.get("latency_ms", 0.0) > 0])
        else:
            latencies = sorted([r.latency for r in self._usage_tracker._records if r.latency > 0])
        return {
            "p50": _percentile(latencies, 50.0),
            "p95": _percentile(latencies, 95.0),
            "p99": _percentile(latencies, 99.0),
        }

    def get_rates(self) -> dict[str, float | int]:
        """Compute success rate, failure rate, and failover rate."""
        total = len(self._recorded_metrics)
        if total == 0:
            return {
                "total_requests": 0,
                "success_rate": 1.0,
                "failure_rate": 0.0,
                "failover_rate": 0.0,
            }
        succ = sum(1 for m in self._recorded_metrics if m.get("success", True))
        fail = total - succ
        fo = self._recorded_failovers
        return {
            "total_requests": total,
            "success_rate": succ / total,
            "failure_rate": fail / total,
            "failover_rate": fo / total,
        }

    def get_metrics(
        self,
        provider_id: str | None = None,
        account_id: str | None = None,
        model_id: str | None = None,
    ) -> PerformanceMetrics:
        target_id = account_id or provider_id or model_id or "global"
        target_type = "account" if account_id else ("provider" if provider_id else ("model" if model_id else "global"))

        recs = self._usage_tracker._filter_records(
            provider_id=provider_id,
            account_id=account_id,
            model_id=model_id,
        )

        n = len(recs)
        if n == 0:
            return PerformanceMetrics(
                target_id=target_id,
                target_type=target_type,
                sample_size=0,
                success_rate=100.0,
                failure_rate=0.0,
                retry_rate=0.0,
                failover_rate=0.0,
                avg_latency=0.0,
                p50_latency=0.0,
                p95_latency=0.0,
                p99_latency=0.0,
            )

        successful = sum(1 for r in recs if r.success)
        failed = n - successful
        retried = sum(1 for r in recs if r.retries > 0)
        failovers = sum(1 for r in recs if r.failovers > 0)

        latencies = sorted([r.latency for r in recs if r.latency > 0])
        avg_lat = round(sum(latencies) / len(latencies), 3) if latencies else 0.0

        p50 = _percentile(latencies, 50.0)
        p95 = _percentile(latencies, 95.0)
        p99 = _percentile(latencies, 99.0)

        return PerformanceMetrics(
            target_id=target_id,
            target_type=target_type,
            sample_size=n,
            success_rate=round((successful / n) * 100.0, 1),
            failure_rate=round((failed / n) * 100.0, 1),
            retry_rate=round((retried / n) * 100.0, 1),
            failover_rate=round((failovers / n) * 100.0, 1),
            avg_latency=avg_lat,
            p50_latency=p50,
            p95_latency=p95,
            p99_latency=p99,
        )

    def get_all_accounts_metrics(self) -> dict[str, PerformanceMetrics]:
        accounts: set[str] = {r.account_id for r in self._usage_tracker._records if r.account_id}
        return {aid: self.get_metrics(account_id=aid) for aid in accounts}

    def get_all_providers_metrics(self) -> dict[str, PerformanceMetrics]:
        providers: set[str] = {r.provider_id for r in self._usage_tracker._records if r.provider_id}
        return {pid: self.get_metrics(provider_id=pid) for pid in providers}


_GLOBAL_ANALYTICS_ENGINE: AnalyticsEngine | None = None


def get_analytics_engine() -> AnalyticsEngine:
    global _GLOBAL_ANALYTICS_ENGINE
    if _GLOBAL_ANALYTICS_ENGINE is None:
        _GLOBAL_ANALYTICS_ENGINE = AnalyticsEngine()
    return _GLOBAL_ANALYTICS_ENGINE
