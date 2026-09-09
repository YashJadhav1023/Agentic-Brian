"""Routing Models, Enums, and Failover Specifications for Mission Control.

Defines routing modes (Performance, Balanced, Cost, Reliability, Manual),
multi-factor candidate representations, and deterministic failover chains.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from models.policies.model_policy import Complexity


class RoutingMode(str, Enum):
    """Cost and operational optimization modes."""
    PERFORMANCE = "performance"  # prioritize latency and highest model capability
    BALANCED = "balanced"        # weighted combination of capability, health, latency, cost
    COST = "cost"                # prioritize lowest token cost and free local models
    RELIABILITY = "reliability"  # prioritize highest success rate and healthy accounts
    MANUAL = "manual"            # strictly obey explicit selection


@dataclass
class FailoverAttempt:
    """Record of a failed execution attempt triggering failover."""
    attempt_number: int
    original_provider: str
    original_account: str
    error: str
    next_provider: str
    next_account: str
    reason: str
    retryable: bool = True
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FailoverConfig:
    """Configurable policy governing failover limits and error classification."""
    max_attempts: int = 4
    cooldown_seconds: float = 60.0
    retryable_errors: list[str] = field(default_factory=lambda: [
        "rate_limit", "429", "timeout", "timed out", "connection error",
        "connection refused", "500", "502", "503", "504", "server error", "temporary"
    ])
    non_retryable_errors: list[str] = field(default_factory=lambda: [
        "auth_error", "unauthorized", "401", "forbidden", "403",
        "invalid_request", "400", "not_found", "404"
    ])

    def is_retryable(self, error_message: str) -> bool:
        err_lower = error_message.lower()
        # Non-retryable takes precedence
        if any(non.lower() in err_lower for non in self.non_retryable_errors):
            return False
        # If in retryable list or general transient error
        return any(ret.lower() in err_lower for ret in self.retryable_errors) or True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RoutingCandidate:
    """Candidate agent/account/model evaluated for a task."""
    provider_id: str
    account_id: str
    model_id: str
    capabilities: list[str] = field(default_factory=list)
    provider_healthy: bool = True
    account_healthy: bool = True
    model_healthy: bool = True
    cooldown_active: bool = False
    account_priority: int = 10
    provider_priority: int = 10
    model_priority: int = 10
    quota_remaining: int | None = None
    estimated_cost: float | None = None
    estimated_cost_per_1m: float | None = None
    latency: float = 0.0
    latency_ms: float = 0.0
    success_rate: float = 1.0
    recent_success_rate: float = 100.0
    failure_rate: float = 0.0
    streaming_supported: bool = True
    tool_supported: bool = True
    context_window: int = 128_000
    agent_id: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id:
            self.agent_id = self.account_id
        if self.latency_ms and not self.latency:
            self.latency = self.latency_ms
        elif self.latency and not self.latency_ms:
            self.latency_ms = self.latency
        if self.success_rate <= 1.0 and self.recent_success_rate == 100.0 and self.success_rate > 0:
            self.recent_success_rate = self.success_rate * 100.0
        if self.estimated_cost_per_1m is not None and self.estimated_cost is None:
            self.estimated_cost = self.estimated_cost_per_1m


@dataclass
class RoutingScore:
    """Evaluation score across all 16 multi-dimensional factors."""
    total_score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    candidate: Any = None
    capability_match: float = 0.0
    provider_health: float = 0.0
    account_health: float = 0.0
    model_health: float = 0.0
    latency: float = 0.0
    recent_success_rate: float = 0.0
    failure_rate: float = 0.0
    account_priority: float = 0.0
    provider_priority: float = 0.0
    model_priority: float = 0.0
    quota_remaining: float = 0.0
    estimated_cost: float = 0.0
    task_complexity: float = 0.0
    context_size: float = 0.0
    streaming_support: float = 0.0
    tool_support: float = 0.0

    def __getattr__(self, name: str) -> Any:
        if name in self.breakdown:
            return self.breakdown[name]
        raise AttributeError(f"RoutingScore has no attribute '{name}'")


@dataclass
class CandidateScore:
    """Scored candidate agent/account/model."""
    agent_id: str
    account_id: str
    provider: str
    score: float
    model_id: str = ""
    matched_capabilities: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    estimated_cost: float | None = None
    latency: float = 0.0
    success_rate: float = 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "account_id": self.account_id,
            "provider": self.provider,
            "score": round(self.score, 2),
            "model_id": self.model_id,
            "matched_capabilities": self.matched_capabilities,
            "reasons": self.reasons,
            "score_breakdown": {k: round(v, 2) for k, v in self.score_breakdown.items()},
            "estimated_cost": self.estimated_cost,
            "latency": round(self.latency, 3),
            "success_rate": round(self.success_rate, 1),
        }


@dataclass
class RoutingDecision:
    """Comprehensive routing decision with human-readable explanation."""
    agent_id: str
    account_id: str
    provider: str
    model: str
    complexity: Complexity
    reason: str
    job_id: str = ""
    routing_mode: RoutingMode = RoutingMode.BALANCED
    total_score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    fallback_agent_id: str | None = None
    fallback_model: str | None = None
    fallback_chain: list[dict[str, Any]] = field(default_factory=list)
    task_type: str = "general"
    required_capabilities: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    failover_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def provider_id(self) -> str:
        return self.provider

    @property
    def failover_chain(self) -> list[dict[str, Any]]:
        return self.fallback_chain

    def explain(self) -> str:
        """Render a formatted human-readable score and reason explanation."""
        lines = [
            f"Selected Provider: {self.provider}",
            f"Selected Account:  {self.account_id or self.agent_id}",
            f"Selected Model:    {self.model}",
            f"Routing Mode:      {self.routing_mode.value if hasattr(self.routing_mode, 'value') else self.routing_mode}",
            f"Task Type:         {self.task_type}",
            f"Total Score:       {self.total_score:.1f}",
            "Score Breakdown:",
        ]
        for factor, val in sorted(self.score_breakdown.items()):
            sign = "+" if val >= 0 else ""
            lines.append(f"  - {factor:<22}: {sign}{val:.1f}")
        if self.reason:
            lines.append(f"Reason: {self.reason}")
        if self.fallback_chain:
            lines.append(f"Failover Chain ({len(self.fallback_chain)} backups):")
            for idx, fb in enumerate(self.fallback_chain, 1):
                lines.append(f"  {idx}. {fb.get('provider')}/{fb.get('account') or fb.get('agent_id')} ({fb.get('model')})")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "account_id": self.account_id,
            "provider": self.provider,
            "provider_id": self.provider,
            "model": self.model,
            "model_id": self.model,
            "complexity": self.complexity.value if hasattr(self.complexity, "value") else str(self.complexity),
            "routing_mode": self.routing_mode.value if hasattr(self.routing_mode, "value") else str(self.routing_mode),
            "task_type": self.task_type,
            "total_score": round(self.total_score, 2),
            "score_breakdown": {k: round(v, 2) for k, v in self.score_breakdown.items()},
            "reason": self.reason,
            "explanation": self.explain(),
            "fallback_agent_id": self.fallback_agent_id,
            "fallback_model": self.fallback_model,
            "fallback_chain": self.fallback_chain,
            "required_capabilities": self.required_capabilities,
            "candidates": self.candidates,
            "failover_history": self.failover_history,
        }
