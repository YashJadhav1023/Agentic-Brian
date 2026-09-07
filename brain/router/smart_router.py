"""Smart Agent and Model Router.

Analyzes incoming task requirements, required capabilities, and agent health,
then selects the optimal agent and model with explicit fallbacks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agents.base.adapter import Capability
from models.policies.model_policy import Complexity, select_model
from providers.registry.provider_registry import ProviderRegistry


@dataclass
class RoutingDecision:
    agent_id: str
    account_id: str
    provider: str
    model: str
    complexity: Complexity
    reason: str
    fallback_agent_id: str | None = None
    fallback_model: str | None = None


class SmartRouter:
    """Intelligent task routing across the four active agents."""

    # Keyword patterns mapped to primary agents
    ROUTING_PATTERNS = [
        # Antigravity Account 1: Architecture, Governance, Strategic Planning
        (
            re.compile(r"\b(architecture|governance|strategy|design doc|rfc|protocol|spec|system design)\b", re.I),
            "antigravity-account-1",
            "Task requires high-level architectural design or protocol governance",
        ),
        # Antigravity Account 2: Deep Refactoring, Component Architecture, Code Review
        (
            re.compile(r"\b(refactor|component|review|clean code|modernize|telemetry|consolidate|optimize)\b", re.I),
            "antigravity-account-2",
            "Task matches deep refactoring and component modernization specialty",
        ),
        # Cline: Frontend, CSS, UI, Styling, React
        (
            re.compile(r"\b(frontend|ui|css|html|styling|tailwind|component styling|view|layout)\b", re.I),
            "cline",
            "Task requires frontend styling or UI component adjustments",
        ),
        # Kiro: Terminal commands, testing, pipelines, docker, git, infrastructure
        (
            re.compile(r"\b(test|pytest|unittest|terminal|bash|docker|kubernetes|k8s|ci/cd|pipeline|git|deploy|build)\b", re.I),
            "kiro-cli",
            "Task requires terminal execution, testing, or environment validation",
        ),
    ]

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def infer_complexity(self, text: str) -> Complexity:
        lower = text.lower()
        if any(w in lower for w in ("deep reasoning", "formal proof", "mission critical", "high risk", "complex rfc")):
            return Complexity.REASONING
        if any(w in lower for w in ("refactor all", "restructure", "comprehensive", "full audit", "major rewrite")):
            return Complexity.STRONG
        if any(w in lower for w in ("quick fix", "typo", "lint", "comment", "format", "minor tweak", "sanity")):
            return Complexity.FAST
        return Complexity.STANDARD

    def route(
        self,
        task_text: str,
        preferred_agent: str | None = None,
        preferred_model: str | None = None,
        forced_complexity: Complexity | None = None,
    ) -> RoutingDecision:
        complexity = forced_complexity or self.infer_complexity(task_text)

        # 1. Handle explicit agent preference
        if preferred_agent:
            adapter = self._registry.get_adapter(preferred_agent)
            if adapter:
                healthy, reason = adapter.health()
                if healthy:
                    model_decision = select_model(adapter.agent_id, complexity=complexity)
                    chosen_model = preferred_model or model_decision.preferred_model
                    return RoutingDecision(
                        agent_id=adapter.agent_id,
                        account_id=adapter.account_id,
                        provider=adapter.provider,
                        model=chosen_model,
                        complexity=complexity,
                        reason=f"User preferred agent {preferred_agent}",
                        fallback_agent_id="antigravity-account-1" if preferred_agent != "antigravity-account-1" else "kiro-cli",
                    )

        # 2. Match heuristics
        selected_agent_id = "antigravity-account-1"  # Default primary
        reason = "Default autonomous agent assignment"

        for pattern, candidate_id, explanation in self.ROUTING_PATTERNS:
            if pattern.search(task_text):
                adapter = self._registry.get_adapter(candidate_id)
                if adapter:
                    healthy, _ = adapter.health()
                    if healthy:
                        selected_agent_id = candidate_id
                        reason = explanation
                        break

        adapter = self._registry.get_adapter(selected_agent_id)
        if not adapter:
            # Fallback to any active adapter
            active = self._registry.list_active_adapters()
            if not active:
                raise RuntimeError("No healthy agents available in ProviderRegistry")
            adapter = active[0]
            selected_agent_id = adapter.agent_id
            reason = "Fallback to first healthy available agent"

        model_decision = select_model(adapter.agent_id, complexity=complexity)
        chosen_model = preferred_model or model_decision.preferred_model

        # Determine fallback agent
        fallback_candidates = [
            a.agent_id for a in self._registry.list_active_adapters()
            if a.agent_id != selected_agent_id
        ]
        fallback_id = fallback_candidates[0] if fallback_candidates else None
        fallback_model = None
        if fallback_id:
            fallback_model = select_model(fallback_id, complexity=complexity).preferred_model

        return RoutingDecision(
            agent_id=adapter.agent_id,
            account_id=adapter.account_id,
            provider=adapter.provider,
            model=chosen_model,
            complexity=complexity,
            reason=reason,
            fallback_agent_id=fallback_id,
            fallback_model=fallback_model,
        )
