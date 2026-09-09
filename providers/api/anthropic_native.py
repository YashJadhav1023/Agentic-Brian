"""Anthropic API Provider.

Native REST wrapper for Anthropic Messages API without dependencies.
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


class AnthropicProvider(AIProvider):
    """Native implementation of Anthropic API."""

    def __init__(
        self,
        provider_id: str = "anthropic",
        base_url: str = "https://api.anthropic.com/v1",
        default_model: str = "claude-3-5-sonnet-20240620",
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
        # Anthropic doesn't have a simple GET /models endpoint that we can use without side effects 
        # for a quick health check without sending a chat message. 
        # We can just do a minimal invalid request and see if we get an Auth error.
        api_key = self._get_api_key(account)
        if not api_key:
            return False, "AUTH_ERROR: Missing credential"

        req = urllib.request.Request(
            f"{self._base_url}/messages",
            data=b"{}",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                pass
        except urllib.error.HTTPError as e:
            if e.code == 401 or e.code == 403:
                return False, f"Auth Error {e.code}"
            # 400 Bad Request means Auth succeeded but our `{}` payload was rejected, which is healthy
            if e.code == 400:
                return True, "OK"
            return False, f"HTTP Error {e.code}"
        except Exception as e:
            return False, str(e)
        return True, "OK"

    def list_models(self, account: Any = None) -> list[Any]:
        return [
            "claude-3-5-sonnet-20240620",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307"
        ]

    def validate_config(self) -> tuple[bool, list[str]]:
        return True, []

    def _build_request(self, job: Any, stream: bool = False) -> tuple[urllib.request.Request, dict[str, Any]]:
        api_key = self._get_api_key(job.account)
        if not api_key:
            raise ValueError("API Key is missing")

        url = f"{self._base_url}/messages"
        model = job.model if hasattr(job, "model") and job.model else self._default_model
        task = job.task if hasattr(job, "task") else str(job)
        
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": task}],
            "stream": stream
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            method="POST"
        )
        return req, payload

    def execute(self, job: Any) -> TaskExecutionResult:
        try:
            req, payload = self._build_request(job, stream=False)
            with urllib.request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
                if "content" in data and len(data["content"]) > 0:
                    return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=True, exit_code=0, output=data["content"][0]["text"], error="")
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
                            if chunk.get("type") == "content_block_delta" and "delta" in chunk:
                                content = chunk["delta"].get("text", "")
                                full_content.append(content)
                                on_event({"type": "content", "content": content})
                        except json.JSONDecodeError:
                            continue
                            
            return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=True, exit_code=0, output="".join(full_content), error="")
        except urllib.error.HTTPError as e:
            return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=False, exit_code=1, output="", error=f"HTTP Error {e.code}")
        except Exception as e:
            return TaskExecutionResult(task_id=getattr(job, 'task_id', 'none'), agent_id='api', account_id=getattr(job, 'account_id', 'none'), provider=self._provider_id, success=False, exit_code=1, output="", error=str(e))
