"""Central Provider, Agent, Account, and Model Registry for Mission Control.

Manages all known agent execution providers, direct API providers, accounts,
and models. Providers, accounts, and models can be queried by the router,
task runner, CLI, and Mission Control UI without hard-coded assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.base.adapter import AgentAdapter, Capability
from providers.base import AIProvider
from providers.registry.account_registry import AccountRegistry
from providers.registry.credential_manager import CredentialManager, get_credential_manager
from providers.registry.model_registry import ModelRegistry


@dataclass
class Provider:
    """A high-level execution provider (e.g. Antigravity, Kiro, Cline, OpenAI)."""
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
    """Universal Registry pattern for dynamic provider and account management."""

    def __init__(
        self,
        account_registry: AccountRegistry | None = None,
        model_registry: ModelRegistry | None = None,
        credential_manager: CredentialManager | None = None,
    ) -> None:
        self._providers: dict[str, Provider] = {}
        self._adapters: dict[str, AgentAdapter] = {}
        self._ai_providers: dict[str, AIProvider] = {}
        self.account_registry = account_registry or AccountRegistry()
        self.model_registry = model_registry or ModelRegistry()
        self.credential_manager = credential_manager or get_credential_manager()

    @property
    def accounts(self) -> AccountRegistry:
        return self.account_registry

    @property
    def models(self) -> ModelRegistry:
        return self.model_registry

    # --- AgentAdapter interface (Phase 5-8 compatibility) ------------------
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

    def discover_models(self, provider_id: str) -> list[str]:
        """Discover available models across registered adapters for a provider."""
        provider = self.get_provider(provider_id)
        if provider and provider.models:
            return provider.models
        ai_prov = self._ai_providers.get(provider_id)
        if ai_prov:
            return [m.model_id for m in ai_prov.list_models()]
        return []

    def discover_capabilities(self, provider_id: str) -> list[str]:
        """Discover declared capabilities across registered adapters for a provider."""
        provider = self.get_provider(provider_id)
        if provider:
            return [c.value for c in provider.capabilities]
        ai_prov = self._ai_providers.get(provider_id)
        if ai_prov:
            return [c.value for c in ai_prov.capabilities()]
        return []

    def authenticate(self, provider_id: str) -> bool:
        """Verify authentication/usability for all adapters under a provider."""
        provider = self.get_provider(provider_id)
        if provider:
            health = provider.health()
            return health.get("healthy", False)
        ai_prov = self._ai_providers.get(provider_id)
        if ai_prov:
            ok, _ = ai_prov.health()
            return ok
        return False

    # --- AIProvider interface (Phase 9 Universal Orchestration) ------------
    def register_ai_provider(self, provider: AIProvider) -> None:
        """Register a universal AIProvider and index its models into the model registry."""
        self._ai_providers[provider.provider_id] = provider
        for model_meta in provider.list_models():
            self.model_registry.register_model(model_meta)

    def get_ai_provider(self, provider_id: str) -> AIProvider | None:
        return self._ai_providers.get(provider_id)

    def list_ai_providers(self) -> list[AIProvider]:
        return list(self._ai_providers.values())

    def to_dict(self) -> dict[str, Any]:
        """Serialize provider and agent status for Mission Control."""
        out = {}
        try:
            from providers.registry.config import load_config
            cfg_providers = load_config().get("providers", {})
        except Exception:
            cfg_providers = {}

        for p_id, provider in self._providers.items():
            meta = cfg_providers.get(p_id, {})
            out[p_id] = {
                "id": provider.id,
                "name": provider.name,
                "description": provider.description,
                "enabled": provider.enabled,
                "models": provider.models,
                "capabilities": [c.value for c in provider.capabilities],
                "model_capabilities": meta.get("model_capabilities", []),
                "accounts": {},
            }
            for a_id, adapter in provider.adapters.items():
                described = adapter.describe()
                health = described.pop("health", {})
                described.update(
                    {
                        "healthy": health.get("healthy", False),
                        "health_reason": health.get("reason", "unknown"),
                    }
                )
                out[p_id]["accounts"][a_id] = described

        # Also add direct AIProviders
        for p_id, ai_prov in self._ai_providers.items():
            if p_id not in out:
                ok, reason = ai_prov.health()
                meta = cfg_providers.get(p_id, {})
                out[p_id] = {
                    "id": ai_prov.provider_id,
                    "name": ai_prov.display_name,
                    "description": meta.get("description", f"{ai_prov.display_name} API"),
                    "enabled": meta.get("enabled", True),
                    "type": ai_prov.provider_type.value,
                    "models": [m.model_id for m in ai_prov.list_models()],
                    "capabilities": [c.value for c in ai_prov.capabilities()],
                    "model_capabilities": meta.get("model_capabilities", []),
                    "accounts": {},
                    "healthy": ok,
                    "health_reason": reason,
                }
                for acc in self.account_registry.list_accounts(p_id):
                    out[p_id]["accounts"][acc.id] = acc.to_dict()

        return out
