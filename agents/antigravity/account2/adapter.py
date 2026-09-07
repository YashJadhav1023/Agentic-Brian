"""Antigravity Account 2 — second account, headless via profile `antigravity-ide`.

Account 2 is a first-class headless agent. It does **not** require the
Antigravity IDE to be running, and nothing here opens or talks to the IDE; the
profile directory name is simply the account's isolation boundary.

This class holds no execution logic and no CLI flags. It is a named handle onto
the account entry in `config/providers.json`.
"""
from __future__ import annotations

from pathlib import Path

from agents.antigravity.adapter import AntigravityAccountAdapter, AntigravityAdapter

AGENT_ID = "antigravity-account-2"


class AntigravityAccount2Adapter(AntigravityAccountAdapter):
    """Account 2 adapter loaded from the central provider configuration."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        super().__init__(AntigravityAdapter.get_account(AGENT_ID, config_path).config)
