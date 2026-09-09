"""Direct Anthropic Messages REST API Provider for Mission Control.

Uses Anthropic Messages API (https://api.anthropic.com/v1/messages) with stdlib urllib.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from agents.base.adapter import Capability, TaskExecutionResult
from brain.orchestrator.job import Job
from providers.base import AIProvider, ProviderType, UsageStats
from providers.registry.credential_manager import get_credential_manager
from providers.registry.model_registry import ModelMetadata


class AnthropicAPIProvider(AIProvider):
    """Direct REST provider for Anthropic Claude models."""

    def __init__(
        self,
        provider_id: str = "anthropic",
        credential_reference: str = "",
        default_model: str = "claude-3-5-sonnet-20241022",
        models: tuple[str, ...] = (
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ),
    ) -> None:
        self._provider_id = provider_id
        self._credential_reference = credential_reference
        self._default_model = default_model
        self._models = list(models)
        self._endpoint = "https://api.anthropic.com/v1/messages"
        self._usage = UsageStats()

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.API

    @property
    def display_name(self) -> str:
        return "Anthropic Claude API"

    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            [
                Capability.ARCHITECTURE,
                Capability.DEEP_REASONING,
                Capability.EDITOR_REFACTORING,
                Capability.CODE_REVIEW,
                Capability.DOCUMENTATION,
            ]
        )

    def _get_api_key(self) -> str | None:
        if not self._credential_reference:
            return None
        return get_credential_manager().retrieve(self._credential_reference)

    def health(self) -> tuple[bool, str]:
        key = self._get_api_key()
        if not key:
            return False, f"AUTH_ERROR: Missing credential for {self._credential_reference}"
        return True, "ONLINE: Anthropic Claude API configured"

    def list_models(self) -> list[ModelMetadata]:
        return [
            ModelMetadata(
                model_id=m,
                provider_id=self._provider_id,
                display_name=m,
                capabilities=frozenset(c.value for c in self.capabilities()),
                context_window=200_000,
                enabled=True,
            )
            for m in self._models
        ]

    def execute(self, job: Job) -> TaskExecutionResult:
        job.mark_started()
        t0 = time.time()
        model_name = job.model or self._default_model
        key = self._get_api_key()
        if not key:
            job.mark_failed("Missing Anthropic API key")
            return TaskExecutionResult(
                task_id=job.id,
                agent_id=f"{self.provider_id}-{job.account or 'default'}",
                account_id=job.account or "default",
                provider=self.provider_id,
                success=False,
                exit_code=1,
                output="",
                error="AUTH_ERROR: Missing Anthropic API key",
            )

        payload = {
            "model": model_name,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": job.task}],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": "AgenticBrain-MissionControl/1.0",
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._endpoint, data=data_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                dur = time.time() - t0
                parsed = json.loads(resp.read().decode("utf-8"))
                text = ""
                for part in parsed.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "text":
                        text += part.get("text", "")

                u = parsed.get("usage", {})
                in_tok = u.get("input_tokens", 0)
                out_tok = u.get("output_tokens", 0)
                tot_tok = in_tok + out_tok

                self._usage.request_count += 1
                self._usage.input_tokens += in_tok
                self._usage.output_tokens += out_tok
                self._usage.total_tokens += tot_tok

                job.mark_completed(
                    result={"output": text, "model": model_name, "usage": u},
                    duration=dur,
                )
                return TaskExecutionResult(
                    task_id=job.id,
                    agent_id=f"{self.provider_id}-{job.account or 'default'}",
                    account_id=job.account or "default",
                    provider=self.provider_id,
                    success=True,
                    exit_code=0,
                    output=text,
                    error="",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    total_tokens=tot_tok,
                    duration_seconds=round(dur, 3),
                    actual_model=model_name,
                    requested_model=model_name,
                )
        except urllib.error.HTTPError as e:
            dur = time.time() - t0
            raw_err = e.read().decode("utf-8", errors="replace")
            clean_err = get_credential_manager().redactor.redact(raw_err)
            job.mark_failed(f"HTTP {e.code}: {clean_err}", duration=dur)
            return TaskExecutionResult(
                task_id=job.id,
                agent_id=f"{self.provider_id}-{job.account or 'default'}",
                account_id=job.account or "default",
                provider=self.provider_id,
                success=False,
                exit_code=e.code,
                output="",
                error=f"HTTP {e.code}: {clean_err}",
                duration_seconds=round(dur, 3),
            )
        except Exception as exc:
            dur = time.time() - t0
            clean_err = get_credential_manager().redactor.redact(str(exc))
            job.mark_failed(clean_err, duration=dur)
            return TaskExecutionResult(
                task_id=job.id,
                agent_id=f"{self.provider_id}-{job.account or 'default'}",
                account_id=job.account or "default",
                provider=self.provider_id,
                success=False,
                exit_code=1,
                output="",
                error=clean_err,
                duration_seconds=round(dur, 3),
            )

    def validate_config(self) -> tuple[bool, list[str]]:
        return True, []

    def get_usage(self, account_id: str | None = None) -> UsageStats | str:
        return self._usage
