"""Universal AI Provider Abstraction for Mission Control.

Provides a unified interface for both IDE/Agent providers (Antigravity, Kiro, Cline,
OpenHands) and direct API providers (OpenAI, Gemini, Anthropic, OpenRouter, Azure,
Bedrock, Ollama, and any OpenAI-compatible endpoint).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from agents.base.adapter import Capability, TaskExecutionResult


class ProviderType(str, Enum):
    """Categorization of AI providers."""
    AGENT = "agent"
    IDE = "ide"
    API = "api"
    LOCAL_MODEL = "local_model"
    GATEWAY = "gateway"


@dataclass
class UsageStats:
    """Usage metrics reported or tracked for an account/provider."""
    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_count": self.request_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass
class RateLimitInfo:
    """Rate limit and quota state reported by provider headers or telemetry."""
    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None
    requests_remaining: int | None = None
    tokens_remaining: int | None = None
    reset_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests_per_minute": self.requests_per_minute,
            "tokens_per_minute": self.tokens_per_minute,
            "requests_remaining": self.requests_remaining,
            "tokens_remaining": self.tokens_remaining,
            "reset_at": self.reset_at,
        }


class AIProvider(abc.ABC):
    """Universal contract implemented by all Mission Control AI providers."""

    @property
    @abc.abstractmethod
    def provider_id(self) -> str:
        """Unique identifier of the provider (e.g. 'openai', 'gemini', 'antigravity')."""
        ...

    @property
    @abc.abstractmethod
    def provider_type(self) -> ProviderType:
        """Categorization of this provider."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable display name."""
        return self.provider_id.replace("-", " ").title()

    @abc.abstractmethod
    def capabilities(self) -> frozenset[Capability]:
        """Declared capabilities supported across this provider's deployments."""
        ...

    @abc.abstractmethod
    def health(self) -> tuple[bool, str]:
        """Verify connectivity, credential validity, and service availability.
        
        Returns (healthy, diagnostic_reason). Never exposes raw secrets.
        """
        ...

    @abc.abstractmethod
    def list_models(self) -> list[Any]:
        """Return list of available ModelMetadata objects."""
        ...

    @abc.abstractmethod
    def execute(self, job: Any) -> TaskExecutionResult:
        """Execute a single-shot completion or task."""
        ...

    def execute_stream(
        self, job: Any, on_event: Callable[[dict[str, Any]], None] | None = None
    ) -> TaskExecutionResult:
        """Execute a streaming completion, falling back to execute if unsupported."""
        return self.execute(job)

    @abc.abstractmethod
    def validate_config(self) -> tuple[bool, list[str]]:
        """Validate configuration parameters without performing network requests.
        
        Returns (is_valid, list_of_error_messages).
        """
        ...

    def get_usage(self, account_id: str | None = None) -> UsageStats | str:
        """Return collected usage stats or 'NOT_SUPPORTED'."""
        return "NOT_SUPPORTED"

    def get_limits(self, account_id: str | None = None) -> RateLimitInfo | str:
        """Return known rate limit and quota state or 'NOT_SUPPORTED'."""
        return "NOT_SUPPORTED"
