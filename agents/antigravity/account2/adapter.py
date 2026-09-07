"""Antigravity Account 2 Adapter (IDE Profile)."""
from __future__ import annotations

from agents.antigravity.base_adapter import AntigravityBaseAdapter
from agents.base.adapter import Capability


class AntigravityAccount2Adapter(AntigravityBaseAdapter):
    """Headless Antigravity Account 2 (IDE Session Profile).

    Directly leverages the authenticated session in ~/.gemini/antigravity-ide/
    via the official CLI's --app_data_dir parameter, providing fully autonomous,
    headless task execution without requiring manual interaction with the IDE.
    """

    def __init__(self, data_dir_name: str = "antigravity-ide") -> None:
        super().__init__(data_dir_name=data_dir_name)

    @property
    def agent_id(self) -> str:
        return "antigravity-account-2"

    @property
    def provider(self) -> str:
        return "antigravity"

    @property
    def account_id(self) -> str:
        return "account-2"

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({
            Capability.EDITOR_REFACTORING,
            Capability.COMPONENT_REFACTORING,
            Capability.ARCHITECTURE,
            Capability.DEEP_REASONING,
            Capability.CODE_REVIEW,
        })
