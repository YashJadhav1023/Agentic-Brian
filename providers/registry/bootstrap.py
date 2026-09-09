"""Bootstrap the Universal ProviderRegistry for Mission Control.

Registers both Agent/IDE providers (Antigravity, Kiro, Cline, OpenHands) and
direct API providers (OpenAI, Gemini, Anthropic, OpenRouter, LiteLLM, and
generic OpenAI-compatible services) into a unified orchestration layer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.antigravity.adapter import AntigravityAdapter
from agents.cline.adapter import ClineAdapter
from agents.kiro.adapter import KiroAdapter
from agents.openhands.adapter import OpenHandsAdapter, OpenHandsProvider
from providers.adapters.bridge import AgentProviderBridge
from providers.api.anthropic_native import AnthropicProvider
from providers.api.gemini_native import GeminiProvider
from providers.api.litellm_gateway import LiteLLMGatewayProvider
from providers.api.ollama_local import OllamaProvider
from providers.api.openai_compatible import OpenAICompatibleProvider
from providers.registry.account_registry import Account, AccountStatus, AuthenticationType
from providers.registry.config import get_accounts, get_provider_meta, load_config
from providers.registry.model_registry import ModelMetadata
from providers.registry.provider_registry import Provider, ProviderRegistry

#: Standard CLI agent adapter classes
SIMPLE_CLI_ADAPTERS = {
    "kiro": KiroAdapter,
    "cline": ClineAdapter,
}


def create_default_registry(config_path: str | Path | None = None) -> ProviderRegistry:
    """Build the ProviderRegistry for every enabled provider in configuration."""
    reg = ProviderRegistry()
    path = str(config_path) if config_path else None
    cfg = load_config(path)
    all_providers_cfg = cfg.get("providers", {})

    # --- 1. Antigravity: One provider, N isolated account profiles ----------
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
            # Universal AIProvider bridge
            bridge = AgentProviderBridge(adapter)
            reg.register_ai_provider(bridge)
            # Register first-class account
            reg.account_registry.register_account(
                Account(
                    id=adapter.agent_id,
                    provider_id="antigravity",
                    account_name=adapter.account_id,
                    account_type="ide",
                    authentication_type=AuthenticationType.OAUTH,
                    credential_reference="",  # Uses isolated local token file
                    status=AccountStatus.ONLINE if adapter.health()[0] else AccountStatus.OFFLINE,
                    enabled=True,
                    models=list(adapter.available_models()),
                    capabilities=sorted(list(c.value for c in adapter.capabilities())),
                    metadata={"data_dir": adapter.data_dir},
                )
            )
        # Register any configured accounts that are disabled in config
        for acct_key, acct_conf in antigravity_meta.get("accounts", {}).items():
            aid = acct_conf.get("agent_id", acct_key)
            if not reg.account_registry.get_account(aid):
                reg.account_registry.register_account(
                    Account(
                        id=aid,
                        provider_id="antigravity",
                        account_name=acct_conf.get("account_id", acct_key),
                        account_type="ide",
                        authentication_type=AuthenticationType.OAUTH,
                        credential_reference="",
                        status=AccountStatus.OFFLINE,
                        enabled=False,
                        models=list(acct_conf.get("models", [])),
                        capabilities=list(acct_conf.get("capabilities", [])),
                        metadata={"data_dir": acct_conf.get("execution", {}).get("app_data_dir", "")},
                    )
                )
        reg.register_provider(provider)

    # --- 2. Single-account CLI providers (Kiro, Cline) ----------------------
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
            kwargs = {
                "executable": account.command,
                "capabilities": account.capabilities or None,
                "models": account.models or None,
                "default_model": account.default_model,
            }
            if provider_id == "cline":
                kwargs["agent_id"] = account.agent_id
                kwargs["account_id"] = account.account_id
                kwargs["config_dir"] = account.raw.get("config_dir")
                kwargs["data_dir"] = account.raw.get("data_dir")
            adapter = adapter_cls(**kwargs)
            provider.add_adapter(adapter)
            # Universal AIProvider bridge
            bridge = AgentProviderBridge(adapter)
            reg.register_ai_provider(bridge)
            # Register first-class account
            reg.account_registry.register_account(
                Account(
                    id=account.agent_id,
                    provider_id=provider_id,
                    account_name=account.account_id,
                    account_type="agent",
                    authentication_type=AuthenticationType.LOCAL,
                    credential_reference="",
                    status=AccountStatus.ONLINE if adapter.health()[0] else AccountStatus.OFFLINE,
                    enabled=account.enabled,
                    models=list(adapter.available_models()),
                    capabilities=sorted(list(c.value for c in adapter.capabilities())),
                    metadata={
                        "config_dir": account.raw.get("config_dir"),
                        "data_dir": account.raw.get("data_dir"),
                    }
                )
            )
        if provider.adapters:
            reg.register_provider(provider)

    # --- 3. OpenHands agent integration -------------------------------------
    openhands_meta = get_provider_meta("openhands", path)
    if openhands_meta and openhands_meta.get("enabled", False):
        oh_adapter = OpenHandsAdapter(
            agent_id="openhands",
            server_url=openhands_meta.get("server_url", "http://localhost:3000/api"),
            credential_reference=openhands_meta.get("credential_ref", ""),
        )
        oh_provider = Provider(
            id="openhands",
            name="OpenHands",
            description=openhands_meta.get("description", "OpenHands Autonomous Agent Platform"),
            enabled=True,
        )
        oh_provider.add_adapter(oh_adapter)
        reg.register_provider(oh_provider)
        reg.register_ai_provider(OpenHandsProvider(oh_adapter))
        reg.account_registry.register_account(
            Account(
                id="openhands",
                provider_id="openhands",
                account_name="main",
                account_type="agent",
                authentication_type=AuthenticationType.API_KEY,
                credential_reference=openhands_meta.get("credential_ref", ""),
                status=AccountStatus.ONLINE,
                enabled=True,
            )
        )

    # --- 4. Direct API Providers & OpenAI-Compatible Gateways --------------
    for p_id, p_conf in all_providers_cfg.items():
        if p_id in ("antigravity", "kiro", "cline", "openhands"):
            continue
        if not p_conf.get("enabled", True):
            continue

        prov_type = p_conf.get("type", "openai-compatible")
        base_url = p_conf.get("base_url", "https://api.openai.com/v1")
        accounts_map = p_conf.get("accounts", {})

        # Default model and models catalog
        models = p_conf.get("models", [])
        default_model = p_conf.get("default_model", models[0] if models else "default")

        # Instantiate specific or generic provider
        ai_provider = None
        if p_id == "gemini-api" or prov_type == "gemini":
            cred_ref = p_conf.get("credential_ref", "")
            ai_provider = GeminiProvider(
                provider_id=p_id,
                default_model=default_model,
            )
        elif p_id == "anthropic" or prov_type == "anthropic":
            cred_ref = p_conf.get("credential_ref", "")
            ai_provider = AnthropicProvider(
                provider_id=p_id,
                default_model=default_model,
            )
        elif prov_type == "litellm" or p_id == "litellm":
            cred_ref = p_conf.get("credential_ref", "")
            ai_provider = LiteLLMGatewayProvider(
                provider_id=p_id,
                base_url=base_url,
                credential_reference=cred_ref,
                default_model=default_model,
                models=tuple(models) if models else ("gpt-4o", "claude-3-5-sonnet"),
            )
        elif prov_type in ("ollama", "local", "local-model") or p_id == "ollama":
            # Local model server. Credential is optional: Ollama is
            # unauthenticated by default, and provider_type stays LOCAL_MODEL
            # even if the daemon is reachable on a non-loopback host.
            cred_ref = p_conf.get("credential_ref", "")
            ai_provider = OllamaProvider(
                provider_id=p_id,
                base_url=base_url if base_url != "https://api.openai.com/v1" else "http://localhost:11434/v1",
                default_model=default_model if default_model != "default" else "llama3.2",
            )
        else:
            # Generic OpenAI-compatible endpoint (OpenRouter, vLLM, LM Studio, custom)
            cred_ref = p_conf.get("credential_ref", "")
            ai_provider = OpenAICompatibleProvider(
                provider_id=p_id,
                base_url=base_url,
                default_model=default_model,
            )

        if ai_provider:
            reg.register_ai_provider(ai_provider)

            # Register accounts under this provider
            if accounts_map:
                for a_key, a_val in accounts_map.items():
                    a_ref = a_val.get("credential_reference") or a_val.get("credential_ref") or cred_ref
                    acc_id = a_key if a_key.startswith(f"{p_id}-") or a_key == p_id else f"{p_id}-{a_key}"
                    acc_name = a_val.get("account_id") or a_key.removeprefix(f"{p_id}-")
                    reg.account_registry.register_account(
                        Account(
                            id=acc_id,
                            provider_id=p_id,
                            account_name=acc_name,
                            account_type="api",
                            authentication_type=AuthenticationType(a_val.get("authentication_type", a_val.get("auth_type", "api_key"))),
                            credential_reference=a_ref,
                            status=AccountStatus.ONLINE if a_val.get("enabled", True) else AccountStatus.OFFLINE,
                            enabled=a_val.get("enabled", True),
                            models=list(a_val.get("models", models)),
                            capabilities=list(a_val.get("capabilities", [])),
                            concurrency_limit=int(a_val.get("concurrency_limit", 2)),
                        )
                    )
            elif cred_ref:
                # Register a default account if credential reference is defined at provider level
                reg.account_registry.register_account(
                    Account(
                        id=f"{p_id}-main",
                        provider_id=p_id,
                        account_name="main",
                        account_type="api",
                        authentication_type=AuthenticationType.API_KEY,
                        credential_reference=cred_ref,
                        status=AccountStatus.ONLINE,
                        enabled=True,
                    )
                )

    return reg
