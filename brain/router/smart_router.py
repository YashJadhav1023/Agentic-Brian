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
from pathlib import Path
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
RELIABILITY_WEIGHT = 4.0
LATENCY_WEIGHT = 2.0
TOKEN_EFFICIENCY_WEIGHT = 2.0
HEALTH_WEIGHT = 2.0
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
    score_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "account_id": self.account_id,
            "provider": self.provider,
            "score": round(self.score, 2),
            "matched_capabilities": self.matched_capabilities,
            "reasons": self.reasons,
            "score_breakdown": {k: round(v, 2) for k, v in self.score_breakdown.items()},
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
    """Capability-aware, multi-factor task routing across all registered agents."""

    def __init__(
        self,
        registry: ProviderRegistry,
        task_manager: Any | None = None,
        history_file: Path | None = None,
        telemetry_tracker: Any | None = None,
    ) -> None:
        self._registry = registry
        self._task_manager = task_manager
        self._history_file = history_file or (Path(__file__).resolve().parent.parent.parent / "runtime" / "logs" / "routing_history.jsonl")
        self._telemetry_tracker = telemetry_tracker

    def _get_agent_metrics(self, agent_id: str) -> dict[str, Any]:
        if self._telemetry_tracker:
            metrics = self._telemetry_tracker.get_metrics()
            agent_data = metrics.get("by_agent", {}).get(agent_id, {})
            if agent_data:
                return agent_data

        # Fallback to TaskManager inspection if tracker not provided
        if self._task_manager:
            try:
                tasks = [t for t in self._task_manager.list_tasks() if t.assigned_agent == agent_id]
                if tasks:
                    completed = sum(1 for t in tasks if t.status.value in ("COMPLETED", "VERIFICATION_COMPLETE"))
                    return {
                        "tasks": len(tasks),
                        "success_rate": completed / len(tasks),
                        "avg_duration": 15.0,
                        "avg_tokens": 10000,
                    }
            except Exception:
                pass

        return {"tasks": 0, "success_rate": 1.0, "avg_duration": 10.0, "avg_tokens": 5000}

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def infer_complexity(self, text: str) -> Complexity:
        lower = text.lower()
        if any(w in lower for w in (
            "deep reasoning", "formal proof", "mission critical", "high risk", "complex rfc",
            "architect", "consensus", "byzantine", "formal verification", "cryptographic",
            "distributed protocol", "fault tolerance"
        )):
            return Complexity.REASONING
        if any(w in lower for w in (
            "refactor all", "restructure", "comprehensive", "full audit", "major rewrite",
            "across every", "multi-region", "migration"
        )):
            return Complexity.STRONG
        if any(w in lower for w in (
            "quick fix", "typo", "lint", "comment", "format", "minor tweak", "sanity", "fix typo"
        )):
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
        """Score every healthy agent against the task with multi-factor weighting."""
        complexity = complexity or self.infer_complexity(task_text)
        required = self.infer_capabilities(task_text)
        target_strength = COMPLEXITY_TARGET_STRENGTH.get(complexity, 3)

        scores: list[CandidateScore] = []
        for adapter in self._registry.list_active_adapters():
            breakdown: dict[str, float] = {}
            candidate = CandidateScore(
                agent_id=adapter.agent_id,
                account_id=adapter.account_id,
                provider=adapter.provider,
                score=0.0,
            )

            # 1. Keyword Affinity
            kw_score = 0.0
            for agent_id, pattern, explanation in AGENT_KEYWORDS:
                if agent_id == adapter.agent_id and pattern.search(task_text):
                    kw_score += KEYWORD_AFFINITY_WEIGHT
                    candidate.reasons.append(f"keyword affinity: {explanation}")
                    break
            breakdown["keyword_affinity"] = kw_score

            # 2. Capability Overlap
            declared = adapter.capabilities()
            overlap = required & declared
            cap_score = 0.0
            if overlap:
                cap_score = CAPABILITY_WEIGHT * len(overlap)
                candidate.matched_capabilities = sorted(c.value for c in overlap)
                candidate.reasons.append(
                    f"{len(overlap)}/{len(required)} required capabilities declared"
                )
            breakdown["capability_match"] = cap_score

            # 3. Model Strength Fit
            reachable = self._max_strength(adapter.agent_id)
            strength_score = 0.0
            if reachable >= target_strength:
                strength_score = STRENGTH_FIT_WEIGHT
                candidate.reasons.append(
                    f"model strength {reachable} >= required {target_strength}"
                )
            else:
                candidate.reasons.append(
                    f"model strength {reachable} < required {target_strength}"
                )
            breakdown["strength_fit"] = strength_score

            # 4. Reliability / Historical Success Rate
            metrics = self._get_agent_metrics(adapter.agent_id)
            succ_rate = metrics.get("success_rate", 1.0)
            rel_score = RELIABILITY_WEIGHT * succ_rate
            breakdown["reliability"] = rel_score
            if metrics.get("tasks", 0) > 0:
                candidate.reasons.append(f"reliability: {succ_rate*100:.0f}% success")

            # 5. Latency Score
            avg_duration = metrics.get("avg_duration", 15.0)
            lat_score = max(0.0, LATENCY_WEIGHT * (1.0 - min(avg_duration / 60.0, 1.0)))
            breakdown["latency"] = lat_score

            # 6. Token Efficiency Score
            avg_tokens = metrics.get("avg_tokens", 10000)
            tok_score = max(0.0, TOKEN_EFFICIENCY_WEIGHT * (1.0 - min(avg_tokens / 100000.0, 1.0)))
            breakdown["token_efficiency"] = tok_score

            # 7. Health Bonus
            healthy, _ = adapter.health()
            hlth_score = HEALTH_WEIGHT if healthy else 0.0
            breakdown["health"] = hlth_score

            # 8. Load Penalty
            load_pen = 0.0
            if self._busy(adapter):
                load_pen = LOAD_PENALTY
                candidate.reasons.append("currently WORKING; deprioritized")
            breakdown["load_penalty"] = -load_pen

            # Sum total score
            candidate.score = sum(breakdown.values())
            candidate.score_breakdown = breakdown
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
            decision = RoutingDecision(
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
            self._log_decision(task_text, decision)
            return decision

        # 2. Scored competition across every healthy agent.
        candidates = self.score_candidates(task_text, complexity)
        if not candidates:
            raise RuntimeError("No healthy agents available in ProviderRegistry")

        winner = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        model = preferred_model or select_model(winner.agent_id, complexity=complexity).preferred_model

        reason = "; ".join(winner.reasons) if winner.reasons else "highest scoring available agent"

        decision = RoutingDecision(
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
        self._log_decision(task_text, decision)
        return decision

    def _log_decision(self, task_text: str, decision: RoutingDecision) -> None:
        try:
            import datetime
            record = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "task_text": task_text[:120],
                "selected_agent": decision.agent_id,
                "selected_account": decision.account_id,
                "selected_model": decision.model,
                "complexity": decision.complexity.value,
                "reason": decision.reason,
                "fallback_agent": decision.fallback_agent_id,
                "candidates": decision.candidates,
            }
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, "a", encoding="utf-8") as f:
                import json
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    def get_routing_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent routing history records in reverse chronological order."""
        if not self._history_file.is_file():
            return []
        records: list[dict[str, Any]] = []
        try:
            import json
            with open(self._history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:
            return []
        return list(reversed(records))[:limit]
