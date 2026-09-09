"""Bridge connecting legacy AgentAdapters to the universal AIProvider interface.

Allows Antigravity, Kiro, and Cline adapters to function as first-class
AIProvider instances without modifying their underlying implementations.
"""
from __future__ import annotations

from typing import Any, Callable

from agents.base.adapter import AgentAdapter, Capability, TaskExecutionResult
from brain.orchestrator.job import Job
from providers.base import AIProvider, ProviderType
from providers.registry.model_registry import ModelMetadata


class AgentProviderBridge(AIProvider):
    """Wraps an AgentAdapter to satisfy the AIProvider contract."""

    def __init__(self, adapter: AgentAdapter) -> None:
        self.adapter = adapter

    @property
    def provider_id(self) -> str:
        return self.adapter.provider

    @property
    def provider_type(self) -> ProviderType:
        if self.adapter.provider == "antigravity":
            return ProviderType.IDE
        return ProviderType.AGENT

    @property
    def display_name(self) -> str:
        return f"{self.adapter.provider.capitalize()} ({self.adapter.agent_id})"

    def capabilities(self) -> frozenset[Capability]:
        return self.adapter.capabilities()

    def health(self) -> tuple[bool, str]:
        return self.adapter.health()

    def list_models(self) -> list[ModelMetadata]:
        out = []
        for m in self.adapter.available_models():
            out.append(
                ModelMetadata(
                    model_id=m,
                    provider_id=self.provider_id,
                    account_id=self.adapter.account_id,
                    display_name=m,
                    capabilities=frozenset(c.value for c in self.adapter.capabilities()),
                    context_window=1_000_000 if "gemini" in m or "claude" in m else 128_000,
                    enabled=True,
                )
            )
        return out

    def execute(self, job: Job) -> TaskExecutionResult:
        job.mark_started()
        res = self.adapter.execute(
            task_id=job.id,
            prompt=job.task,
            model=job.model or None,
            options=job.metadata.get("options"),
        )
        if res.success:
            job.mark_completed(
                result={"output": res.output, "conversation_id": res.conversation_id},
                duration=res.duration_seconds,
            )
        else:
            job.mark_failed(res.error or "Execution failed", duration=res.duration_seconds)
        return res

    def execute_stream(
        self, job: Job, on_event: Callable[[dict[str, Any]], None] | None = None
    ) -> TaskExecutionResult:
        if hasattr(self.adapter, "stream") and callable(getattr(self.adapter, "stream")):
            job.mark_started()
            res = self.adapter.stream(
                task_id=job.id,
                prompt=job.task,
                model=job.model or None,
                options=job.metadata.get("options"),
                on_event=on_event,
            )
            if res.success:
                job.mark_completed(
                    result={"output": res.output, "conversation_id": res.conversation_id},
                    duration=res.duration_seconds,
                )
            else:
                job.mark_failed(res.error or "Execution failed", duration=res.duration_seconds)
            return res
        return self.execute(job)

    def validate_config(self) -> tuple[bool, list[str]]:
        if hasattr(self.adapter, "binary") and getattr(self.adapter, "binary") is None:
            return False, [f"Binary not resolved for {self.adapter.agent_id}"]
        return True, []
