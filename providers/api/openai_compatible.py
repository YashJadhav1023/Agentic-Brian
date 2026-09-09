"""OpenAI-compatible API Provider.

Supports any provider using the OpenAI Chat Completions API format,
including OpenAI, OpenRouter, Groq, LiteLLM, vLLM, and others.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

from agents.base.adapter import Capability, TaskExecutionResult
from providers.base import AIProvider, ProviderType
from providers.registry.credential_manager import get_credential_manager
from providers.registry.model_registry import ModelMetadata

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(AIProvider):
    """Native implementation of OpenAI-compatible API without dependencies."""

    def __init__(
        self,
        provider_id: str = "openai",
        base_url: str = "https://api.openai.com/v1",
        credential_reference: str = "",
        default_model: str = "gpt-4o",
        models: tuple[str, ...] = ("default",),
        headers: dict[str, str] | None = None,
        display_name: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        **kwargs,
    ) -> None:
        self._provider_id = provider_id
        self._base_url = base_url.rstrip("/")
        self._credential_reference = credential_reference
        self._default_model = default_model
        self._models = list(models)
        self._extra_headers = headers or {}
        self._display_name = display_name or provider_id
        self._organization = organization
        self._project = project
        self._capabilities = frozenset([
            Capability.DEEP_REASONING,
            Capability.CODE_COMPLETION,
        ])

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def models(self) -> list[str]:
        return self._models

    @property
    def default_model(self) -> str:
        return self._default_model

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def provider_type(self) -> ProviderType:
        if self._base_url.startswith("http://localhost") or self._base_url.startswith("http://127.0.0.1"):
            return ProviderType.LOCAL_MODEL
        if "openrouter.ai" in self._base_url or "litellm" in self._provider_id:
            return ProviderType.GATEWAY
        return ProviderType.API

    def capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    def _get_api_key(self, account: Any = None) -> str | None:
        ref = self._credential_reference
        if account and hasattr(account, "credential_reference") and account.credential_reference:
            ref = account.credential_reference
        if not ref:
            return None
        return get_credential_manager().resolve_secret(ref)

    def _build_request_headers(self, account: Any = None) -> dict[str, str]:
        h = dict(self._extra_headers)
        h["Content-Type"] = "application/json"
        api_key = self._get_api_key(account)
        if api_key:
            h["Authorization"] = f"Bearer {api_key}"
        if self._organization:
            h["OpenAI-Organization"] = self._organization
        if self._project:
            h["OpenAI-Project"] = self._project
        return h

    def health(self, account: Any = None) -> tuple[bool, str]:
        api_key = self._get_api_key(account)
        if not api_key:
            return False, "AUTH_ERROR: Missing credential"

        req = urllib.request.Request(
            f"{self._base_url}/models",
            headers=self._build_request_headers(account)
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                pass
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "AUTH_ERROR: Unauthorized"
            return False, f"HTTP Error {e.code}"
        except Exception as e:
            return False, str(e)
        return True, "OK"

    def list_models(self, account: Any = None) -> list[Any]:
        return [
            ModelMetadata(
                model_id=m,
                provider_id=self._provider_id,
                display_name=m,
                capabilities=self._capabilities
            )
            for m in self._models
        ]

    def validate_config(self) -> tuple[bool, list[str]]:
        errors = []
        if not self._provider_id:
            errors.append("provider_id is required")
        if not self._base_url:
            errors.append("base_url is required")
        return len(errors) == 0, errors

    def _build_request(self, job: Any, stream: bool = False) -> tuple[urllib.request.Request, dict[str, Any]]:
        url = f"{self._base_url}/chat/completions"
        model = job.model if hasattr(job, "model") and job.model else self._default_model
        task = job.task if hasattr(job, "task") else str(job)
        
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": task}],
            "stream": stream
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._build_request_headers(getattr(job, "account", None)),
            method="POST"
        )
        return req, payload

    def execute(self, job: Any) -> TaskExecutionResult:
        try:
            req, payload = self._build_request(job, stream=False)
            with urllib.request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                actual_model = data.get("model", payload.get("model"))
                usage = data.get("usage", {})
                in_toks = usage.get("prompt_tokens")
                out_toks = usage.get("completion_tokens")
                tot_toks = usage.get("total_tokens")
                return TaskExecutionResult(
                    task_id=getattr(job, 'task_id', 'none'),
                    agent_id='api',
                    account_id=getattr(job, 'account_id', 'none'),
                    provider=self._provider_id,
                    success=True,
                    exit_code=0,
                    output=content,
                    error="",
                    actual_model=actual_model,
                    input_tokens=in_toks,
                    output_tokens=out_toks,
                    total_tokens=tot_toks
                )
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=False, exit_code=1, output="", error=f"HTTP Error {e.code}: {err_msg}")
        except Exception as e:
            return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=False, exit_code=1, output="", error=str(e))

    def execute_stream(
        self, job: Any, on_event: Callable[[dict[str, Any]], None] | None = None
    ) -> TaskExecutionResult:
        if not on_event:
            return self.execute(job)

        try:
            req, payload = self._build_request(job, stream=True)
            full_content = []
            
            with urllib.request.urlopen(req, timeout=120) as response:
                for line in response:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: ") and line != "data: [DONE]":
                        data_str = line[6:]
                        try:
                            chunk = json.loads(data_str)
                            content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if content:
                                full_content.append(content)
                                on_event({"type": "content", "content": content})
                        except json.JSONDecodeError:
                            continue
                            
            return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=True, exit_code=0, output="".join(full_content), error="")
        except urllib.error.HTTPError as e:
            return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=False, exit_code=1, output="", error=f"HTTP Error {e.code}")
        except Exception as e:
            return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=False, exit_code=1, output="", error=str(e))
