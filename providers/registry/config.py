"""Provider configuration loader.

`config/providers.json` is the single source of truth for provider, account,
execution and model configuration. Every adapter reads its command, profile,
capabilities and model catalogue from here, so no CLI flag, profile path or
model id is hard-coded in Python.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from agents.base.adapter import Capability

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "providers.json"


@dataclass(frozen=True)
class AccountConfigView:
    """Read-only view of one account entry, provider-neutral."""

    agent_id: str
    account_id: str
    provider_id: str
    enabled: bool
    command: str
    capabilities: frozenset[Capability]
    models: tuple[str, ...]
    default_model: str
    raw: dict[str, Any]


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict[str, Any]:
    target = Path(path) if path else CONFIG_PATH
    if not target.is_file():
        raise FileNotFoundError(f"Provider configuration not found: {target}")
    return json.loads(target.read_text(encoding="utf-8"))


def _to_capabilities(values: list[str]) -> frozenset[Capability]:
    resolved = set()
    for value in values:
        try:
            resolved.add(Capability(value))
        except ValueError:
            # An unknown capability in config must not crash the registry; it is
            # simply not routable until the Capability enum learns about it.
            continue
    return frozenset(resolved)


def get_accounts(provider_id: str, path: str | None = None) -> list[AccountConfigView]:
    provider = load_config(path).get("providers", {}).get(provider_id, {})
    if not provider:
        return []
    provider_command = provider.get("command", provider_id)
    views: list[AccountConfigView] = []
    for agent_key, account in provider.get("accounts", {}).items():
        execution = account.get("execution", {})
        views.append(
            AccountConfigView(
                agent_id=account.get("agent_id", agent_key),
                account_id=account.get("account_id", "cli"),
                provider_id=provider_id,
                enabled=bool(account.get("enabled", True)) and bool(provider.get("enabled", True)),
                command=execution.get("command") or provider_command,
                capabilities=_to_capabilities(account.get("capabilities", [])),
                models=tuple(account.get("models", ())),
                default_model=account.get("default_model", "auto"),
                raw=account,
            )
        )
    return views


def get_account(provider_id: str, agent_id: str, path: str | None = None) -> AccountConfigView | None:
    for view in get_accounts(provider_id, path):
        if view.agent_id == agent_id:
            return view
    return None


def get_provider_meta(provider_id: str, path: str | None = None) -> dict[str, Any]:
    return load_config(path).get("providers", {}).get(provider_id, {})


def max_concurrent_agents(path: str | None = None) -> int:
    """Bounded concurrency for this host, from config."""
    return int(load_config(path).get("concurrency", {}).get("max_concurrent_agents", 2))
