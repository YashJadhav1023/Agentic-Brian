"""Gemini API Provider.

Native REST wrapper for Google Gemini API without dependencies.
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

logger = logging.getLogger(__name__)


class GeminiProvider(AIProvider):
    """Native implementation of Google Gemini API."""

    def __init__(
        self,
        provider_id: str = "gemini",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        default_model: str = "gemini-1.5-pro",
    ) -> None:
        self._provider_id = provider_id
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._capabilities = frozenset([
            Capability.DEEP_REASONING,
            Capability.CODE_COMPLETION,
        ])

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.API

    def capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    def _get_api_key(self, account: Any) -> str | None:
        if not account or not account.credential_reference:
            return None
        return get_credential_manager().resolve_secret(account.credential_reference)

    def health(self, account: Any = None) -> tuple[bool, str]:
        api_key = self._get_api_key(account)
        if not api_key:
            return False, "AUTH_ERROR: Missing credential"

        req = urllib.request.Request(f"{self._base_url}/models?key={api_key}")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    return True, "OK"
                return False, f"HTTP {response.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP Error {e.code}"
        except urllib.error.URLError as e:
            return False, f"URL Error {e.reason}"
        except Exception as e:
            return False, str(e)

    def list_models(self, account: Any = None) -> list[Any]:
        api_key = self._get_api_key(account)
        if not api_key:
            return []

        req = urllib.request.Request(f"{self._base_url}/models?key={api_key}")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    if isinstance(data, dict) and "models" in data:
                        return [m["name"].replace("models/", "") for m in data["models"]]
        except Exception:
            pass
        return []

    def validate_config(self) -> tuple[bool, list[str]]:
        return True, []

    def _build_request(self, job: Any, stream: bool = False) -> tuple[urllib.request.Request, dict[str, Any]]:
        api_key = self._get_api_key(job.account)
        if not api_key:
            raise ValueError("API Key is missing")

        model = job.model if hasattr(job, "model") and job.model else self._default_model
        task = job.task if hasattr(job, "task") else str(job)
        
        endpoint = "streamGenerateContent" if stream else "generateContent"
        url = f"{self._base_url}/models/{model}:{endpoint}?key={api_key}"
        
        payload = {
            "contents": [{"parts": [{"text": task}]}]
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
                if "candidates" in data and len(data["candidates"]) > 0:
                    parts = data["candidates"][0].get("content", {}).get("parts", [])
                    content = "".join([p.get("text", "") for p in parts])
                    return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=True, exit_code=0, output=content, error="")
                return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=False, exit_code=1, output="", error="Invalid response format")
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
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            chunk = json.loads(data_str)
                            if "candidates" in chunk and len(chunk["candidates"]) > 0:
                                parts = chunk["candidates"][0].get("content", {}).get("parts", [])
                                content = "".join([p.get("text", "") for p in parts])
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
