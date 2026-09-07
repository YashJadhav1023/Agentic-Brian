"""Bootstrap the standard ProviderRegistry with active system agents."""
from __future__ import annotations

from agents.antigravity.account1.adapter import AntigravityAccount1Adapter
from agents.antigravity.account2.adapter import AntigravityAccount2Adapter
from agents.cline.adapter import ClineAdapter
from agents.kiro.adapter import KiroAdapter
from providers.registry.provider_registry import Provider, ProviderRegistry


def create_default_registry() -> ProviderRegistry:
    """Build and return the ProviderRegistry with the 4 active execution resources:
    - Kiro CLI
    - Cline CLI
    - Antigravity Account 1 (CLI)
    - Antigravity Account 2 (IDE)
    """
    reg = ProviderRegistry()

    # Provider: Antigravity (2 accounts: Account 1 & Account 2)
    antigravity_provider = Provider(
        id="antigravity",
        name="Google Antigravity",
        description="Local Antigravity autonomous multi-agent accounts",
    )
    antigravity_provider.add_adapter(AntigravityAccount1Adapter())
    antigravity_provider.add_adapter(AntigravityAccount2Adapter())
    reg.register_provider(antigravity_provider)

    # Provider: Kiro CLI
    kiro_provider = Provider(
        id="kiro",
        name="Kiro",
        description="Headless Kiro command line execution agent",
    )
    kiro_provider.add_adapter(KiroAdapter())
    reg.register_provider(kiro_provider)

    # Provider: Cline CLI
    cline_provider = Provider(
        id="cline",
        name="Cline",
        description="Headless Cline developer coding agent",
    )
    cline_provider.add_adapter(ClineAdapter())
    reg.register_provider(cline_provider)

    return reg
