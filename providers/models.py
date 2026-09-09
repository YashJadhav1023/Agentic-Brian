"""Mission Control Model Management Interface.

Provides ModelInfo and ModelRegistry for model registration, capability tagging,
priority ordering, and status management.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ModelInfo:
    """Detailed model metadata and operational parameters."""
    model_id: str
    provider_id: str
    account_id: str | None = None
    display_name: str = ""
    context_window: int = 128_000
    capabilities: list[str] = field(default_factory=list)
    input_cost_per_1m: float = 0.0
    output_cost_per_1m: float = 0.0
    priority: int = 10
    enabled: bool = True
    status: str = "available"  # available, experimental, deprecated
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.model_id
        if isinstance(self.capabilities, (set, frozenset, tuple)):
            self.capabilities = list(self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelRegistry:
    """Registry maintaining active and configured models."""

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}

    def register_model(self, model: ModelInfo) -> None:
        """Register or update a model."""
        self._models[model.model_id] = model

    def get_model(self, model_id: str, provider_id: str | None = None) -> ModelInfo | None:
        """Get model by model_id, optionally matching provider_id."""
        if provider_id:
            for m in self._models.values():
                if m.model_id == model_id and m.provider_id == provider_id:
                    return m
        return self._models.get(model_id)

    def list_models(
        self,
        provider_id: str | None = None,
        capability: str | None = None,
        enabled_only: bool = False,
    ) -> list[ModelInfo]:
        """List registered models matching filters."""
        results = []
        for m in self._models.values():
            if provider_id and m.provider_id != provider_id:
                continue
            if enabled_only and not m.enabled:
                continue
            if capability and capability not in m.capabilities:
                continue
            results.append(m)
        results.sort(key=lambda m: (-m.priority, m.provider_id, m.model_id))
        return results

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        """Grouped models dictionary by provider."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for m in self._models.values():
            grouped.setdefault(m.provider_id, []).append(m.to_dict())
        return grouped
