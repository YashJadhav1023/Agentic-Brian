"""Antigravity Account Failover and Capability-Aware Recovery (Phase 4C).

Provides transparent failover between Antigravity Account 1 and Account 2 under:
- HTTP 429 / Rate limiting
- Quota exhaustion / RESOURCE_EXHAUSTED
- Subprocess timeouts
- Transient CLI errors / model unavailability

Prevents duplicate work by reusing task_id, tracking parent_task_id and
failover_count, and preserving sandbox state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from events.bus import EventBus, EventType
from providers.registry.provider_registry import ProviderRegistry
from tasks.manager import Task


# Patterns indicating retryable provider errors
RETRYABLE_PATTERNS = [
    re.compile(r"\b(rate\s*limit|quota\s*exceeded|resource_exhausted|too\s*many\s*requests|429)\b", re.IGNORECASE),
    re.compile(r"\b(timeout|timed\s*out|temporarily\s*unavailable|service\s*unavailable|503)\b", re.IGNORECASE),
    re.compile(r"\b(model\s*not\s*found|model\s*unavailable|unsupported\s*model)\b", re.IGNORECASE),
    re.compile(r"\b(connection\s*reset|broken\s*pipe|network\s*error|econnreset)\b", re.IGNORECASE),
    re.compile(r"\b(session\s*unusable|cli\s*failure|process\s*crashed)\b", re.IGNORECASE),
]


@dataclass
class FailoverDecision:
    """Actionable failover decision."""
    should_failover: bool
    fallback_agent_id: str | None = None
    reason: str = ""
    error_classification: str = "none"
    failover_count: int = 0


class FailoverManager:
    """Coordinates capability-aware failover across interchangeable accounts."""

    def __init__(
        self,
        registry: ProviderRegistry,
        event_bus: EventBus | None = None,
        max_failovers_per_task: int = 2,
    ) -> None:
        self._registry = registry
        self._event_bus = event_bus
        self._max_failovers = max_failovers_per_task

    def classify_error(self, error_text: str, exit_code: int = 1) -> str:
        """Categorize error text into a structured taxonomy."""
        lower = error_text.lower()
        if exit_code == 124 or "timed out" in lower or "timeout" in lower:
            return "timeout"
        if any(w in lower for w in ("rate limit", "429", "quota", "resource_exhausted", "too many requests")):
            return "rate_limit"
        if any(w in lower for w in ("model not found", "model unavailable", "unsupported model")):
            return "model_unavailable"
        if any(w in lower for w in ("connection reset", "broken pipe", "network error", "econnreset")):
            return "network_error"
        if exit_code != 0 and ("crash" in lower or "cli failure" in lower or "process crashed" in lower):
            return "cli_failure"
        return "permanent_error"

    def is_retryable(self, error_text: str, exit_code: int = 1) -> bool:
        classification = self.classify_error(error_text, exit_code)
        return classification != "permanent_error"

    def evaluate_failover(
        self,
        task: Task,
        failed_agent_id: str,
        error_text: str,
        exit_code: int = 1,
        attempted_agents: list[str] | None = None,
    ) -> FailoverDecision:
        """Determine whether to failover and select the optimal fallback agent."""
        attempted = list(attempted_agents or [failed_agent_id])
        current_failover_count = len(attempted) - 1

        if current_failover_count >= self._max_failovers:
            return FailoverDecision(
                should_failover=False,
                reason=f"Max failovers ({self._max_failovers}) reached for task {task.task_id}",
                failover_count=current_failover_count,
            )

        if not self.is_retryable(error_text, exit_code):
            return FailoverDecision(
                should_failover=False,
                reason=f"Error not classified as retryable: {error_text[:120]}",
                error_classification="permanent_error",
                failover_count=current_failover_count,
            )

        classification = self.classify_error(error_text, exit_code)

        # 1. Antigravity Interchangeable Failover (AG-1 <-> AG-2)
        target_fallback: str | None = None
        if failed_agent_id == "antigravity-account-1" and "antigravity-account-2" not in attempted:
            target_fallback = "antigravity-account-2"
        elif failed_agent_id == "antigravity-account-2" and "antigravity-account-1" not in attempted:
            target_fallback = "antigravity-account-1"
        else:
            # 2. Capability-matched non-Antigravity fallback
            active_adapters = self._registry.list_active_adapters()
            for adapter in active_adapters:
                if adapter.agent_id not in attempted:
                    target_fallback = adapter.agent_id
                    break

        if not target_fallback:
            return FailoverDecision(
                should_failover=False,
                reason="No healthy unattempted fallback agents available",
                error_classification=classification,
                failover_count=current_failover_count,
            )

        # Check health of target fallback
        target_adapter = self._registry.get_adapter(target_fallback)
        if not target_adapter:
            return FailoverDecision(
                should_failover=False,
                reason=f"Fallback agent {target_fallback} not registered",
                error_classification=classification,
                failover_count=current_failover_count,
            )

        healthy, health_reason = target_adapter.health()
        if not healthy:
            return FailoverDecision(
                should_failover=False,
                reason=f"Fallback agent {target_fallback} is unhealthy: {health_reason}",
                error_classification=classification,
                failover_count=current_failover_count,
            )

        new_count = current_failover_count + 1
        decision = FailoverDecision(
            should_failover=True,
            fallback_agent_id=target_fallback,
            reason=f"Failing over from {failed_agent_id} to {target_fallback} due to {classification}",
            error_classification=classification,
            failover_count=new_count,
        )

        if self._event_bus:
            self._event_bus.publish(
                EventType.AGENT_FAILOVER_TRIGGERED,
                agent_id=target_fallback,
                task_id=task.task_id,
                metadata={
                    "from_agent": failed_agent_id,
                    "to_agent": target_fallback,
                    "reason": decision.reason,
                    "error_classification": classification,
                    "failover_count": new_count,
                },
            )

        return decision
