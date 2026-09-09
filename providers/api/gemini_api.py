"""Direct Google Gemini REST API Provider for Mission Control.

Uses Google AI Studio REST API (v1beta/models/...:generateContent) with stdlib urllib.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from agents.base.adapter import Capability, TaskExecutionResult
from brain.orchestrator.job import Job
from providers.base import AIProvider, ProviderType, RateLimitInfo, UsageStats
from providers.registry.credential_manager import get_credential_manager
from providers.registry.model_registry import ModelMetadata


class GeminiAPIProvider(AIProvider):
    """Direct REST provider for Google Gemini models via AI Studio."""

    def __init__(
        self,
        provider_id: str = "gemini-api",
        credential_reference: str = "",
        default_model: str = "gemini-2.0-flash",
        models: tuple[str, ...] = ("gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"),
    ) -> None:
        self._provider_id = provider_id
        self._credential_reference = credential_reference
        self._default_model = default_model
        self._models = list(models)
        self._base_url = "https://generativelanguage.googleapis.com/v1beta/models"
        self._usage = UsageStats()

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.API

    @property
    def display_name(self) -> str:
        return "Google Gemini API"

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
        # Lightweight probe: list models
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True, "ONLINE: Connected to Google Gemini API"
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                return False, f"AUTH_ERROR: HTTP {e.code} from Google Gemini API"
            if e.code == 429:
                return False, "RATE_LIMITED: Google Gemini quota limit reached"
            return False, f"OFFLINE: HTTP {e.code} from Google Gemini API"
        except Exception as exc:
            return False, f"OFFLINE: {exc}"
        return True, "ONLINE: Google Gemini API configured"

    def list_models(self) -> list[ModelMetadata]:
        return [
            ModelMetadata(
                model_id=m,
                provider_id=self._provider_id,
                display_name=m,
                capabilities=frozenset(c.value for c in self.capabilities()),
                context_window=1_000_000,
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
            job.mark_failed("Missing Gemini API key")
            return TaskExecutionResult(
                task_id=job.id,
                agent_id=f"{self.provider_id}-{job.account or 'default'}",
                account_id=job.account or "default",
                provider=self.provider_id,
                success=False,
                exit_code=1,
                output="",
                error="AUTH_ERROR: Missing Gemini API key",
            )

        url = f"{self._base_url}/{model_name}:generateContent?key={key}"
        payload = {"contents": [{"parts": [{"text": job.task}]}]}
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                dur = time.time() - t0
                parsed = json.loads(resp.read().decode("utf-8"))
                text = ""
                candidates = parsed.get("candidates", [])
                if candidates and isinstance(candidates[0], dict):
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and isinstance(parts[0], dict):
                        text = parts[0].get("text", "")

                meta = parsed.get("usageMetadata", {})
                in_tok = meta.get("promptTokenCount", 0)
                out_tok = meta.get("candidatesTokenCount", 0)
                tot_tok = meta.get("totalTokenCount", in_tok + out_tok)

                self._usage.request_count += 1
                self._usage.input_tokens += in_tok
                self._usage.output_tokens += out_tok
                self._usage.total_tokens += tot_tok

                job.mark_completed(
                    result={"output": text, "model": model_name, "usage": meta},
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
