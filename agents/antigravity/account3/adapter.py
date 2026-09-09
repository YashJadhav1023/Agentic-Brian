"""Antigravity Account 3 — third account, headless via profile `antigravity-account-yash`.

This class holds no execution logic and no CLI flags. It is a named handle onto
the account entry in `config/providers.json`.
"""
from __future__ import annotations

from pathlib import Path

from agents.antigravity.adapter import AntigravityAccountAdapter, AntigravityAdapter

AGENT_ID = "antigravity-account-3"


class AntigravityAccount3Adapter(AntigravityAccountAdapter):
    """Account 3 adapter loaded from the central provider configuration."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        super().__init__(AntigravityAdapter.get_account(AGENT_ID, config_path).config)
