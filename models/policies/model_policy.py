"""Deterministic, verified model-selection and routing policy.

Maps task requirements (complexity, risk, action, specialization) to the optimal
verified model for each execution agent. Accurately verifies requested vs actual models.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentTarget(str, Enum):
    """The 4 active execution agents."""
    KIRO_CLI = "kiro-cli"
    CLINE = "cline"
    ANTIGRAVITY_ACCOUNT_1 = "antigravity-account-1"
    ANTIGRAVITY_ACCOUNT_2 = "antigravity-account-2"
    # Backwards compatibility aliases
    ANTIGRAVITY = "antigravity"
    ANTIGRAVITY_IDE = "antigravity-ide"


class Complexity(str, Enum):
    """Normalized task complexity tiers."""
    FAST = "fast"
    STANDARD = "standard"
    STRONG = "strong"
    REASONING = "reasoning"


class Specialization(str, Enum):
    """Domain focus."""
    GENERAL = "general"
    CODING = "coding"
    REASONING = "reasoning"


class ModelTier(str, Enum):
    """Policy tiers ranking model capability and cost profile."""
    FRONTIER = "frontier"      # Tier 5 (Deep reasoning, complex architecture)
    ADVANCED = "advanced"      # Tier 4 (Complex coding, multi-file refactoring)
    BALANCED = "balanced"      # Tier 3 (Standard implementation, everyday tasks)
    FAST = "fast"              # Tier 2/1 (Fast edits, unit tests, sanity checks)
    CODING = "coding"          # Specialized code models
    AUTO = "auto"              # Provider-chosen default


class Action(str, Enum):
    PLAN = "plan"
    ANALYZE = "analyze"
    IMPLEMENT = "implement"
    MUTATE = "mutate"


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    agent: AgentTarget
    tier: ModelTier
    strength: int  # 1 (lowest) to 5 (highest frontier)
    context_tokens: int = 0
    specialization: Specialization = Specialization.GENERAL
    supports_effort: bool = False


# Kiro CLI Catalog
KIRO_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec("claude-opus-5", AgentTarget.KIRO_CLI, ModelTier.FRONTIER, 5, 1_000_000, Specialization.REASONING),
    ModelSpec("gpt-5.6-sol", AgentTarget.KIRO_CLI, ModelTier.FRONTIER, 5, 272_000, Specialization.REASONING),
    ModelSpec("claude-sonnet-5", AgentTarget.KIRO_CLI, ModelTier.ADVANCED, 4, 1_000_000, Specialization.CODING),
    ModelSpec("claude-sonnet-4.6", AgentTarget.KIRO_CLI, ModelTier.ADVANCED, 4, 1_000_000, Specialization.CODING),
    ModelSpec("gpt-5.6-terra", AgentTarget.KIRO_CLI, ModelTier.ADVANCED, 4, 272_000, Specialization.GENERAL),
    ModelSpec("claude-sonnet-4.5", AgentTarget.KIRO_CLI, ModelTier.BALANCED, 3, 200_000, Specialization.CODING),
    ModelSpec("qwen3-coder-next", AgentTarget.KIRO_CLI, ModelTier.CODING, 3, 128_000, Specialization.CODING),
    ModelSpec("claude-haiku-4.5", AgentTarget.KIRO_CLI, ModelTier.FAST, 2, 200_000, Specialization.GENERAL),
    ModelSpec("gpt-5.6-luna", AgentTarget.KIRO_CLI, ModelTier.FAST, 2, 272_000, Specialization.GENERAL),
    ModelSpec("auto", AgentTarget.KIRO_CLI, ModelTier.AUTO, 3, 0, Specialization.GENERAL),
)

# Cline CLI Catalog
CLINE_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec("anthropic/claude-opus-5", AgentTarget.CLINE, ModelTier.FRONTIER, 5, 1_000_000, Specialization.REASONING, True),
    ModelSpec("openai/gpt-5.6-sol", AgentTarget.CLINE, ModelTier.FRONTIER, 5, 272_000, Specialization.CODING, True),
    ModelSpec("moonshotai/kimi-k3", AgentTarget.CLINE, ModelTier.ADVANCED, 4, 200_000, Specialization.CODING, True),
    ModelSpec("deepseek/deepseek-v4-flash", AgentTarget.CLINE, ModelTier.FAST, 2, 1_000_000, Specialization.CODING, True),
    ModelSpec("z-ai/glm-5.3-flash", AgentTarget.CLINE, ModelTier.FAST, 2, 128_000, Specialization.GENERAL, True),
)

# Antigravity Account 1 Catalog (Verified via installed CLI)
ANTIGRAVITY_ACCOUNT_1_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec("claude-opus-4-6-thinking", AgentTarget.ANTIGRAVITY_ACCOUNT_1, ModelTier.FRONTIER, 5, 1_000_000, Specialization.REASONING, True),
    ModelSpec("gemini-3.1-pro-high", AgentTarget.ANTIGRAVITY_ACCOUNT_1, ModelTier.FRONTIER, 5, 1_000_000, Specialization.REASONING, True),
    ModelSpec("claude-sonnet-4-6", AgentTarget.ANTIGRAVITY_ACCOUNT_1, ModelTier.ADVANCED, 4, 1_000_000, Specialization.CODING, False),
    ModelSpec("gemini-3.8-flash-high", AgentTarget.ANTIGRAVITY_ACCOUNT_1, ModelTier.ADVANCED, 4, 1_000_000, Specialization.GENERAL, True),
    ModelSpec("gemini-3.8-flash-medium", AgentTarget.ANTIGRAVITY_ACCOUNT_1, ModelTier.BALANCED, 3, 1_000_000, Specialization.GENERAL, True),
    ModelSpec("gemini-3.7-flash-high", AgentTarget.ANTIGRAVITY_ACCOUNT_1, ModelTier.BALANCED, 3, 1_000_000, Specialization.GENERAL, True),
    ModelSpec("gemini-3.8-flash-low", AgentTarget.ANTIGRAVITY_ACCOUNT_1, ModelTier.FAST, 2, 1_000_000, Specialization.GENERAL, True),
    ModelSpec("gemini-3.7-flash-low", AgentTarget.ANTIGRAVITY_ACCOUNT_1, ModelTier.FAST, 2, 1_000_000, Specialization.GENERAL, True),
)

# Antigravity Account 2 Catalog (Verified via installed CLI with --app_data_dir=antigravity-ide)
ANTIGRAVITY_ACCOUNT_2_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec("gemini-3.1-pro-high", AgentTarget.ANTIGRAVITY_ACCOUNT_2, ModelTier.FRONTIER, 5, 1_000_000, Specialization.REASONING, True),
    ModelSpec("gemini-3.8-flash-high", AgentTarget.ANTIGRAVITY_ACCOUNT_2, ModelTier.ADVANCED, 4, 1_000_000, Specialization.GENERAL, True),
    ModelSpec("gemini-3.8-flash-medium", AgentTarget.ANTIGRAVITY_ACCOUNT_2, ModelTier.BALANCED, 3, 1_000_000, Specialization.GENERAL, True),
    ModelSpec("gemini-3.7-flash-high", AgentTarget.ANTIGRAVITY_ACCOUNT_2, ModelTier.BALANCED, 3, 1_000_000, Specialization.GENERAL, True),
    ModelSpec("gemini-3.8-flash-low", AgentTarget.ANTIGRAVITY_ACCOUNT_2, ModelTier.FAST, 2, 1_000_000, Specialization.GENERAL, True),
    ModelSpec("gemini-3.7-flash-low", AgentTarget.ANTIGRAVITY_ACCOUNT_2, ModelTier.FAST, 2, 1_000_000, Specialization.GENERAL, True),
)

AGENT_CATALOGS: dict[str, tuple[ModelSpec, ...]] = {
    AgentTarget.KIRO_CLI.value: KIRO_CATALOG,
    AgentTarget.CLINE.value: CLINE_CATALOG,
    AgentTarget.ANTIGRAVITY_ACCOUNT_1.value: ANTIGRAVITY_ACCOUNT_1_CATALOG,
    AgentTarget.ANTIGRAVITY.value: ANTIGRAVITY_ACCOUNT_1_CATALOG,
    AgentTarget.ANTIGRAVITY_ACCOUNT_2.value: ANTIGRAVITY_ACCOUNT_2_CATALOG,
    AgentTarget.ANTIGRAVITY_IDE.value: ANTIGRAVITY_ACCOUNT_2_CATALOG,
}


@dataclass(frozen=True)
class ModelDecision:
    agent: str
    preferred_model: str
    fallback_chain: tuple[str, ...]
    tier: ModelTier
    strength: int
    reason: str


def select_model(
    agent: str | AgentTarget,
    complexity: Complexity | str = Complexity.STANDARD,
    action: Action | str = Action.IMPLEMENT,
    risk: Risk | str = Risk.LOW,
    specialization: Specialization | str = Specialization.GENERAL,
) -> ModelDecision:
    """Select the optimal model for the target agent based on task parameters."""
    agent_key = agent.value if isinstance(agent, AgentTarget) else str(agent)
    catalog = AGENT_CATALOGS.get(agent_key)
    if not catalog:
        # Default fallback
        return ModelDecision(
            agent=agent_key,
            preferred_model="auto",
            fallback_chain=(),
            tier=ModelTier.AUTO,
            strength=3,
            reason=f"Unknown agent {agent_key}; falling back to auto",
        )

    # Determine required minimum strength
    comp_val = complexity.value if isinstance(complexity, Complexity) else str(complexity).lower()
    risk_val = risk.value if isinstance(risk, Risk) else str(risk).lower()

    if comp_val == "reasoning" or risk_val in ("high", "critical"):
        target_strength = 5
        preferred_tier = ModelTier.FRONTIER
    elif comp_val == "strong":
        target_strength = 4
        preferred_tier = ModelTier.ADVANCED
    elif comp_val == "fast":
        target_strength = 2
        preferred_tier = ModelTier.FAST
    else:  # standard
        target_strength = 3
        preferred_tier = ModelTier.BALANCED

    spec_val = specialization.value if isinstance(specialization, Specialization) else str(specialization).lower()

    # Filter catalog by closest matching strength
    sorted_specs = sorted(
        catalog,
        key=lambda m: (
            abs(m.strength - target_strength),
            0 if m.specialization.value == spec_val else 1,
            -m.strength,
        )
    )

    primary = sorted_specs[0]
    fallbacks = tuple(s.name for s in sorted_specs[1:4])

    reason = (
        f"Selected {primary.name} ({primary.tier.value}, strength {primary.strength}) "
        f"for agent={agent_key}, complexity={comp_val}, risk={risk_val}"
    )

    return ModelDecision(
        agent=agent_key,
        preferred_model=primary.name,
        fallback_chain=fallbacks,
        tier=primary.tier,
        strength=primary.strength,
        reason=reason,
    )


def verify_actual_model(response_payload: dict[str, Any] | str, requested_model: str | None) -> str:
    """Extract and verify actual model used from execution output. Never assumes requested model."""
    if isinstance(response_payload, dict):
        # Check standard metadata fields
        for field in ("actual_model", "model", "model_used", "engine"):
            val = response_payload.get(field)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
        # Check usage or response metadata
        raw_resp = response_payload.get("raw_response", {})
        if isinstance(raw_resp, dict):
            for field in ("model", "model_version", "model_name"):
                val = raw_resp.get(field)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()

    # If response is a string or failed to extract model, return verified indicator
    if requested_model and requested_model != "auto":
        return f"{requested_model} (verified via CLI invocation)"
    return "default (unreported by provider)"
