"""Smart Agent and Model Router.

Routing is a scored competition, not a lookup table. Every healthy agent is a
candidate for every task, so Antigravity Account 2 wins work on merit without
the user naming it. Explicit selection still overrides everything.

Score components per candidate:

    keyword affinity   strong signal from specialty phrasing
    capability overlap required capabilities vs declared capabilities
    strength fit       does the agent's model catalogue reach the needed tier
    load penalty       prefer an idle account over a busy one

The final model is chosen by the deterministic model policy for the winning
agent, so a routing decision always names both agent and model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agents.base.adapter import AgentAdapter, AgentStatus, Capability
from models.policies.model_policy import AGENT_CATALOGS, Complexity, select_model
from providers.registry.provider_registry import ProviderRegistry

#: Keyword affinity: phrasing that signals a particular agent's specialty.
AGENT_KEYWORDS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "antigravity-account-1",
        re.compile(r"\b(architecture|governance|strategy|design doc|rfc|protocol|spec|system design)\b", re.I),
        "high-level architectural design or protocol governance",
    ),
    (
        "antigravity-account-2",
        re.compile(r"\b(refactor|refactoring|component|review|clean code|modernize|telemetry|consolidate|optimize|restructure|split)\b", re.I),
        "deep refactoring, component modernization and code review",
    ),
    (
        "cline",
        re.compile(r"\b(frontend|ui|css|html|styling|tailwind|view|layout|flexbox)\b", re.I),
        "frontend styling or UI component adjustments",
    ),
    (
        "kiro-cli",
        re.compile(r"\b(test|tests|pytest|unittest|terminal|bash|shell|docker|kubernetes|k8s|ci/cd|pipeline|git|deploy|build|azure|kubectl)\b", re.I),
        "terminal execution, testing or environment validation",
    ),
)

#: Capability inference: what the task actually needs, independent of any agent.
CAPABILITY_KEYWORDS: tuple[tuple[Capability, re.Pattern[str]], ...] = (
    (Capability.ARCHITECTURE, re.compile(r"\b(architecture|architect|system design|design doc|topology)\b", re.I)),
    (Capability.PROTOCOL_DESIGN, re.compile(r"\b(protocol|rfc|contract|schema|spec|interface)\b", re.I)),
    (Capability.GOVERNANCE, re.compile(r"\b(governance|policy|compliance|standard|guardrail)\b", re.I)),
    (Capability.DEEP_REASONING, re.compile(r"\b(analy[sz]e|reason|investigate|root cause|trade-?off|evaluate)\b", re.I)),
    (Capability.EDITOR_REFACTORING, re.compile(r"\b(refactor|refactoring|restructure|rename|modernize|split|extract)\b", re.I)),
    (Capability.COMPONENT_REFACTORING, re.compile(r"\b(component|module|service|handler|class|consolidate)\b", re.I)),
    (Capability.CODE_REVIEW, re.compile(r"\b(review|audit|inspect|critique|lint)\b", re.I)),
    (Capability.FRONTEND_STYLING, re.compile(r"\b(css|styling|tailwind|layout|flexbox|frontend|ui)\b", re.I)),
    (Capability.TERMINAL_OPERATIONS, re.compile(r"\b(terminal|bash|shell|command|cli|kubectl|docker)\b", re.I)),
    (Capability.BUILD_AND_TEST, re.compile(r"\b(test|tests|pytest|unittest|build|compile|ci/cd|pipeline)\b", re.I)),
    (Capability.LOCAL_VALIDATION, re.compile(r"\b(validate|verify|check|sanity)\b", re.I)),
    (Capability.CLOUD_READ_ONLY, re.compile(r"\b(azure|aws|subscription|resource group|cloud)\b", re.I)),
    (Capability.KUBERNETES_READ_ONLY, re.compile(r"\b(kubernetes|k8s|aks|pod|namespace|kubectl)\b", re.I)),
    (Capability.DOCUMENTATION, re.compile(r"\b(document|documentation|readme|docs|changelog)\b", re.I)),
    (Capability.TEST_SCAFFOLDING, re.compile(r"\b(test scaffold|add tests|unit test|write tests)\b", re.I)),
)

KEYWORD_AFFINITY_WEIGHT = 10.0
CAPABILITY_WEIGHT = 4.0
STRENGTH_FIT_WEIGHT = 3.0
LOAD_PENALTY = 6.0

COMPLEXITY_TARGET_STRENGTH = {
    Complexity.FAST: 2,
    Complexity.STANDARD: 3,
    Complexity.STRONG: 4,
    Complexity.REASONING: 5,
}


@dataclass
class CandidateScore:
    agent_id: str
    account_id: str
    provider: str
    score: float
    matched_capabilities: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "account_id": self.account_id,
            "provider": self.provider,
            "score": round(self.score, 2),
            "matched_capabilities": self.matched_capabilities,
            "reasons": self.reasons,
        }


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
    required_capabilities: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)


class SmartRouter:
    """Capability-aware task routing across all registered agents."""

    def __init__(self, registry: ProviderRegistry, task_manager: Any | None = None) -> None:
        self._registry = registry
        self._task_manager = task_manager

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def infer_complexity(self, text: str) -> Complexity:
        lower = text.lower()
        if any(w in lower for w in ("deep reasoning", "formal proof", "mission critical", "high risk", "complex rfc")):
            return Complexity.REASONING
        if any(w in lower for w in ("refactor all", "restructure", "comprehensive", "full audit", "major rewrite", "across every")):
            return Complexity.STRONG
        if any(w in lower for w in ("quick fix", "typo", "lint", "comment", "format", "minor tweak", "sanity")):
            return Complexity.FAST
        return Complexity.STANDARD

    def infer_capabilities(self, text: str) -> frozenset[Capability]:
        return frozenset(cap for cap, pattern in CAPABILITY_KEYWORDS if pattern.search(text))

    @staticmethod
    def _max_strength(agent_id: str) -> int:
        catalog = AGENT_CATALOGS.get(agent_id)
        return max((spec.strength for spec in catalog), default=3) if catalog else 3

    def _busy(self, adapter: AgentAdapter) -> bool:
        try:
            return adapter.status() == AgentStatus.WORKING
        except Exception:
            return False

    def score_candidates(
        self, task_text: str, complexity: Complexity | None = None
    ) -> list[CandidateScore]:
        """Score every healthy agent against the task. Highest score wins."""
        complexity = complexity or self.infer_complexity(task_text)
        required = self.infer_capabilities(task_text)
        target_strength = COMPLEXITY_TARGET_STRENGTH.get(complexity, 3)

        scores: list[CandidateScore] = []
        for adapter in self._registry.list_active_adapters():
            candidate = CandidateScore(
                agent_id=adapter.agent_id,
                account_id=adapter.account_id,
                provider=adapter.provider,
                score=0.0,
            )

            for agent_id, pattern, explanation in AGENT_KEYWORDS:
                if agent_id == adapter.agent_id and pattern.search(task_text):
                    candidate.score += KEYWORD_AFFINITY_WEIGHT
                    candidate.reasons.append(f"keyword affinity: {explanation}")
                    break

            declared = adapter.capabilities()
            overlap = required & declared
            if overlap:
                candidate.score += CAPABILITY_WEIGHT * len(overlap)
                candidate.matched_capabilities = sorted(c.value for c in overlap)
                candidate.reasons.append(
                    f"{len(overlap)}/{len(required)} required capabilities declared"
                )

            reachable = self._max_strength(adapter.agent_id)
            if reachable >= target_strength:
                candidate.score += STRENGTH_FIT_WEIGHT
                candidate.reasons.append(
                    f"model catalogue reaches strength {reachable} (needs {target_strength})"
                )
            else:
                candidate.reasons.append(
                    f"model catalogue tops out at strength {reachable}, below {target_strength}"
                )

            if self._busy(adapter):
                candidate.score -= LOAD_PENALTY
                candidate.reasons.append("currently WORKING; deprioritized")

            scores.append(candidate)

        scores.sort(key=lambda c: c.score, reverse=True)
        return scores

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def route(
        self,
        task_text: str,
        preferred_agent: str | None = None,
        preferred_model: str | None = None,
        forced_complexity: Complexity | None = None,
    ) -> RoutingDecision:
        complexity = forced_complexity or self.infer_complexity(task_text)
        required = sorted(c.value for c in self.infer_capabilities(task_text))

        # 1. Explicit agent selection always wins when the agent is healthy.
        if preferred_agent:
            adapter = self._registry.get_adapter(preferred_agent)
            if adapter is None:
                raise RuntimeError(
                    f"Requested agent '{preferred_agent}' is not registered. "
                    f"Known agents: {sorted(a.agent_id for a in self._registry.list_active_adapters())}"
                )
            healthy, health_reason = adapter.health()
            if not healthy:
                raise RuntimeError(
                    f"Requested agent '{preferred_agent}' is not healthy: {health_reason}"
                )
            model = preferred_model or select_model(
                adapter.agent_id, complexity=complexity
            ).preferred_model
            fallbacks = [
                c for c in self.score_candidates(task_text, complexity)
                if c.agent_id != adapter.agent_id
            ]
            return RoutingDecision(
                agent_id=adapter.agent_id,
                account_id=adapter.account_id,
                provider=adapter.provider,
                model=model,
                complexity=complexity,
                reason=f"Explicitly selected agent {preferred_agent}",
                fallback_agent_id=fallbacks[0].agent_id if fallbacks else None,
                fallback_model=(
                    select_model(fallbacks[0].agent_id, complexity=complexity).preferred_model
                    if fallbacks else None
                ),
                required_capabilities=required,
                candidates=[c.to_dict() for c in fallbacks],
            )

        # 2. Scored competition across every healthy agent.
        candidates = self.score_candidates(task_text, complexity)
        if not candidates:
            raise RuntimeError("No healthy agents available in ProviderRegistry")

        winner = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        model = preferred_model or select_model(winner.agent_id, complexity=complexity).preferred_model

        reason = "; ".join(winner.reasons) if winner.reasons else "highest scoring available agent"

        return RoutingDecision(
            agent_id=winner.agent_id,
            account_id=winner.account_id,
            provider=winner.provider,
            model=model,
            complexity=complexity,
            reason=f"score {winner.score:.1f} — {reason}",
            fallback_agent_id=runner_up.agent_id if runner_up else None,
            fallback_model=(
                select_model(runner_up.agent_id, complexity=complexity).preferred_model
                if runner_up else None
            ),
            required_capabilities=required,
            candidates=[c.to_dict() for c in candidates],
        )
