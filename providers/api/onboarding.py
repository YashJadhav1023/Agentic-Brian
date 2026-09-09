"""Generic API Provider Onboarding and Structured Validation Framework.

Supports pre-flight validation and onboarding for:
- OpenAI (https://api.openai.com/v1)
- Anthropic (https://api.anthropic.com/v1)
- Google Gemini API (https://generativelanguage.googleapis.com/v1beta)
- OpenAI-compatible endpoints (Groq, OpenRouter, LiteLLM, vLLM, local gateways)

Emits structured ValidationReport with standardized ValidationStatus enums.
Guarantees zero plaintext secret exposure: all secrets are routed directly
into CredentialManager and referenced via `secret://` URIs.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from providers.registry.account_registry import (
    Account,
    AccountRegistry,
    AccountStatus,
    AuthenticationType,
)
from providers.registry.config import add_account_config
from providers.registry.credential_manager import get_credential_manager
from providers.registry.lifecycle import (
    AccountLifecycleState,
    AccountLifecycleStateMachine,
    AuthState,
    HealthState,
    ProcessState,
)

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    """Structured validation outcomes for API provider credentials and endpoints."""
    CONNECTED = "CONNECTED"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class ValidationReport:
    """Detailed structured validation report."""
    status: ValidationStatus
    provider_id: str
    models_discovered: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    error_message: str | None = None
    endpoint: str | None = None
    validated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "provider_id": self.provider_id,
            "models_discovered": self.models_discovered,
            "latency_ms": round(self.latency_ms, 2),
            "error_message": self.error_message,
            "endpoint": self.endpoint,
            "validated_at": self.validated_at,
            "details": self.details,
        }


class APIProviderOnboarder:
    """Universal onboarding and pre-flight validation engine for API providers."""

    DEFAULT_ENDPOINTS: dict[str, str] = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "gemini-api": "https://generativelanguage.googleapis.com/v1beta",
    }

    KNOWN_MODELS: dict[str, list[str]] = {
        "openai": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        "anthropic": [
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ],
        "gemini": [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
        "gemini-api": [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
    }

    @classmethod
    def resolve_endpoint(cls, provider_id: str, base_url: str | None = None) -> str:
        """Resolve base URL for provider, with fallbacks for known services."""
        if base_url and base_url.strip():
            return base_url.strip().rstrip("/")
        pid = provider_id.lower().strip()
        if pid in cls.DEFAULT_ENDPOINTS:
            return cls.DEFAULT_ENDPOINTS[pid]
        return "https://api.openai.com/v1"

    @classmethod
    def validate_credentials(
        cls,
        provider_id: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 8.0,
        extra_headers: dict[str, str] | None = None,
    ) -> ValidationReport:
        """Validate API key and connectivity against provider endpoint."""
        clean_key = (api_key or "").strip()
        if not clean_key:
            return ValidationReport(
                status=ValidationStatus.INVALID_CONFIGURATION,
                provider_id=provider_id,
                error_message="API key must not be empty",
                endpoint=base_url,
            )

        endpoint = cls.resolve_endpoint(provider_id, base_url)
        pid = provider_id.lower().strip()

        start_t = time.perf_counter()

        if pid == "anthropic":
            return cls._validate_anthropic(clean_key, endpoint, timeout, extra_headers, start_t)
        elif pid in ("gemini", "gemini-api"):
            return cls._validate_gemini(clean_key, endpoint, timeout, start_t)
        else:
            # Default to OpenAI / OpenAI-compatible
            return cls._validate_openai_compatible(pid, clean_key, endpoint, timeout, extra_headers, start_t)

    @classmethod
    def _validate_openai_compatible(
        cls,
        provider_id: str,
        api_key: str,
        endpoint: str,
        timeout: float,
        extra_headers: dict[str, str] | None,
        start_t: float,
    ) -> ValidationReport:
        url = f"{endpoint}/models"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "MissionControl/1.0",
        }
        if extra_headers:
            headers.update(extra_headers)

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                raw_body = resp.read()
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    if not isinstance(payload, (dict, list)):
                        return ValidationReport(
                            status=ValidationStatus.INVALID_CONFIGURATION,
                            provider_id=provider_id,
                            latency_ms=elapsed_ms,
                            error_message="Provider endpoint returned non-JSON response (possible HTML error page)",
                            endpoint=endpoint,
                            details={"status_code": resp.status},
                        )
                except Exception as exc:
                    return ValidationReport(
                        status=ValidationStatus.INVALID_CONFIGURATION,
                        provider_id=provider_id,
                        latency_ms=elapsed_ms,
                        error_message=f"Provider endpoint returned invalid non-JSON response: {exc}",
                        endpoint=endpoint,
                        details={"status_code": resp.status},
                    )

                discovered: list[str] = []
                data = payload.get("data", []) if isinstance(payload, dict) else payload
                if isinstance(data, list):
                    for m in data:
                        mid = m.get("id") if isinstance(m, dict) else str(m)
                        if mid:
                            discovered.append(mid)

                if not discovered:
                    discovered = cls.KNOWN_MODELS.get(provider_id, ["default"])

                return ValidationReport(
                    status=ValidationStatus.CONNECTED,
                    provider_id=provider_id,
                    models_discovered=discovered[:50],
                    latency_ms=elapsed_ms,
                    endpoint=endpoint,
                    details={"status_code": resp.status},
                )

        except urllib.error.HTTPError as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            if e.code in (401, 403):
                return ValidationReport(
                    status=ValidationStatus.UNAUTHORIZED,
                    provider_id=provider_id,
                    latency_ms=elapsed_ms,
                    error_message=f"HTTP {e.code}: Unauthorized. Please verify your API key.",
                    endpoint=endpoint,
                    details={"status_code": e.code},
                )
            elif e.code == 429:
                return ValidationReport(
                    status=ValidationStatus.RATE_LIMITED,
                    provider_id=provider_id,
                    latency_ms=elapsed_ms,
                    error_message="HTTP 429: Rate limit or quota exceeded.",
                    endpoint=endpoint,
                    details={"status_code": e.code},
                )
            elif e.code in (404, 500, 502, 503, 504):
                return ValidationReport(
                    status=ValidationStatus.UNAVAILABLE,
                    provider_id=provider_id,
                    latency_ms=elapsed_ms,
                    error_message=f"HTTP {e.code}: Provider endpoint unavailable or invalid.",
                    endpoint=endpoint,
                    details={"status_code": e.code},
                )
            return ValidationReport(
                status=ValidationStatus.INVALID_CONFIGURATION,
                provider_id=provider_id,
                latency_ms=elapsed_ms,
                error_message=f"HTTP {e.code}: {e.reason}",
                endpoint=endpoint,
                details={"status_code": e.code},
            )

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            return ValidationReport(
                status=ValidationStatus.NETWORK_ERROR,
                provider_id=provider_id,
                latency_ms=elapsed_ms,
                error_message=f"Network error: {e}",
                endpoint=endpoint,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            return ValidationReport(
                status=ValidationStatus.INVALID_CONFIGURATION,
                provider_id=provider_id,
                latency_ms=elapsed_ms,
                error_message=str(e),
                endpoint=endpoint,
            )

    @classmethod
    def _validate_anthropic(
        cls,
        api_key: str,
        endpoint: str,
        timeout: float,
        extra_headers: dict[str, str] | None,
        start_t: float,
    ) -> ValidationReport:
        url = f"{endpoint}/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": "MissionControl/1.0",
        }
        if extra_headers:
            headers.update(extra_headers)

        # Send empty/dummy probe to /messages:
        # If API key is valid, Anthropic responds with HTTP 400 (Bad Request: missing messages)
        # If API key is invalid, Anthropic responds with HTTP 401 (Unauthorized)
        dummy_body = json.dumps({"max_tokens": 1}).encode("utf-8")
        req = urllib.request.Request(url, data=dummy_body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                raw_body = resp.read()
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    if not isinstance(payload, (dict, list)):
                        return ValidationReport(
                            status=ValidationStatus.INVALID_CONFIGURATION,
                            provider_id="anthropic",
                            latency_ms=elapsed_ms,
                            error_message="Provider endpoint returned non-JSON response (possible HTML error page)",
                            endpoint=endpoint,
                            details={"status_code": resp.status},
                        )
                except Exception as exc:
                    return ValidationReport(
                        status=ValidationStatus.INVALID_CONFIGURATION,
                        provider_id="anthropic",
                        latency_ms=elapsed_ms,
                        error_message=f"Provider endpoint returned invalid non-JSON response: {exc}",
                        endpoint=endpoint,
                        details={"status_code": resp.status},
                    )

                return ValidationReport(
                    status=ValidationStatus.CONNECTED,
                    provider_id="anthropic",
                    models_discovered=cls.KNOWN_MODELS["anthropic"],
                    latency_ms=elapsed_ms,
                    endpoint=endpoint,
                )
        except urllib.error.HTTPError as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            if e.code == 400:
                if e.fp:
                    try:
                        err_body = e.fp.read().decode("utf-8", errors="replace")
                        if "<html" in err_body.lower() or "<!doctype html" in err_body.lower():
                            return ValidationReport(
                                status=ValidationStatus.INVALID_CONFIGURATION,
                                provider_id="anthropic",
                                latency_ms=elapsed_ms,
                                error_message="Provider endpoint returned HTML error page instead of API response",
                                endpoint=endpoint,
                                details={"status_code": 400},
                            )
                    except Exception:
                        pass
                # Expected: HTTP 400 confirms valid auth credentials!
                return ValidationReport(
                    status=ValidationStatus.CONNECTED,
                    provider_id="anthropic",
                    models_discovered=cls.KNOWN_MODELS["anthropic"],
                    latency_ms=elapsed_ms,
                    endpoint=endpoint,
                    details={"status_code": 400, "auth_verified": True},
                )
            elif e.code in (401, 403):
                return ValidationReport(
                    status=ValidationStatus.UNAUTHORIZED,
                    provider_id="anthropic",
                    latency_ms=elapsed_ms,
                    error_message=f"HTTP {e.code}: Unauthorized Anthropic API key.",
                    endpoint=endpoint,
                    details={"status_code": e.code},
                )
            elif e.code == 429:
                return ValidationReport(
                    status=ValidationStatus.RATE_LIMITED,
                    provider_id="anthropic",
                    latency_ms=elapsed_ms,
                    error_message="HTTP 429: Anthropic rate limit or credit exhaustion.",
                    endpoint=endpoint,
                    details={"status_code": e.code},
                )
            return ValidationReport(
                status=ValidationStatus.UNAVAILABLE,
                provider_id="anthropic",
                latency_ms=elapsed_ms,
                error_message=f"HTTP {e.code}: {e.reason}",
                endpoint=endpoint,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            return ValidationReport(
                status=ValidationStatus.NETWORK_ERROR,
                provider_id="anthropic",
                latency_ms=elapsed_ms,
                error_message=f"Network error connecting to Anthropic: {e}",
                endpoint=endpoint,
            )

    @classmethod
    def _validate_gemini(
        cls,
        api_key: str,
        endpoint: str,
        timeout: float,
        start_t: float,
    ) -> ValidationReport:
        url = f"{endpoint}/models"
        req = urllib.request.Request(
            url,
            headers={
                "x-goog-api-key": api_key,
                "User-Agent": "MissionControl/1.0",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                raw_body = resp.read()
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    if not isinstance(payload, dict):
                        return ValidationReport(
                            status=ValidationStatus.INVALID_CONFIGURATION,
                            provider_id="gemini",
                            latency_ms=elapsed_ms,
                            error_message="Provider endpoint returned non-JSON response (possible HTML error page)",
                            endpoint=endpoint,
                            details={"status_code": resp.status},
                        )
                except Exception as exc:
                    return ValidationReport(
                        status=ValidationStatus.INVALID_CONFIGURATION,
                        provider_id="gemini",
                        latency_ms=elapsed_ms,
                        error_message=f"Provider endpoint returned invalid non-JSON response: {exc}",
                        endpoint=endpoint,
                        details={"status_code": resp.status},
                    )

                discovered: list[str] = []
                for m in payload.get("models", []):
                    name = m.get("name", "") if isinstance(m, dict) else str(m)
                    if name.startswith("models/"):
                        name = name[len("models/"):]
                    if name:
                        discovered.append(name)

                if not discovered:
                    discovered = cls.KNOWN_MODELS["gemini"]

                return ValidationReport(
                    status=ValidationStatus.CONNECTED,
                    provider_id="gemini",
                    models_discovered=discovered[:50],
                    latency_ms=elapsed_ms,
                    endpoint=endpoint,
                    details={"status_code": resp.status},
                )
        except urllib.error.HTTPError as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            if e.code in (400, 401, 403):
                return ValidationReport(
                    status=ValidationStatus.UNAUTHORIZED,
                    provider_id="gemini",
                    latency_ms=elapsed_ms,
                    error_message=f"HTTP {e.code}: Invalid Google Gemini API key.",
                    endpoint=endpoint,
                    details={"status_code": e.code},
                )
            elif e.code == 429:
                return ValidationReport(
                    status=ValidationStatus.RATE_LIMITED,
                    provider_id="gemini",
                    latency_ms=elapsed_ms,
                    error_message="HTTP 429: Gemini quota or rate limit exceeded.",
                    endpoint=endpoint,
                    details={"status_code": e.code},
                )
            return ValidationReport(
                status=ValidationStatus.UNAVAILABLE,
                provider_id="gemini",
                latency_ms=elapsed_ms,
                error_message=f"HTTP {e.code}: {e.reason}",
                endpoint=endpoint,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            return ValidationReport(
                status=ValidationStatus.NETWORK_ERROR,
                provider_id="gemini",
                latency_ms=elapsed_ms,
                error_message=f"Network error connecting to Gemini API: {e}",
                endpoint=endpoint,
            )

    @classmethod
    def onboard_account(
        cls,
        account_id: str,
        provider_id: str,
        api_key: str,
        base_url: str | None = None,
        display_name: str | None = None,
        priority: int = 10,
        models: list[str] | None = None,
        registry: AccountRegistry | None = None,
        config_path: str | Path | None = None,
        validate_first: bool = True,
    ) -> tuple[Account | None, ValidationReport]:
        """Perform end-to-end API provider onboarding with safe credential persistence."""
        report = ValidationReport(
            status=ValidationStatus.CONNECTED,
            provider_id=provider_id,
            endpoint=base_url,
        )

        if validate_first:
            report = cls.validate_credentials(provider_id, api_key, base_url=base_url)
            if report.status in (ValidationStatus.UNAUTHORIZED, ValidationStatus.INVALID_CONFIGURATION):
                return None, report

        # Store credential safely under secret URI
        cred_ref = f"secret://mission-control/{provider_id}/{account_id}/api_key"
        cm = get_credential_manager()
        cm.store(cred_ref, api_key)

        acct_models = models or report.models_discovered or cls.KNOWN_MODELS.get(provider_id, ["default"])
        acct_endpoint = cls.resolve_endpoint(provider_id, base_url)

        is_connected = (report.status == ValidationStatus.CONNECTED)
        account = Account(
            id=account_id,
            provider_id=provider_id,
            account_name=account_id,
            display_name=display_name or f"{provider_id.capitalize()} ({account_id})",
            account_type="api",
            authentication_type=AuthenticationType.API_KEY,
            credential_reference=cred_ref,
            endpoint=acct_endpoint,
            status=AccountStatus.ONLINE if is_connected else AccountStatus.OFFLINE,
            enabled=True,
            priority=priority,
            models=acct_models,
            capabilities=["api_inference", "chat_completion"],
            metadata={
                "base_url": acct_endpoint,
                "auth_method": "api_key",
                "validation_status": report.status.value,
            },
            lifecycle_state=AccountLifecycleState.ONLINE if is_connected else AccountLifecycleState.VALIDATION_FAILED,
            auth_state=AuthState.AUTHENTICATED if is_connected else AuthState.AUTH_FAILED,
            health_state=HealthState.HEALTHY if is_connected else HealthState.UNHEALTHY,
            health_reason="" if is_connected else (report.error_message or "Validation failed"),
            process_state=ProcessState.IDLE,
        )

        if registry:
            registry.register_account(account)

        if config_path:
            account_conf = {
                "account_id": account.account_name,
                "agent_id": account.id,
                "display_name": account.display_name,
                "priority": account.priority,
                "enabled": True,
                "models": acct_models,
                "default_model": acct_models[0] if acct_models else "default",
                "capabilities": account.capabilities,
                "endpoint": acct_endpoint,
                "credential_reference": cred_ref,
            }
            try:
                add_account_config(provider_id, account.id, account_conf, str(config_path))
            except Exception as e:
                logger.warning(f"Could not persist API provider account config: {e}")

        return account, report
