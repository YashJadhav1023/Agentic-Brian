"""Bootstrap the standard ProviderRegistry from config/providers.json.

Adding a provider means adding a config entry plus one ProviderAdapter. Nothing
in the brain core, router, task system, memory, handoff or UI changes, because
they all speak only to `AgentAdapter` and `ProviderRegistry`.

Registration lifecycle for a future provider:
    REGISTER -> AUTHENTICATE -> HEALTH CHECK -> DISCOVER MODELS
    -> DISCOVER CAPABILITIES -> REGISTER AGENT -> ROUTER -> MISSION CONTROL
"""
from __future__ import annotations

from pathlib import Path

from agents.antigravity.adapter import AntigravityAdapter
from agents.cline.adapter import ClineAdapter
from agents.kiro.adapter import KiroAdapter
from providers.registry.config import get_accounts, get_provider_meta
from providers.registry.provider_registry import Provider, ProviderRegistry

#: Providers whose adapter takes (executable, capabilities, models, default_model).
SIMPLE_CLI_ADAPTERS = {
    "kiro": KiroAdapter,
    "cline": ClineAdapter,
}


def create_default_registry(config_path: str | Path | None = None) -> ProviderRegistry:
    """Build the ProviderRegistry for every enabled provider in configuration."""
    reg = ProviderRegistry()
    path = str(config_path) if config_path else None

    # --- Antigravity: one provider, N isolated account profiles ----------
    antigravity_meta = get_provider_meta("antigravity", path)
    if antigravity_meta.get("enabled", True):
        provider = Provider(
            id="antigravity",
            name=antigravity_meta.get("name", "Google Antigravity"),
            description=antigravity_meta.get("description", ""),
            enabled=True,
        )
        for adapter in AntigravityAdapter.load_from_config(config_path):
            provider.add_adapter(adapter)
        reg.register_provider(provider)

    # --- Single-account CLI providers -------------------------------------
    for provider_id, adapter_cls in SIMPLE_CLI_ADAPTERS.items():
        meta = get_provider_meta(provider_id, path)
        if not meta or not meta.get("enabled", True):
            continue
        provider = Provider(
            id=provider_id,
            name=meta.get("name", provider_id.capitalize()),
            description=meta.get("description", ""),
            enabled=True,
        )
        for account in get_accounts(provider_id, path):
            if not account.enabled:
                continue
            provider.add_adapter(
                adapter_cls(
                    executable=account.command,
                    capabilities=account.capabilities or None,
                    models=account.models or None,
                    default_model=account.default_model,
                )
            )
        if provider.adapters:
            reg.register_provider(provider)

    return reg
