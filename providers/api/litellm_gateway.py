"""LiteLLM Gateway Provider for Mission Control.

Acts as an optional gateway adapter connecting Mission Control to a LiteLLM proxy
server (e.g. http://localhost:4000/v1), enabling unified access to 100+ LLMs
via virtual keys, budgets, and enterprise load-balancing.
"""
from __future__ import annotations

from providers.api.openai_compatible import OpenAICompatibleProvider
from providers.base import ProviderType


class LiteLLMGatewayProvider(OpenAICompatibleProvider):
    """Optional Gateway provider routing requests through a LiteLLM proxy."""

    def __init__(
        self,
        provider_id: str = "litellm",
        base_url: str = "http://localhost:4000/v1",
        credential_reference: str = "",
        default_model: str = "gpt-4o",
        models: tuple[str, ...] = ("gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"),
        timeout: int = 60,
    ) -> None:
        super().__init__(
            provider_id=provider_id,
            base_url=base_url,
            credential_reference=credential_reference,
            default_model=default_model,
            models=models,
            timeout=timeout,
            display_name="LiteLLM Gateway",
        )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GATEWAY
