"""Provider-independent Cost Tracking Model for Mission Control.

Calculates token costs for models with known pricing schedules.
For unknown models, cost is explicitly reported as unknown (None).
NEVER invents arbitrary pricing numbers.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("MissionControl.CostTracker")


class PricingType(str, Enum):
    FREE = "free"
    PAID = "paid"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


PricingTier = PricingType


@dataclass
class CostEstimate:
    input_cost: float | None = 0.0
    output_cost: float | None = 0.0
    total_cost: float = 0.0
    is_known: bool = True
    pricing_type: str = "paid"


@dataclass
class ModelPricing:
    """Pricing schedule for a specific model (per 1,000,000 tokens)."""
    model_id: str
    provider_id: str = ""
    input_cost_per_1m: float = 0.0
    output_cost_per_1m: float = 0.0
    pricing_type: PricingType = PricingType.PAID
    description: str = ""
    # Aliases for flexibility
    tier: Any = None
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0

    def __post_init__(self):
        if self.input_per_1m and not self.input_cost_per_1m:
            self.input_cost_per_1m = self.input_per_1m
        if self.output_per_1m and not self.output_cost_per_1m:
            self.output_cost_per_1m = self.output_per_1m
        if self.tier is not None:
            if isinstance(self.tier, PricingType):
                self.pricing_type = self.tier
            elif isinstance(self.tier, str):
                try:
                    self.pricing_type = PricingType(self.tier)
                except ValueError:
                    self.pricing_type = PricingType.PAID

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> dict[str, Any]:
        if self.pricing_type == PricingType.FREE:
            return {
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
                "pricing_type": PricingType.FREE.value,
                "is_known": True,
            }

        if self.pricing_type == PricingType.UNKNOWN:
            return {
                "input_cost": None,
                "output_cost": None,
                "total_cost": 0.0,
                "pricing_type": PricingType.UNKNOWN.value,
                "is_known": False,
            }

        in_cost = (input_tokens / 1_000_000.0) * self.input_cost_per_1m
        out_cost = (output_tokens / 1_000_000.0) * self.output_cost_per_1m
        return {
            "input_cost": round(in_cost, 6),
            "output_cost": round(out_cost, 6),
            "total_cost": round(in_cost + out_cost, 6),
            "pricing_type": self.pricing_type.value,
            "is_known": True,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "input_cost_per_1m": self.input_cost_per_1m,
            "output_cost_per_1m": self.output_cost_per_1m,
            "pricing_type": self.pricing_type.value,
            "description": self.description,
        }


#: Verified baseline pricing schedule for standard public models.
#: For unlisted models, pricing is UNKNOWN.
DEFAULT_PRICING_CATALOG: dict[str, ModelPricing] = {
    # Free / Local models
    "ollama/*": ModelPricing("ollama/*", "ollama", 0.0, 0.0, PricingType.FREE, "Ollama local execution"),
    "deepseek/deepseek-v4-flash": ModelPricing("deepseek/deepseek-v4-flash", "cline", 0.0, 0.0, PricingType.FREE, "Cline OAuth free usage billing ($0.00 cost)"),
    # OpenAI standard models (USD per 1M tokens)
    "gpt-4o": ModelPricing("gpt-4o", "openai", 2.50, 10.00, PricingType.PAID, "OpenAI GPT-4o"),
    "gpt-4o-mini": ModelPricing("gpt-4o-mini", "openai", 0.15, 0.60, PricingType.PAID, "OpenAI GPT-4o mini"),
    "gpt-4-turbo": ModelPricing("gpt-4-turbo", "openai", 10.00, 30.00, PricingType.PAID, "OpenAI GPT-4 Turbo"),
    "gpt-3.5-turbo": ModelPricing("gpt-3.5-turbo", "openai", 0.50, 1.50, PricingType.PAID, "OpenAI GPT-3.5 Turbo"),
    # Anthropic models
    "claude-3-5-sonnet-20241022": ModelPricing("claude-3-5-sonnet-20241022", "anthropic", 3.00, 15.00, PricingType.PAID, "Anthropic Claude 3.5 Sonnet"),
    "claude-3-5-sonnet": ModelPricing("claude-3-5-sonnet", "anthropic", 3.00, 15.00, PricingType.PAID, "Anthropic Claude 3.5 Sonnet alias"),
    "claude-3-haiku-20240307": ModelPricing("claude-3-haiku-20240307", "anthropic", 0.25, 1.25, PricingType.PAID, "Anthropic Claude 3 Haiku"),
    "claude-3-opus-20240229": ModelPricing("claude-3-opus-20240229", "anthropic", 15.00, 75.00, PricingType.PAID, "Anthropic Claude 3 Opus"),
    # Gemini models
    "gemini-1.5-flash": ModelPricing("gemini-1.5-flash", "gemini", 0.075, 0.30, PricingType.PAID, "Google Gemini 1.5 Flash"),
    "gemini-1.5-pro": ModelPricing("gemini-1.5-pro", "gemini", 1.25, 5.00, PricingType.PAID, "Google Gemini 1.5 Pro"),
    "gemini-2.0-flash": ModelPricing("gemini-2.0-flash", "gemini", 0.10, 0.40, PricingType.PAID, "Google Gemini 2.0 Flash"),
}


class CostTracker:
    """Provider-independent cost calculation and pricing schedule manager."""

    def __init__(self, config_file: Path | None = None) -> None:
        self._pricing: dict[str, ModelPricing] = dict(DEFAULT_PRICING_CATALOG)
        self._config_file = config_file or (Path(__file__).resolve().parents[2] / "config" / "pricing.json")
        self._load_custom_pricing()

    def _load_custom_pricing(self) -> None:
        if not self._config_file.is_file():
            return
        try:
            data = json.loads(self._config_file.read_text(encoding="utf-8"))
            for key, val in data.items():
                ptype = PricingType(val.get("pricing_type", "paid"))
                self._pricing[key] = ModelPricing(
                    model_id=key,
                    provider_id=val.get("provider_id", ""),
                    input_cost_per_1m=float(val.get("input_cost_per_1m", 0.0)),
                    output_cost_per_1m=float(val.get("output_cost_per_1m", 0.0)),
                    pricing_type=ptype,
                    description=val.get("description", ""),
                )
        except Exception as exc:
            logger.warning(f"Failed to load pricing config from {self._config_file}: {exc}")

    def save_custom_pricing(self) -> None:
        try:
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            serializable = {k: v.to_dict() for k, v in self._pricing.items()}
            self._config_file.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Failed to save pricing config to {self._config_file}: {exc}")

    def register_pricing(
        self,
        model_id: str | ModelPricing,
        input_cost_per_1m: float = 0.0,
        output_cost_per_1m: float = 0.0,
        provider_id: str = "",
        pricing_type: PricingType = PricingType.PAID,
        description: str = "",
    ) -> ModelPricing:
        if isinstance(model_id, ModelPricing):
            pricing = model_id
            self._pricing[pricing.model_id] = pricing
            self.save_custom_pricing()
            return pricing
        pricing = ModelPricing(
            model_id=model_id,
            provider_id=provider_id,
            input_cost_per_1m=input_cost_per_1m,
            output_cost_per_1m=output_cost_per_1m,
            pricing_type=pricing_type,
            description=description,
        )
        self._pricing[model_id] = pricing
        self.save_custom_pricing()
        return pricing

    def get_pricing(self, model_id: str, provider_id: str | None = None) -> ModelPricing:
        # 1. Exact match
        if model_id in self._pricing:
            return self._pricing[model_id]

        # 2. Match with provider prefix e.g. "ollama/llama3" -> "ollama/*"
        if provider_id == "ollama" or model_id.startswith("ollama/"):
            return self._pricing.get("ollama/*", ModelPricing(model_id, "ollama", 0.0, 0.0, PricingType.FREE))

        # 3. Match normalized model name (e.g. gpt-4o:latest -> gpt-4o)
        base_name = model_id.split(":")[0].strip()
        if base_name in self._pricing:
            return self._pricing[base_name]

        # 4. Unknown pricing - NEVER invent arbitrary prices
        return ModelPricing(
            model_id=model_id,
            provider_id=provider_id or "",
            pricing_type=PricingType.UNKNOWN,
            description="Pricing unknown for this model",
        )

    def calculate_cost(
        self,
        provider_id: str,
        model_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> dict[str, Any]:
        """Calculate exact cost or return unknown. Never invent numbers."""
        pricing = self.get_pricing(model_id=model_id, provider_id=provider_id)
        return pricing.calculate_cost(input_tokens=input_tokens, output_tokens=output_tokens)

    def estimate_cost(
        self,
        provider_id_or_model: str,
        model_id_or_tokens: Any = None,
        estimated_total_tokens: int | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        provider_id: str = "",
    ) -> Any:
        """Estimate cost supporting both (model_id, input_tokens=..., output_tokens=...) and (provider, model, total_tokens)."""
        # Form 1: (provider, model, total_tokens)
        if isinstance(model_id_or_tokens, (str,)) and estimated_total_tokens is not None:
            pricing = self.get_pricing(model_id=model_id_or_tokens, provider_id=provider_id_or_model)
            if pricing.pricing_type == PricingType.FREE:
                return 0.0
            if pricing.pricing_type == PricingType.UNKNOWN:
                return None
            in_tokens = int(estimated_total_tokens * 0.75)
            out_tokens = estimated_total_tokens - in_tokens
            res = pricing.calculate_cost(in_tokens, out_tokens)
            return res["total_cost"]

        # Form 2: (model_id, input_tokens=..., output_tokens=...)
        mid = provider_id_or_model
        pid = provider_id or (model_id_or_tokens if isinstance(model_id_or_tokens, str) else "")
        pricing = self.get_pricing(model_id=mid, provider_id=pid or None)
        res = pricing.calculate_cost(input_tokens, output_tokens)
        return CostEstimate(
            input_cost=res.get("input_cost"),
            output_cost=res.get("output_cost"),
            total_cost=res.get("total_cost") or 0.0,
            is_known=res.get("is_known", True),
            pricing_type=res.get("pricing_type", "paid")
        )

    def list_pricing_rules(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._pricing.values()]

    def list_pricing(self) -> list[dict[str, Any]]:
        return self.list_pricing_rules()


_GLOBAL_COST_TRACKER: CostTracker | None = None


def get_cost_tracker() -> CostTracker:
    global _GLOBAL_COST_TRACKER
    if _GLOBAL_COST_TRACKER is None:
        _GLOBAL_COST_TRACKER = CostTracker()
    return _GLOBAL_COST_TRACKER
