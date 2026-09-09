"""Central Model Registry for Mission Control.

Tracks model metadata, context window tokens, capabilities, cost estimates,
and availability across all connected Agent and API providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelMetadata:
    """Metadata describing a specific model on a provider."""
    model_id: str
    provider_id: str
    account_id: str | None = None
    display_name: str = ""
    capabilities: frozenset[str] = frozenset()
    context_window: int = 128_000
    input_cost_per_1m: float = 0.0
    output_cost_per_1m: float = 0.0
    enabled: bool = True
    status: str = "available"  # available, experimental, deprecated
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "account_id": self.account_id,
            "display_name": self.display_name or self.model_id,
            "capabilities": sorted(list(self.capabilities)),
            "context_window": self.context_window,
            "input_cost_per_1m": self.input_cost_per_1m,
            "output_cost_per_1m": self.output_cost_per_1m,
            "enabled": self.enabled,
            "status": self.status,
            "supports_streaming": self.supports_streaming,
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
        }


class ModelRegistry:
    """Central registry maintaining verified and discovered models across providers."""

    def __init__(self) -> None:
        self._models: dict[str, ModelMetadata] = {}

    def _key(self, model_id: str, provider_id: str | None = None) -> str:
        if provider_id:
            return f"{provider_id}::{model_id}"
        return model_id

    def register_model(self, model: ModelMetadata) -> None:
        self._models[f"{model.provider_id}::{model.model_id}"] = model

    def get_model(self, model_id: str, provider_id: str | None = None) -> ModelMetadata | None:
        if provider_id:
            return self._models.get(f"{provider_id}::{model_id}")
        # Search across all providers for matching model_id
        for key, m in self._models.items():
            if m.model_id == model_id:
                return m
        return None

    def list_models(
        self,
        provider_id: str | None = None,
        capability: str | None = None,
        enabled_only: bool = True,
    ) -> list[ModelMetadata]:
        out: list[ModelMetadata] = []
        for m in self._models.values():
            if provider_id and m.provider_id != provider_id:
                continue
            if enabled_only and not m.enabled:
                continue
            if capability and capability not in m.capabilities:
                continue
            out.append(m)
        out.sort(key=lambda m: (m.provider_id, m.model_id))
        return out

    def to_dict(self) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for m in self._models.values():
            grouped.setdefault(m.provider_id, []).append(m.to_dict())
        return grouped
