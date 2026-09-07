"""Antigravity Account 1 — signed-in Google account, profile `antigravity-cli`.

This class holds no execution logic and no CLI flags. It is a named handle onto
the account entry in `config/providers.json`.
"""
from __future__ import annotations

from pathlib import Path

from agents.antigravity.adapter import AntigravityAccountAdapter, AntigravityAdapter

AGENT_ID = "antigravity-account-1"


class AntigravityAccount1Adapter(AntigravityAccountAdapter):
    """Account 1 adapter loaded from the central provider configuration."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        super().__init__(AntigravityAdapter.get_account(AGENT_ID, config_path).config)
