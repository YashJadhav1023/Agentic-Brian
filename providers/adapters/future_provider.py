"""Extensible Template for Future API Providers (e.g. Gemini API, OpenAI API, Anthropic API).

This module illustrates how a new provider is added into the system without touching
the core orchestrator or router. When credentials and an adapter are configured,
the ProviderRegistry registers the agent, models, and capabilities dynamically.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agents.base.adapter import AgentAdapter, Capability, ExecutionMode, TaskExecutionResult


class GenericAPIAdapter(AgentAdapter):
    """Generic adapter for remote LLM API providers."""

    def __init__(
        self,
        provider_name: str,
        account_name: str,
        api_key_env_var: str,
        supported_models: tuple[str, ...],
        declared_capabilities: frozenset[Capability],
    ) -> None:
        self._provider = provider_name
        self._account = account_name
        self._key_var = api_key_env_var
        self._models = supported_models
        self._caps = declared_capabilities

    @property
    def agent_id(self) -> str:
        return f"{self._provider}-{self._account}"

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def account_id(self) -> str:
        return self._account

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.API

    def capabilities(self) -> frozenset[Capability]:
        return self._caps

    def available_models(self) -> tuple[str, ...]:
        return self._models

    def health(self) -> tuple[bool, str]:
        key = os.environ.get(self._key_var)
        if not key:
            return False, f"Missing API key in environment variable: {self._key_var}"
        return True, f"API key configured via {self._key_var}"

    def execute(
        self,
        task_id: str,
        prompt: str,
        model: str | None = None,
        work_dir: Path | None = None,
        timeout_seconds: int = 300,
        options: dict[str, Any] | None = None,
    ) -> TaskExecutionResult:
        # Placeholder for future API SDK calls (e.g. google-genai, openai, anthropic)
        return TaskExecutionResult(
            task_id=task_id,
            agent_id=self.agent_id,
            account_id=self.account_id,
            provider=self.provider,
            success=False,
            exit_code=1,
            output="",
            error="Future provider not yet activated in current execution pool",
        )

    def continue_session(
        self,
        task_id: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        work_dir: Path | None = None,
        timeout_seconds: int = 300,
        options: dict[str, Any] | None = None,
    ) -> TaskExecutionResult:
        return self.execute(task_id, prompt, model, work_dir, timeout_seconds, options)

    def cancel(self, task_id: str) -> bool:
        return True
