"""Antigravity Account 1 Adapter (CLI Profile)."""
from __future__ import annotations

from agents.antigravity.base_adapter import AntigravityBaseAdapter
from agents.base.adapter import Capability


class AntigravityAccount1Adapter(AntigravityBaseAdapter):
    """Headless Antigravity Account 1 (CLI OAuth Session)."""

    def __init__(self, data_dir_name: str = "antigravity-cli") -> None:
        super().__init__(data_dir_name=data_dir_name)

    @property
    def agent_id(self) -> str:
        return "antigravity-account-1"

    @property
    def provider(self) -> str:
        return "antigravity"

    @property
    def account_id(self) -> str:
        return "account-1"

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({
            Capability.ARCHITECTURE,
            Capability.PROTOCOL_DESIGN,
            Capability.GOVERNANCE,
            Capability.DEEP_REASONING,
        })
