"""Central Provider and Agent Registry.

Manages all known agent execution providers and adapters. Providers and accounts
can be queried by the router, task runner, and UI without any hard-coded provider
assumptions in the brain core.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.base.adapter import AgentAdapter, AgentStatus, Capability, ExecutionMode


@dataclass
class Provider:
    """A high-level execution provider (e.g. Antigravity, Kiro, Cline)."""
    id: str
    name: str
    description: str
    enabled: bool = True
    adapters: dict[str, AgentAdapter] = field(default_factory=dict)

    def add_adapter(self, adapter: AgentAdapter) -> None:
        self.adapters[adapter.agent_id] = adapter

    def remove_adapter(self, agent_id: str) -> None:
        self.adapters.pop(agent_id, None)

    @property
    def capabilities(self) -> frozenset[Capability]:
        caps: set[Capability] = set()
        for adapter in self.adapters.values():
            caps.update(adapter.capabilities())
        return frozenset(caps)

    @property
    def models(self) -> list[str]:
        m: set[str] = set()
        for adapter in self.adapters.values():
            m.update(adapter.available_models())
        return sorted(m)

    def health(self) -> dict[str, Any]:
        results = {}
        all_healthy = True
        for agent_id, adapter in self.adapters.items():
            ok, reason = adapter.health()
            results[agent_id] = {"healthy": ok, "reason": reason}
            if not ok:
                all_healthy = False
        return {
            "provider": self.id,
            "healthy": all_healthy and len(self.adapters) > 0,
            "adapters": results,
        }


class ProviderRegistry:
    """Singleton/Registry pattern for dynamic provider management."""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}
        self._adapters: dict[str, AgentAdapter] = {}

    def register_provider(self, provider: Provider) -> None:
        self._providers[provider.id] = provider
        for agent_id, adapter in provider.adapters.items():
            self._adapters[agent_id] = adapter

    def register_adapter(self, provider_id: str, adapter: AgentAdapter) -> None:
        if provider_id not in self._providers:
            self._providers[provider_id] = Provider(
                id=provider_id,
                name=provider_id.capitalize(),
                description=f"Auto-registered provider {provider_id}",
            )
        self._providers[provider_id].add_adapter(adapter)
        self._adapters[adapter.agent_id] = adapter

    def get_adapter(self, agent_id: str) -> AgentAdapter | None:
        return self._adapters.get(agent_id)

    def get_provider(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id)

    def list_providers(self) -> list[Provider]:
        return list(self._providers.values())

    def list_active_adapters(self) -> list[AgentAdapter]:
        """Return adapters that belong to enabled providers and pass health checks."""
        active = []
        for provider in self._providers.values():
            if not provider.enabled:
                continue
            for adapter in provider.adapters.values():
                healthy, _ = adapter.health()
                if healthy:
                    active.append(adapter)
        return active

    def to_dict(self) -> dict[str, Any]:
        """Serialize provider and agent status for the Mission Control dashboard."""
        out = {}
        for p_id, provider in self._providers.items():
            out[p_id] = {
                "id": provider.id,
                "name": provider.name,
                "description": provider.description,
                "enabled": provider.enabled,
                "models": provider.models,
                "capabilities": [c.value for c in provider.capabilities],
                "accounts": {},
            }
            for a_id, adapter in provider.adapters.items():
                healthy, reason = adapter.health()
                out[p_id]["accounts"][a_id] = {
                    "agent_id": adapter.agent_id,
                    "account_id": adapter.account_id,
                    "provider": adapter.provider,
                    "execution_mode": adapter.execution_mode.value,
                    "healthy": healthy,
                    "health_reason": reason,
                    "capabilities": [c.value for c in adapter.capabilities()],
                    "models": list(adapter.available_models()),
                }
        return out
