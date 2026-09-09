"""Ollama Local Model Provider.

Supports Ollama running locally or remotely.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

from agents.base.adapter import Capability, TaskExecutionResult
from providers.base import AIProvider, ProviderType
from providers.registry.model_registry import ModelMetadata

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"

class OllamaProvider(AIProvider):
    """Native implementation of Ollama via OpenAI-compatible API without dependencies."""

    def __init__(
        self,
        provider_id: str = "ollama",
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        credential_reference: str = "",
        default_model: str = "llama3.2",
        models: list[str] | None = None,
        **kwargs
    ) -> None:
        self._provider_id = provider_id
        self._base_url = base_url.rstrip("/")
        self._credential_reference = ""  # No auth for Ollama
        self._default_model = default_model
        self._models = models or []
        self._capabilities = frozenset([
            Capability.DEEP_REASONING,
            Capability.CODE_COMPLETION,
        ])

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def display_name(self) -> str:
        return self._provider_id

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def native_api_root(self) -> str:
        if self._base_url.endswith("/v1"):
            return self._base_url[:-3] + "/api"
        return self._base_url + "/api"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.LOCAL_MODEL

    def capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    def health(self, account: Any = None) -> tuple[bool, str]:
        req = urllib.request.Request(f"{self.native_api_root}/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                pass
        except Exception as e:
            return False, str(e)
        return True, "OK"

    def list_models(self, account: Any = None) -> list[Any]:
        req = urllib.request.Request(f"{self.native_api_root}/tags", method="GET")
        model_names = []
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                model_names = [m.get("name") for m in data.get("models", [])]
        except Exception:
            model_names = self._models
            
        return [
            ModelMetadata(
                model_id=m,
                provider_id=self._provider_id,
                display_name=m,
                capabilities=self._capabilities
            )
            for m in model_names
        ]

    def validate_config(self) -> tuple[bool, list[str]]:
        errors = []
        if not self._base_url.endswith("/v1"):
            errors.append("Ollama base_url must end with /v1 (e.g. http://localhost:11434/v1)")
            return False, errors
        return True, errors

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
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        return req, payload

    def execute(self, job: Any) -> TaskExecutionResult:
        try:
            req, payload = self._build_request(job, stream=False)
            with urllib.request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=True, exit_code=0, output=content, error="")
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
