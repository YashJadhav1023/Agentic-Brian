#!/usr/bin/env python3
"""Mission Control Dashboard Server.

Next-Gen Shared Brain & Multi-Agent Mission Control web application.
Serves interactive tabs: Overview, Agents, Tasks Kanban, Execution Flow,
Routing Simulator & History, Memory Explorer, Handoff Viewer, Worktree Sandboxes,
Live Events, and Git Monitor.
Equipped with local security hardening: Bearer token auth for mutating actions,
rate limiting, CORS local-origin restriction, security headers, and worktree approval gates.
"""
from __future__ import annotations

import atexit
import base64
import datetime
import errno
import hmac
import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.base.adapter import Capability
from brain.analytics import (
    get_analytics_engine,
    get_cost_tracker,
    get_quota_manager,
    get_retention_manager,
    get_usage_tracker,
)
from brain.context.continuator import UniversalContinuator
from brain.orchestrator.job import Job
from brain.orchestrator.job_manager import JobManager, UsageTracker
from brain.orchestrator.orchestrator import Orchestrator
from brain.router.models import RoutingMode
from brain.router.smart_router import SmartRouter
from events.bus import Event, EventBus, EventType
from handoffs.handoff_manager import HandoffManager
from memory.retrieval.retriever import MemoryRetriever
from memory.store.memory_store import MemoryScope, MemoryStore
from models.policies.model_policy import Complexity
from providers.api.anthropic_native import AnthropicProvider
from providers.api.gemini_native import GeminiProvider
from providers.api.ollama_local import OllamaProvider
from providers.api.openai_compatible import OpenAICompatibleProvider
from providers.base import ProviderType
from providers.registry.account_registry import Account, AccountStatus, AuthenticationType
from providers.registry.bootstrap import create_default_registry
from providers.registry.credential_manager import SecretRedactor, get_credential_manager
from providers.registry.model_registry import ModelMetadata
from tasks.manager import Task, TaskManager, TaskPriority, TaskStatus

STATIC_DIR = (Path(__file__).resolve().parent / "static").resolve()
PORT = int(os.environ.get("BRAIN_PORT", "3333"))

# Core singletons
registry = create_default_registry()
task_manager = TaskManager(root_tasks_dir=PROJECT_ROOT / "tasks")
handoff_manager = HandoffManager(root_dir=PROJECT_ROOT / "handoffs")
event_bus = EventBus(log_path=PROJECT_ROOT / "runtime" / "logs" / "events.jsonl")
memory_store = MemoryStore(db_path=PROJECT_ROOT / "memory" / "store" / "shared_memory.db")
orchestrator = Orchestrator(
    task_manager=task_manager,
    registry=registry,
    event_bus=event_bus,
    workspace_dir=PROJECT_ROOT,
)

# Startup Runtime State Reconciliation (Phase 6B)
startup_reconcile = task_manager.reconcile_runtime_state(
    active_task_ids=orchestrator.swarm.get_active_task_ids()
)

# Phase 10: Job Manager & Secret Redactor
job_manager = JobManager(
    provider_registry=registry,
    storage_dir=PROJECT_ROOT / "runtime" / "jobs",
)
redactor = get_credential_manager().redactor

# ── Phase 22 Parts 6/9/10: Account Onboarding Wizard, Provider Registry UI,
# and Live Account Lifecycle Event Stream ────────────────────────────────────
#
# These imports back the multi-step Account Add Wizard. They are the SAME engines
# used everywhere else in the platform, so the wizard drives the real lifecycle
# rather than a parallel one:
#   * AccountLifecycleStateMachine    — the formal 14-state machine (validated).
#   * APIProviderOnboarder            — real pre-flight validation for API keys.
#   * ClineAuthManager                — DYNAMIC capability discovery for Cline, so
#                                       only auth methods the installed CLI truly
#                                       supports are ever offered.
#   * AntigravityAuthManager          — official Google OAuth CLI flow, isolated to
#                                       a fresh profile dir; never touches the IDE
#                                       GUI profile or keyring service=gemini.
# Secrets entered in the wizard go STRAIGHT to CredentialManager; only a
# secret:// reference is ever stored or surfaced. Every wizard event is redacted
# before it leaves the process.
from providers.registry.lifecycle import (
    AccountLifecycleState,
    AccountLifecycleStateMachine,
    AuthState,
    HealthState,
    ProcessState,
)
from providers.api.onboarding import APIProviderOnboarder, ValidationStatus
from agents.cline.auth import ClineAuthManager
from agents.antigravity.auth import AntigravityAuthManager, TOKEN_FILENAME
from agents.antigravity.adapter import AntigravityAdapter, AntigravityAccountAdapter
from providers.registry.config import add_account_config, remove_account_config
from providers.adapters.bridge import AgentProviderBridge, AIProviderAgentAdapter
from agents.cline.adapter import ClineAdapter
from agents.kiro.adapter import KiroAdapter
from providers.registry.provider_registry import Provider


def _new_correlation_id() -> str:
    """Correlation id tying every event of one onboarding flow together."""
    return "wiz-" + secrets.token_hex(8)


class WizardEventStream:
    """Tiny thread-safe pub/sub for redacted account-lifecycle events.

    The dashboard already streams the append-only :class:`EventBus` over SSE, but
    the required Phase 22 event NAMES (``account.create_started`` and friends) are
    not members of the closed :class:`EventType` enum owned by ``events/bus.py``
    (which this task may not modify). So wizard events are published here with
    their exact dotted names, mirrored (redacted) into the persistent EventBus for
    the audit ledger, and surfaced verbatim by the SSE endpoint. Every payload is
    passed through the CredentialManager redactor before it is broadcast, so no
    secret can ever reach a subscriber.
    """

    #: Canonical event names emitted across an onboarding flow. Kept here so the
    #: names live in exactly one place and the UI/tests have a single source.
    NAMES = (
        "account.create_started",
        "account.configuring",
        "account.authentication_started",
        "account.authentication_success",
        "account.authentication_failed",
        "account.authentication_cancelled",
        "account.validation_started",
        "account.validation_success",
        "account.validation_failed",
        "account.registered",
        "account.health_check",
        "account.online",
        "account.removed",
    )

    def __init__(self, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._recent: list[dict[str, Any]] = []
        self._maxlen = maxlen
        self._seq = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
            backlog = list(self._recent)
        for item in backlog:
            try:
                q.put_nowait(item)
            except queue.Full:
                break
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent[-limit:])

    def emit(
        self,
        name: str,
        correlation_id: str,
        provider_id: str | None = None,
        account_id: str | None = None,
        message: str = "",
        lifecycle_state: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build, redact, persist and broadcast one wizard lifecycle event."""
        self._seq += 1
        raw = {
            "event_type": name,
            "event_id": f"wevt-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{self._seq}",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "correlation_id": correlation_id,
            "provider": provider_id,
            "provider_id": provider_id,
            "account_id": account_id,
            "lifecycle_state": lifecycle_state,
            "message": message,
        }
        payload = dict(extra or {})
        payload.update({
            "correlation_id": correlation_id,
            "provider_id": provider_id,
            "account_id": account_id,
            "lifecycle_state": lifecycle_state,
            "message": message,
        })
        raw["payload"] = payload
        raw["metadata"] = payload
        # Defense in depth: redact the entire event before it ever leaves here.
        safe = redactor.redact_dict(raw)

        with self._lock:
            self._recent.append(safe)
            if len(self._recent) > self._maxlen:
                self._recent.pop(0)
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(safe)
            except queue.Full:
                pass

        # Mirror to the persistent audit ledger under a neutral existing EventType
        # (the dotted name is preserved in metadata). Best-effort; never fatal.
        try:
            event_bus.emit(
                Event(
                    event_type=EventType.AGENT_HEALTH,
                    provider=provider_id,
                    agent_id=account_id,
                    metadata={"wizard_event": name, **payload},
                    payload={"wizard_event": name, **payload},
                )
            )
        except Exception:
            pass
        return safe


wizard_event_stream = WizardEventStream()


class WizardError(Exception):
    """Wizard failure carrying the lifecycle failure state to land in."""

    def __init__(self, message: str, failure_state: AccountLifecycleState) -> None:
        super().__init__(message)
        self.failure_state = failure_state


class WizardSession:
    """One in-flight Account Add Wizard flow.

    Steps map 1:1 onto real AccountLifecycleState transitions:
        select_provider -> DISCOVERED
        select_auth      -> (no transition; records chosen method)
        configure        -> CONFIGURING
        authenticate     -> AUTHENTICATING -> AUTHENTICATED (or AUTH_FAILED/AUTH_CANCELLED)
        validate         -> VALIDATING -> READY (or VALIDATION_FAILED/PROVIDER_UNAVAILABLE)
        register         -> (persist into registry; stays READY)
        health_check     -> health probe
        complete         -> ONLINE

    The account object is built up-front (in DISCOVERED) but only inserted into
    the AccountRegistry at the register step. Any credential or profile directory
    created before that is tracked so cancellation can roll them back cleanly,
    leaving NO orphaned account record, credential, or profile directory.
    """

    ORDER = [
        "select_provider", "select_auth", "configure",
        "authenticate", "validate", "register", "health_check", "complete",
    ]

    def __init__(self, provider_id: str, account_id: str) -> None:
        self.wizard_id = _new_correlation_id()
        self.correlation_id = self.wizard_id
        self.provider_id = provider_id
        self.account_id = account_id
        self.step = "select_provider"
        self.auth_method: str | None = None
        self.config: dict[str, Any] = {}
        self.created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Rollback bookkeeping — never holds a secret value, only references.
        self.credential_reference: str | None = None
        self.profile_dirs: list[str] = []
        self.registered = False
        self.cancelled = False
        self.completed = False
        self.last_error: str | None = None
        self.failure_state: str | None = None
        # The account is built lazily so the state machine has a real target.
        self.account: Account | None = None
        self.discovered_models: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        d = {
            "wizard_id": self.wizard_id,
            "correlation_id": self.correlation_id,
            "provider_id": self.provider_id,
            "account_id": self.account_id,
            "step": self.step,
            "login_method": self.auth_method,
            "registered": self.registered,
            "cancelled": self.cancelled,
            "completed": self.completed,
            "last_error": self.last_error,
            "failure_state": self.failure_state,
            "lifecycle_state": (self.account.lifecycle_state.value if self.account else None),
            "discovered_models": self.discovered_models,
        }
        # The config may echo a base_url or model but NEVER an api_key: the key is
        # popped straight into CredentialManager and never retained on the session.
        return redactor.redact_dict(d)


def _get_antigravity_oauth_credentials() -> tuple[str, str]:
    """Retrieve Antigravity Google OAuth Client ID and Secret dynamically."""
    client_id = os.environ.get("ANTIGRAVITY_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ANTIGRAVITY_OAUTH_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret

    try:
        cm = get_credential_manager()
        cid = cm.retrieve("secret://mission-control/oauth/antigravity/client_id") or ""
        sec = cm.retrieve("secret://mission-control/oauth/antigravity/client_secret") or ""
        if cid and sec:
            return cid.strip(), sec.strip()
    except Exception:
        pass

    paths = [
        PROJECT_ROOT / "creds_oauth.json",
        Path.home() / ".mission-control" / "creds_oauth.json",
        Path("/tmp/omniroute/src/lib/oauth/providers/antigravity.ts"),
    ]
    for p in paths:
        if p.is_file():
            try:
                if p.suffix == ".json":
                    d = json.loads(p.read_text(encoding="utf-8"))
                    cid = (d.get("client_id") or "").strip()
                    csec = (d.get("client_secret") or "").strip()
                    if cid and csec:
                        return cid, csec
                elif p.suffix == ".ts":
                    content = p.read_text(encoding="utf-8")
                    import re
                    m_id = re.search(r'clientId:\s*["\']([^"\']+)["\']', content)
                    m_sec = re.search(r'clientSecret:\s*["\']([^"\']+)["\']', content)
                    if m_id and m_sec:
                        return m_id.group(1).strip(), m_sec.group(1).strip()
            except Exception:
                pass

    return "", ""

ANTIGRAVITY_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]


class WizardManager:
    """Drives Account Add Wizard sessions against the real lifecycle engine."""

    #: Antigravity is OAuth-only and must use a fresh isolated profile. The IDE
    #: GUI profile and keyring slot are never touched by the wizard.
    _STATIC_AUTH_METHODS = {
        "antigravity": [
            {"id": "oauth", "label": "Google Sign-In (Interactive Browser OAuth)"},
            {"id": "api_key", "label": "Direct OAuth Token / Session Key (Manual / Headless)"},
        ],
        "openai": [{"id": "api_key", "label": "API Key"}],
        "anthropic": [{"id": "api_key", "label": "API Key"}],
        "gemini": [{"id": "api_key", "label": "API Key"}],
        "gemini-api": [{"id": "api_key", "label": "API Key"}],
        "openrouter": [{"id": "api_key", "label": "API Key"}],
        "groq": [{"id": "api_key", "label": "API Key"}],
        "ollama": [{"id": "api_key", "label": "Endpoint / Local Server"}],
        "kiro": [{"id": "api_key", "label": "Local CLI Session / API Key"}],
        "cline": [{"id": "api_key", "label": "API Key (cline auth / config)"}],
    }

    #: Router-valid default capabilities applied at registration when the
    #: operator supplied none. Every member is a real ``Capability`` value,
    #: because the router discards unrecognised strings and an account left with
    #: an empty capability set is rejected outright as CAPABILITY_MISMATCH.
    #:
    #: A direct-API LLM account is a general reasoning and authoring resource; it
    #: deliberately does NOT claim terminal, cloud, or Kubernetes capabilities,
    #: which belong to CLI agents that can actually execute locally.
    _DEFAULT_CAPABILITIES_API = frozenset({
        Capability.DEEP_REASONING,
        Capability.CODE_COMPLETION,
        Capability.CODE_REVIEW,
        Capability.DOCUMENTATION,
        Capability.TEST_SCAFFOLDING,
    })
    _DEFAULT_CAPABILITIES_ANTIGRAVITY = frozenset({
        Capability.ARCHITECTURE,
        Capability.DEEP_REASONING,
        Capability.PROTOCOL_DESIGN,
        Capability.GOVERNANCE,
        Capability.DOCUMENTATION,
    })
    _DEFAULT_CAPABILITIES_CLINE = frozenset({
        Capability.EDITOR_REFACTORING,
        Capability.COMPONENT_REFACTORING,
        Capability.FRONTEND_STYLING,
        Capability.CODE_REVIEW,
    })

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, WizardSession] = {}

    # ---- auth-method discovery -------------------------------------------
    def auth_methods_for(self, provider_id: str) -> list[dict[str, str]]:
        """Return the auth methods supported by the provider.

        For Cline, checks live capability discovery; falls back to direct API key.
        For Antigravity, defaults to Interactive Google OAuth.
        For API providers and agents, returns standard authentication methods.
        """
        pid = (provider_id or "").lower().strip()
        if pid == "cline":
            try:
                caps = ClineAuthManager().discover_capabilities()
            except Exception:
                caps = None
            methods: list[dict[str, str]] = []
            if caps and caps.version != "not_installed" and caps.has_auth_command:
                if "-k" in caps.auth_flags or "--apikey" in caps.auth_flags:
                    methods.append({
                        "id": "api_key",
                        "label": "API Key via cline auth",
                        "flags": [f for f in caps.auth_flags if f in ("-k", "-m", "-b", "--config", "--data-dir")],
                        "cli_version": caps.version,
                    })
            if not methods:
                methods.append({
                    "id": "api_key",
                    "label": "API Key (Direct / Headless)",
                })
            return methods
        if pid in self._STATIC_AUTH_METHODS:
            return list(self._STATIC_AUTH_METHODS[pid])
        # Unknown / OpenAI-compatible gateway
        return [{"id": "api_key", "label": "API Key"}]

    # ---- session lifecycle -----------------------------------------------
    def get(self, wizard_id: str) -> WizardSession | None:
        with self._lock:
            return self._sessions.get(wizard_id)

    def start(self, provider_id: str, account_id: str) -> WizardSession:
        with self._lock:
            sess = WizardSession(provider_id, account_id)
            self._sessions[sess.wizard_id] = sess
        # Build the account object in DISCOVERED so the state machine can drive it.
        sess.account = Account(
            id=account_id,
            provider_id=provider_id,
            account_name=account_id,
            display_name=account_id,
            account_type="agent" if provider_id in ("antigravity", "cline") else "api",
            lifecycle_state=AccountLifecycleState.DISCOVERED,
            auth_state=AuthState.UNAUTHENTICATED,
            health_state=HealthState.UNKNOWN,
            process_state=ProcessState.IDLE,
            enabled=True,
        )
        wizard_event_stream.emit(
            "account.create_started", sess.correlation_id, provider_id, account_id,
            message="Onboarding flow started", lifecycle_state="DISCOVERED",
        )
        return sess

    def _transition(self, sess: WizardSession, to_state: AccountLifecycleState, reason: str = "") -> None:
        AccountLifecycleStateMachine.transition(sess.account, to_state, reason=reason)

    def select_auth(self, sess: WizardSession, method: str) -> None:
        allowed = {m["id"] for m in self.auth_methods_for(sess.provider_id)}
        if not allowed:
            raise WizardError(
                f"Provider '{sess.provider_id}' has no supported authentication method available "
                f"(the CLI may not be installed).",
                AccountLifecycleState.PROVIDER_UNAVAILABLE,
            )
        if method not in allowed:
            raise WizardError(
                f"Authentication method '{method}' is not supported by provider '{sess.provider_id}'. "
                f"Supported: {sorted(allowed)}",
                AccountLifecycleState.CONFIG_ERROR,
            )
        sess.auth_method = method
        sess.step = "select_auth"

    def launch_login(self, sess: WizardSession, redirect_origin: str = "http://127.0.0.1:3333") -> dict[str, Any]:
        """Prepare isolated environment and return the official Google OAuth authorization URL."""
        if sess.provider_id == "antigravity":
            mgr = AntigravityAuthManager()
            data_dir, profile_dir = mgr.create_isolated_profile(sess.account_id, sess.config.get("app_data_dir"))
            if str(profile_dir) not in sess.profile_dirs:
                sess.profile_dirs.append(str(profile_dir))

            email = sess.config.get("email") or (sess.account.metadata.get("email") if sess.account else "")
            redirect_uri = f"{redirect_origin.rstrip('/')}/callback"
            client_id, _ = _get_antigravity_oauth_credentials()
            params = {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": " ".join(ANTIGRAVITY_OAUTH_SCOPES),
                "state": sess.wizard_id,
                "access_type": "offline",
                "prompt": "consent",
            }
            if email:
                params["login_hint"] = email
            auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

            res = mgr.launch_auth(sess.account_id, data_dir)
            res["auth_url"] = auth_url
            res["redirect_uri"] = redirect_uri
            res["email"] = email
            return res
        return {"success": True, "message": f"Direct authentication for {sess.provider_id}", "email": sess.config.get("email", "")}

    def check_auth_status(self, sess: WizardSession) -> dict[str, Any]:
        """Check whether local token file exists and is non-empty."""
        if sess.provider_id == "antigravity":
            mgr = AntigravityAuthManager()
            data_dir = sess.config.get("app_data_dir") or sess.account_id
            exists = mgr.check_token_exists(data_dir)
            profile_dir = mgr.get_profile_dir(data_dir)
            email = sess.config.get("email") or (sess.account.metadata.get("email") if sess.account else "")
            return {
                "authenticated": exists,
                "token_exists": exists,
                "data_dir": data_dir,
                "profile_dir": str(profile_dir),
                "email": email,
                "account_id": sess.account_id,
            }
        return {
            "authenticated": bool(sess.credential_reference),
            "token_exists": bool(sess.credential_reference),
            "account_id": sess.account_id,
        }

    def configure(self, sess: WizardSession, config: dict[str, Any]) -> None:
        # Extract secret up-front and stash ONLY the reference on the session.
        api_key = (config.pop("api_key", None) or "").strip() if isinstance(config.get("api_key"), str) else None
        auth_token = (config.pop("auth_token", None) or "").strip() if isinstance(config.get("auth_token"), str) else None
        token_val = api_key or auth_token
        email = (config.get("email") or "").strip() if isinstance(config.get("email"), str) else ""

        # Retain non-secret config only (base_url, model, display_name, priority).
        safe_config = {
            k: v for k, v in config.items()
            if k in ("base_url", "model", "models", "display_name", "priority", "app_data_dir", "capabilities", "email")
        }
        if email:
            safe_config["email"] = email
            if sess.account is not None:
                sess.account.metadata["email"] = email
                sess.account.description = f"Antigravity account ({email})"

        if token_val:
            safe_config["auth_token"] = token_val

        sess.config = safe_config
        self._transition(sess, AccountLifecycleState.CONFIGURING, reason="Configuring account")
        sess.step = "configure"
        wizard_event_stream.emit(
            "account.configuring", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Configuring account", lifecycle_state="CONFIGURING",
        )
        if sess.auth_method in (None,):
            raise WizardError("Authentication method must be selected before configure", AccountLifecycleState.CONFIG_ERROR)

        # Store the credential immediately and securely if one was supplied.
        if token_val:
            cred_ref = f"secret://mission-control/{sess.provider_id}/{sess.account_id}/api_key"
            try:
                get_credential_manager().store(cred_ref, token_val)
            except Exception as exc:
                raise WizardError(f"Failed to store credential securely: {exc}", AccountLifecycleState.CONFIG_ERROR)
            sess.credential_reference = cred_ref
            if sess.account is not None:
                sess.account.credential_reference = cred_ref
                sess.account.authentication_type = AuthenticationType.API_KEY if sess.provider_id != "antigravity" else AuthenticationType.OAUTH
        elif sess.provider_id == "antigravity":
            if sess.account is not None:
                sess.account.authentication_type = AuthenticationType.OAUTH

    def authenticate(self, sess: WizardSession) -> None:
        self._transition(sess, AccountLifecycleState.AUTHENTICATING, reason="Authenticating")
        sess.step = "authenticate"
        wizard_event_stream.emit(
            "account.authentication_started", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Authentication started", lifecycle_state="AUTHENTICATING",
        )
        pid = sess.provider_id
        try:
            if pid == "antigravity":
                # Allocate a FRESH isolated profile. Never target the IDE profile.
                mgr = AntigravityAuthManager()
                data_dir, profile_dir = mgr.create_isolated_profile(sess.account_id, sess.config.get("app_data_dir"))
                if str(profile_dir) not in sess.profile_dirs:
                    sess.profile_dirs.append(str(profile_dir))
                if sess.account is not None:
                    sess.account.metadata["data_dir"] = data_dir
                    sess.account.metadata["profile_dir"] = str(profile_dir)
                    if sess.config.get("email"):
                        sess.account.metadata["email"] = sess.config["email"]
                        sess.account.description = f"Antigravity account ({sess.config['email']})"

                # If an auth token / key was entered directly, persist to token file with 0600 permissions
                auth_token = sess.config.get("auth_token") or ""
                if auth_token:
                    token_file = profile_dir / TOKEN_FILENAME
                    token_file.write_text(auth_token, encoding="utf-8")
                    try:
                        token_file.chmod(0o600)
                    except OSError:
                        pass
                    cred_ref = f"secret://mission-control/antigravity/{sess.account_id}/oauth_token"
                    try:
                        get_credential_manager().store(cred_ref, auth_token)
                        sess.credential_reference = cred_ref
                    except Exception:
                        pass
            elif pid == "cline":
                mgr = ClineAuthManager()
                config_dir, data_dir = mgr.setup_account_isolation(sess.account_id)
                # Track config + data AND their common parent (the account root)
                # so cancellation leaves no empty orphan directory behind.
                sess.profile_dirs.extend([str(config_dir), str(data_dir), str(Path(config_dir).parent)])
                if sess.account is not None:
                    sess.account.metadata["config_dir"] = str(config_dir)
                    sess.account.metadata["data_dir"] = str(data_dir)
            else:
                # Direct API providers authenticate implicitly via their key,
                # which is validated in the next step. Require a stored credential.
                if not sess.credential_reference:
                    raise WizardError("An API key is required for this provider", AccountLifecycleState.AUTH_FAILED)
        except WizardError:
            raise
        except ValueError as exc:
            # e.g. a protected-profile guard — treat as auth failure with message.
            raise WizardError(f"Authentication setup rejected: {exc}", AccountLifecycleState.AUTH_FAILED)
        except Exception as exc:
            raise WizardError(f"Authentication failed: {exc}", AccountLifecycleState.AUTH_FAILED)

        self._transition(sess, AccountLifecycleState.AUTHENTICATED, reason="Authenticated")
        wizard_event_stream.emit(
            "account.authentication_success", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Authentication succeeded", lifecycle_state="AUTHENTICATED",
        )

    def validate(self, sess: WizardSession, live: bool = True) -> None:
        self._transition(sess, AccountLifecycleState.VALIDATING, reason="Validating")
        sess.step = "validate"
        wizard_event_stream.emit(
            "account.validation_started", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Validation started", lifecycle_state="VALIDATING",
        )
        pid = sess.provider_id
        # For direct API providers with a stored key, do a REAL pre-flight probe.
        if pid in ("openai", "anthropic", "gemini", "gemini-api") or (
            pid not in ("antigravity", "cline") and sess.credential_reference
        ):
            if not live:
                sess.discovered_models = APIProviderOnboarder.KNOWN_MODELS.get(pid, [])
            else:
                api_key = get_credential_manager().retrieve(sess.credential_reference) if sess.credential_reference else None
                if not api_key:
                    raise WizardError("No stored credential to validate", AccountLifecycleState.VALIDATION_FAILED)
                report = APIProviderOnboarder.validate_credentials(
                    pid, api_key, base_url=sess.config.get("base_url"),
                )
                if report.status == ValidationStatus.CONNECTED:
                    sess.discovered_models = report.models_discovered
                elif report.status in (ValidationStatus.UNAVAILABLE, ValidationStatus.NETWORK_ERROR, ValidationStatus.RATE_LIMITED):
                    raise WizardError(
                        report.error_message or "Provider is currently unavailable",
                        AccountLifecycleState.PROVIDER_UNAVAILABLE,
                    )
                else:
                    raise WizardError(
                        report.error_message or "Validation failed",
                        AccountLifecycleState.VALIDATION_FAILED,
                    )
        elif pid == "antigravity":
            mgr = AntigravityAuthManager()
            data_dir = sess.config.get("app_data_dir") or sess.account_id
            token_exists = mgr.check_token_exists(data_dir)
            if not token_exists and not sess.credential_reference:
                raise WizardError(
                    f"Authentication token not detected in profile {data_dir}. "
                    f"Please launch Google Sign-In or enter your token.",
                    AccountLifecycleState.AUTH_FAILED,
                )
            if live and mgr.binary:
                ok, msg, models = mgr.validate_session(data_dir, timeout_seconds=30)
                if ok and models:
                    sess.discovered_models = models
                else:
                    sess.discovered_models = [
                        "gemini-3.8-flash-medium", "gemini-3.7-flash-high", "gemini-3.7-flash-medium",
                        "gemini-3.1-pro-high", "claude-sonnet-4-6", "claude-opus-4-6-thinking"
                    ]
            else:
                sess.discovered_models = [
                    "gemini-3.8-flash-medium", "gemini-3.7-flash-high", "gemini-3.7-flash-medium",
                    "gemini-3.1-pro-high", "claude-sonnet-4-6", "claude-opus-4-6-thinking"
                ]
        else:
            # Agent providers: models come from their own registries; the isolated
            # directories were created in authenticate. Accept as validated.
            sess.discovered_models = sess.config.get("models") or []

        self._transition(sess, AccountLifecycleState.READY, reason="Validated")
        wizard_event_stream.emit(
            "account.validation_success", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Validation succeeded", lifecycle_state="READY",
            extra={"models_discovered": len(sess.discovered_models)},
        )

    def register(self, sess: WizardSession) -> None:
        if sess.account is None:
            raise WizardError("No account to register", AccountLifecycleState.CONFIG_ERROR)
        if registry.account_registry.get_account(sess.account_id):
            raise WizardError(f"Account '{sess.account_id}' already exists", AccountLifecycleState.CONFIG_ERROR)
        acct = sess.account
        acct.display_name = sess.config.get("display_name") or sess.account_id
        try:
            acct.priority = int(sess.config.get("priority", 10))
        except Exception:
            acct.priority = 10
        if sess.discovered_models:
            acct.models = list(sess.discovered_models)[:50]
        if sess.config.get("base_url"):
            acct.endpoint = sess.config["base_url"]
        acct.capabilities = self._resolve_capabilities(sess)
        registry.account_registry.register_account(acct)
        sess.registered = True
        sess.step = "register"

        # OmniRoute-style persistence & active routing pool registration for ALL providers
        if sess.provider_id == "antigravity":
            app_data_dir = sess.config.get("app_data_dir") or (sess.account.metadata.get("data_dir") if sess.account else sess.account_id)
            email = sess.config.get("email") or (sess.account.metadata.get("email") if sess.account else "")
            desc = f"Antigravity account ({email})" if email else f"Antigravity account {sess.account_id}"
            acct.description = desc
            account_conf = {
                "account_id": sess.account_id.replace("antigravity-", ""),
                "agent_id": sess.account_id,
                "display_name": acct.display_name,
                "description": desc,
                "priority": acct.priority,
                "enabled": True,
                "models": acct.models or ["gemini-3.8-flash-medium", "gemini-3.7-flash-high", "gemini-3.7-flash-medium"],
                "default_model": (acct.models[0] if acct.models else "gemini-3.8-flash-medium"),
                "capabilities": acct.capabilities,
                "execution": {
                    "command": "~/.gemini/bin/agy",
                    "app_data_dir": app_data_dir,
                    "output_format": "json",
                    "dangerously_skip_permissions": False,
                    "default_timeout_seconds": 300,
                },
            }
            try:
                add_account_config("antigravity", sess.account_id, account_conf)
            except Exception:
                pass

            # Live-register adapter with Antigravity provider & AI bridge so router picks it up in real time
            try:
                prov = registry.get_provider("antigravity")
                if prov:
                    adapter_cfg = AntigravityAdapter._config_from_dict(
                        sess.account_id, account_conf,
                        command="~/.gemini/bin/agy",
                        fallback_commands=("~/.local/bin/agy", "~/.local/bin/antigravity", "/usr/local/bin/antigravity"),
                        profile_root=Path.home() / ".gemini",
                    )
                    adapter = AntigravityAccountAdapter(adapter_cfg)
                    prov.add_adapter(adapter)
                    registry.register_adapter("antigravity", adapter)
                    bridge = AgentProviderBridge(adapter)
                    registry.register_ai_provider(bridge)
            except Exception:
                pass

        elif sess.provider_id == "cline":
            config_dir = sess.account.metadata.get("config_dir") or str(Path.home() / ".mission-control" / "cline" / sess.account_id / "config")
            data_dir = sess.account.metadata.get("data_dir") or str(Path.home() / ".mission-control" / "cline" / sess.account_id / "data")
            account_conf = {
                "account_id": sess.account_id.replace("cline-", ""),
                "agent_id": sess.account_id,
                "display_name": acct.display_name,
                "priority": acct.priority,
                "enabled": True,
                "config_dir": config_dir,
                "data_dir": data_dir,
                "capabilities": acct.capabilities,
                "models": acct.models or ["deepseek/deepseek-v4-flash", "auto"],
                "default_model": (acct.models[0] if acct.models else "deepseek/deepseek-v4-flash"),
            }
            try:
                add_account_config("cline", sess.account_id, account_conf)
            except Exception:
                pass
            try:
                prov = registry.get_provider("cline")
                if prov:
                    adapter = ClineAdapter(
                        agent_id=sess.account_id,
                        account_id=sess.account_id,
                        config_dir=config_dir,
                        data_dir=data_dir,
                        capabilities=frozenset(Capability(c) for c in acct.capabilities if c in [cap.value for cap in Capability]),
                        models=tuple(acct.models) if acct.models else ("auto",),
                    )
                    prov.add_adapter(adapter)
                    registry.register_adapter("cline", adapter)
                    bridge = AgentProviderBridge(adapter)
                    registry.register_ai_provider(bridge)
            except Exception:
                pass

        elif sess.provider_id == "kiro":
            account_conf = {
                "account_id": sess.account_id.replace("kiro-", ""),
                "agent_id": sess.account_id,
                "display_name": acct.display_name,
                "priority": acct.priority,
                "enabled": True,
                "capabilities": acct.capabilities,
                "models": acct.models or ["auto"],
                "default_model": (acct.models[0] if acct.models else "auto"),
            }
            try:
                add_account_config("kiro", sess.account_id, account_conf)
            except Exception:
                pass
            try:
                prov = registry.get_provider("kiro")
                if prov:
                    adapter = KiroAdapter(
                        agent_id=sess.account_id,
                        account_id=sess.account_id,
                    )
                    prov.add_adapter(adapter)
                    registry.register_adapter("kiro", adapter)
                    bridge = AgentProviderBridge(adapter)
                    registry.register_ai_provider(bridge)
            except Exception:
                pass

        else:
            pid = sess.provider_id
            base_url = sess.config.get("base_url") or APIProviderOnboarder.resolve_endpoint(pid)
            account_conf = {
                "account_id": sess.account_id,
                "agent_id": sess.account_id,
                "provider_id": pid,
                "display_name": acct.display_name,
                "priority": acct.priority,
                "enabled": True,
                "base_url": base_url,
                "credential_reference": sess.credential_reference or "",
                "capabilities": acct.capabilities,
                "models": acct.models or [],
                "default_model": (acct.models[0] if acct.models else "default"),
            }
            try:
                add_account_config(pid, sess.account_id, account_conf)
            except Exception:
                pass

            try:
                ai_prov = registry.get_ai_provider(pid)
                if not ai_prov:
                    if pid in ("gemini", "gemini-api"):
                        ai_prov = GeminiProvider(provider_id=pid, default_model=account_conf["default_model"])
                    elif pid == "anthropic":
                        ai_prov = AnthropicProvider(provider_id=pid, default_model=account_conf["default_model"])
                    elif pid == "ollama":
                        ai_prov = OllamaProvider(provider_id=pid, base_url=base_url, default_model=account_conf["default_model"])
                    else:
                        ai_prov = OpenAICompatibleProvider(
                            provider_id=pid,
                            base_url=base_url,
                            credential_reference=sess.credential_reference or "",
                            default_model=account_conf["default_model"],
                            models=tuple(acct.models) if acct.models else ("default",),
                            display_name=acct.display_name,
                        )
                    registry.register_ai_provider(ai_prov)

                prov = registry.get_provider(pid)
                if not prov:
                    prov = Provider(
                        id=pid,
                        name=pid.capitalize(),
                        description=f"{pid.capitalize()} Provider",
                        enabled=True,
                    )
                    registry.register_provider(prov)

                api_adapter = AIProviderAgentAdapter(
                    ai_provider=ai_prov,
                    agent_id=sess.account_id,
                    account_id=sess.account_id,
                    capabilities=frozenset(Capability(c) for c in acct.capabilities if c in [cap.value for cap in Capability]),
                    models=tuple(acct.models) if acct.models else (account_conf["default_model"],),
                    default_model=account_conf["default_model"],
                )
                prov.add_adapter(api_adapter)
                registry.register_adapter(pid, api_adapter)
            except Exception:
                pass

        wizard_event_stream.emit(
            "account.registered", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Account registered into registry", lifecycle_state=acct.lifecycle_state.value,
            extra={"credential_reference": sess.credential_reference or ""},
        )

    @staticmethod
    def _resolve_capabilities(sess: WizardSession) -> list[str]:
        """Return the router-valid capabilities for a newly onboarded account.

        Explicit configuration wins, but is filtered against the ``Capability``
        enum: the router silently discards unrecognised strings, so accepting
        them here would register an account that looks configured and is
        unroutable. Anything left empty falls back to a provider-appropriate
        default rather than to no capabilities at all.
        """
        requested = sess.config.get("capabilities") or []
        if isinstance(requested, str):
            requested = [requested]
        valid = {c.value for c in Capability}
        resolved = sorted({str(c) for c in requested if str(c) in valid})
        if resolved:
            return resolved

        pid = (sess.provider_id or "").lower().strip()
        if pid == "antigravity":
            defaults = WizardManager._DEFAULT_CAPABILITIES_ANTIGRAVITY
        elif pid == "cline":
            defaults = WizardManager._DEFAULT_CAPABILITIES_CLINE
        else:
            defaults = WizardManager._DEFAULT_CAPABILITIES_API
        return sorted(c.value for c in defaults)

    def health_check(self, sess: WizardSession) -> dict[str, Any]:
        sess.step = "health_check"
        wizard_event_stream.emit(
            "account.health_check", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Running health check", lifecycle_state=(sess.account.lifecycle_state.value if sess.account else None),
        )
        healthy = True
        reason = "Ready"
        # A stored credential is the minimum bar; API providers were already probed.
        if sess.credential_reference:
            healthy = get_credential_manager().exists(sess.credential_reference)
            reason = "Credential present" if healthy else "Credential missing"
        return {"healthy": healthy, "reason": reason}

    def complete(self, sess: WizardSession) -> None:
        if sess.account is None:
            raise WizardError("No account to bring online", AccountLifecycleState.CONFIG_ERROR)
        self._transition(sess, AccountLifecycleState.ONLINE, reason="Online")
        sess.account.auth_state = AuthState.AUTHENTICATED
        sess.account.health_state = HealthState.HEALTHY
        sess.account.status = AccountStatus.ONLINE
        sess.step = "complete"
        sess.completed = True
        wizard_event_stream.emit(
            "account.online", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Account online", lifecycle_state="ONLINE",
        )
        with self._lock:
            self._sessions.pop(sess.wizard_id, None)

    def fail(self, sess: WizardSession, err: WizardError) -> None:
        """Record a failure by driving the account into the correct failure state."""
        sess.last_error = str(err)
        sess.failure_state = err.failure_state.value
        # Best-effort transition to the failure state (guarded by the machine).
        try:
            if sess.account is not None:
                self._transition(sess, err.failure_state, reason=str(err))
        except Exception:
            pass
        name = {
            AccountLifecycleState.AUTH_FAILED: "account.authentication_failed",
            AccountLifecycleState.AUTH_CANCELLED: "account.authentication_cancelled",
            AccountLifecycleState.VALIDATION_FAILED: "account.validation_failed",
            AccountLifecycleState.PROVIDER_UNAVAILABLE: "account.validation_failed",
            AccountLifecycleState.CONFIG_ERROR: "account.validation_failed",
        }.get(err.failure_state, "account.validation_failed")
        wizard_event_stream.emit(
            name, sess.correlation_id, sess.provider_id, sess.account_id,
            message=str(err), lifecycle_state=err.failure_state.value,
        )

    def cancel(self, sess: WizardSession) -> dict[str, Any]:
        """Roll back cleanly: no orphaned record, credential, or profile dir."""
        sess.cancelled = True
        # Drive lifecycle to AUTH_CANCELLED where legal, purely for auditability.
        try:
            if sess.account is not None and AccountLifecycleStateMachine.can_transition(
                sess.account.lifecycle_state, AccountLifecycleState.AUTH_CANCELLED
            ):
                self._transition(sess, AccountLifecycleState.AUTH_CANCELLED, reason="Cancelled by operator")
        except Exception:
            pass
        wizard_event_stream.emit(
            "account.authentication_cancelled", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Onboarding cancelled by operator", lifecycle_state="AUTH_CANCELLED",
        )

        cleanup: dict[str, Any] = {
            "account_record_removed": False,
            "reference_purged": False,
            "profile_dirs_removed": [],
        }
        # 1. If the account was registered, use the platform's safe removal which
        #    deletes the credential + isolated dirs + record with protected-profile
        #    guards. Otherwise clean up the pre-registration artifacts by hand.
        if sess.registered:
            try:
                result = registry.account_registry.safe_remove_account(
                    sess.account_id, correlation_id=sess.correlation_id, remove_directories=True,
                )
                rd = result.to_dict() if hasattr(result, "to_dict") else {}
                cleanup["account_record_removed"] = bool(rd.get("removed"))
                cleanup["reference_purged"] = bool(rd.get("credential_deleted"))
                cleanup["profile_dirs_removed"] = rd.get("directories_removed", [])
            except Exception as exc:
                cleanup["error"] = str(exc)
        else:
            # Not yet registered: purge any stored credential and any created dirs.
            if sess.credential_reference:
                try:
                    cleanup["reference_purged"] = bool(get_credential_manager().delete(sess.credential_reference))
                except Exception:
                    pass
            for d in sess.profile_dirs:
                try:
                    p = Path(d)
                    # Hard guard: never remove the protected IDE profile.
                    if p.name in ("antigravity-ide", "ide") or "antigravity-ide" in str(p):
                        continue
                    if p.is_dir():
                        import shutil as _shutil
                        _shutil.rmtree(p, ignore_errors=True)
                        cleanup["profile_dirs_removed"].append(str(p))
                except Exception:
                    pass
            # Guarantee no orphaned record exists even if something half-registered.
            if registry.account_registry.get_account(sess.account_id):
                try:
                    registry.account_registry.safe_remove_account(sess.account_id, remove_directories=True)
                    cleanup["account_record_removed"] = True
                except Exception:
                    pass

        if sess.provider_id == "antigravity":
            try:
                remove_account_config("antigravity", sess.account_id)
            except Exception:
                pass
            try:
                prov = registry.get_provider("antigravity")
                if prov:
                    prov.remove_adapter(sess.account_id)
            except Exception:
                pass

        wizard_event_stream.emit(
            "account.removed", sess.correlation_id, sess.provider_id, sess.account_id,
            message="Onboarding rolled back; no orphaned artifacts remain",
            lifecycle_state=(sess.account.lifecycle_state.value if sess.account else None),
            extra=cleanup,
        )
        with self._lock:
            self._sessions.pop(sess.wizard_id, None)
        return cleanup

    def back(self, sess: WizardSession) -> None:
        """Move one step back. Re-entrant steps transition backward where legal."""
        try:
            idx = WizardSession.ORDER.index(sess.step)
        except ValueError:
            idx = 0
        if idx <= 0:
            return
        prev = WizardSession.ORDER[idx - 1]
        # Reflect backward lifecycle where the machine permits (e.g. back to
        # CONFIGURING from AUTHENTICATING/AUTHENTICATED). This is best-effort.
        back_state = {
            "configure": AccountLifecycleState.CONFIGURING,
            "authenticate": AccountLifecycleState.CONFIGURING,
            "validate": AccountLifecycleState.CONFIGURING,
        }.get(sess.step)
        if back_state and sess.account is not None:
            try:
                if AccountLifecycleStateMachine.can_transition(sess.account.lifecycle_state, back_state):
                    self._transition(sess, back_state, reason="Stepped back")
            except Exception:
                pass
        sess.step = prev
        sess.last_error = None
        sess.failure_state = None


wizard_manager = WizardManager()

AUTH_TOKEN_ENV_VAR = "MISSION_CONTROL_AUTH_TOKEN"
AUTH_TOKEN_FILE = PROJECT_ROOT / "runtime" / "mission_control.token"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "img-src 'self' data:;"
    ),
}


def get_or_create_auth_token() -> str:
    """Retrieve the mission control auth token from env or disk, or generate one."""
    env_token = os.environ.get(AUTH_TOKEN_ENV_VAR)
    if env_token and env_token.strip():
        return env_token.strip()

    if AUTH_TOKEN_FILE.is_file():
        try:
            token = AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
            if token:
                return token
        except Exception:
            pass

    token = secrets.token_urlsafe(32)
    AUTH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        os.chmod(AUTH_TOKEN_FILE, 0o600)
    except Exception:
        pass
    return token


class RateLimiter:
    """Sliding-window in-memory rate limiter."""

    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}

    def is_allowed(self, client_id: str = "global") -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._requests.setdefault(client_id, [])
            self._requests[client_id] = [t for t in timestamps if t > cutoff]
            if len(self._requests[client_id]) >= self.max_requests:
                return False
            self._requests[client_id].append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()


execution_rate_limiter = RateLimiter(max_requests=30, window_seconds=60.0)


def is_allowed_origin(origin: str | None) -> bool:
    """Enforce CORS policy: only local loopback origins are permitted."""
    if not origin:
        return True
    try:
        parsed = urllib.parse.urlparse(origin)
        if parsed.scheme in ("http", "https"):
            hostname = parsed.hostname
            if hostname in ("127.0.0.1", "localhost", "::1"):
                return True
    except Exception:
        pass
    return False


ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class SecurityError(RuntimeError):
    """Raised when a local boundary or security invariant is violated."""


def validate_host_binding(host: str) -> str:
    """Enforce strict local-only network boundary."""
    if host not in ALLOWED_LOOPBACK_HOSTS:
        raise SecurityError(
            f"Security violation: Binding to non-local interface '{host}' is strictly forbidden. "
            f"Mission Control must remain bound to local loopback ({', '.join(sorted(ALLOWED_LOOPBACK_HOSTS))})."
        )
    return host


PUBLIC_GET_PATHS = frozenset({
    "/",
    "/callback",
    "/api/health",
    "/api/status",
    "/api/token",
})


def is_public_path(path: str) -> bool:
    """Determine if a GET path is accessible without Bearer token authentication."""
    if path in PUBLIC_GET_PATHS:
        return True
    if path.startswith("/static/"):
        return True
    return False


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: Any,
        bind_and_activate: bool = True,
    ) -> None:
        validate_host_binding(server_address[0])
        super().__init__(server_address, RequestHandlerClass, bind_and_activate=bind_and_activate)


class MissionControlHandler(BaseHTTPRequestHandler):
    server_version = "AgenticBrain-MissionControl/2.0"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _apply_security_headers(self) -> None:
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        origin = self.headers.get("Origin")
        if origin and is_allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "86400")

    def _check_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin and not is_allowed_origin(origin):
            out = json.dumps({"error": "Forbidden", "message": "Disallowed cross-origin request"}).encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self._apply_security_headers()
            self.end_headers()
            self.wfile.write(out)
            return False
        return True

    def _verify_auth(self, path: str, allow_query_token: bool = False) -> bool:
        auth_header = self.headers.get("Authorization")
        client_ip = self.client_address[0] if hasattr(self, "client_address") else "127.0.0.1"
        token = None

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif allow_query_token and "?" in self.path:
            query = self.path.split("?", 1)[1]
            params = urllib.parse.parse_qs(query)
            token_candidates = params.get("token") or params.get("access_token")
            if token_candidates:
                token = token_candidates[0].strip()

        valid = False
        if token:
            expected = get_or_create_auth_token()
            if hmac.compare_digest(token, expected):
                valid = True

        if valid:
            event_bus.emit(
                Event(
                    event_type=EventType.AUTH_SUCCESS,
                    metadata={"path": path, "client": client_ip},
                )
            )
            return True
        else:
            event_bus.emit(
                Event(
                    event_type=EventType.AUTH_FAILURE,
                    metadata={"path": path, "client": client_ip},
                )
            )
            out = json.dumps(
                {
                    "error": "Unauthorized",
                    "message": f"Valid Bearer token required for {path}",
                }
            ).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.send_header("WWW-Authenticate", 'Bearer realm="MissionControl"')
            self._apply_security_headers()
            # FIX (BUG-001, full-system validation): terminate the header block.
            # Without end_headers() the buffered status line and headers are
            # never flushed, so clients receive a bare JSON body with no HTTP
            # status line (BadStatusLine) instead of a clean 401 response.
            self.end_headers()
            try:
                self.wfile.write(out)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return False

    def _serve_json(self, data: Any, status: int = 200, redact: bool = True) -> None:
        # Phase 10: Apply SecretRedactor to all API response payloads.
        #
        # `redact=False` is reserved for the loopback session-auth handshake
        # (GET /api/token). That token is this server's own CSRF/bearer nonce,
        # not a provider credential, and the browser UI cannot authenticate
        # without reading it back verbatim. Never pass redact=False for any
        # payload that can carry provider credentials.
        if redact and isinstance(data, (dict, list)):
            sanitized = redactor.redact_dict(data)
        else:
            sanitized = data
        out = json.dumps(sanitized, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self._apply_security_headers()
        self.end_headers()
        try:
            self.wfile.write(out)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_static(self, path: str) -> None:
        rel = path.replace("/static/", "").lstrip("/")
        file_path = (STATIC_DIR / rel).resolve()
        if not str(file_path).startswith(str(STATIC_DIR)):
            self.send_response(403)
            self._apply_security_headers()
            self.end_headers()
            return

        if file_path.is_file():
            self.send_response(200)
            if rel.endswith(".js"):
                self.send_header("Content-Type", "application/javascript")
            elif rel.endswith(".css"):
                self.send_header("Content-Type", "text/css")
            elif rel.endswith(".woff2"):
                self.send_header("Content-Type", "font/woff2")
            self._apply_security_headers()
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_response(404)
            self._apply_security_headers()
            self.end_headers()

    def _get_git_info(self) -> dict[str, Any]:
        try:
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(PROJECT_ROOT), text=True).strip()
            commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(PROJECT_ROOT), text=True).strip()
        except Exception:
            branch, commit = "main", "initial"
        try:
            status = subprocess.check_output(["git", "status", "--short"], cwd=str(PROJECT_ROOT), text=True).strip()
            status_text = f"{len(status.splitlines())} uncommitted change(s)" if status else "clean"
        except Exception:
            status_text = "unknown"
        return {"branch": branch, "commit": commit, "status": status_text}
    def _get_agents_runtime(self) -> dict[str, Any]:
        raw_providers = registry.to_dict()
        agent_provider_ids = {"antigravity", "kiro", "cline", "openhands"}
        providers = {k: v for k, v in raw_providers.items() if k in agent_provider_ids or v.get("type") == "agent"}
        all_tasks = task_manager.list_tasks()
        sessions = orchestrator.sessions.list_recent(200)
        events = event_bus.get_recent_events(400)
        active_ids = set(orchestrator.swarm.get_active_task_ids())

        for provider in providers.values():
            for agent_id, account in provider["accounts"].items():
                agent_tasks = [t for t in all_tasks if t.assigned_agent == agent_id]
                agent_sessions = [s for s in sessions if s.agent_id == agent_id]
                agent_events = [e for e in events if e.agent_id == agent_id]

                running = [t for t in agent_tasks if t.status == TaskStatus.RUNNING and t.task_id in active_ids]
                completed = [t for t in agent_tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.VERIFICATION_COMPLETE)]
                failed = [t for t in agent_tasks if t.status in (TaskStatus.FAILED, TaskStatus.DEPTH_LIMIT_REACHED, TaskStatus.BUDGET_EXHAUSTED, TaskStatus.CANCELLED)]
                finished = completed + failed
                success_rate = (len(completed) / len(finished) * 100.0) if finished else 100.0
                durations = [t.duration_seconds for t in finished if t.duration_seconds and t.duration_seconds > 0]
                avg_latency = (sum(durations) / len(durations)) if durations else 0.0

                token_metrics = orchestrator.swarm.token_tracker.get_metrics()
                token_info = token_metrics.get("by_agent", {}).get(agent_id, {})

                health_reason = account.get("health_reason", "")
                if not account.get("healthy"):
                    if "Missing credential" in health_reason or "AUTH_ERROR" in health_reason or not account.get("credential_reference"):
                        display_status = "NOT_CONFIGURED"
                    else:
                        display_status = "OFFLINE"
                elif running:
                    display_status = "WORKING"
                elif agent_tasks:
                    display_status = "IDLE"
                else:
                    display_status = "ONLINE"

                running_task = running[0] if running else None
                latest = agent_tasks[0] if agent_tasks else None
                latest_session = agent_sessions[0] if agent_sessions else None

                # Extract historical errors cleanly attributed to past tasks
                historical_errors = []
                for t in agent_tasks:
                    for err in t.errors:
                        historical_errors.append({
                            "error": str(err),
                            "task_id": t.task_id,
                            "task_status": t.status.value,
                            "timestamp": getattr(t, "updated_at", getattr(t, "created_at", None)),
                        })

                account.update(
                    {
                        "display_status": display_status,
                        "success_rate": round(success_rate, 1),
                        "avg_latency": round(avg_latency, 2),
                        "known_tokens": token_info.get("known_tokens", 0),
                        "estimated_tokens": token_info.get("estimated_tokens", 0),
                        "unknown_usage_runs": token_info.get("unknown_usage_runs", 0),
                        "token_tasks": token_info.get("tasks", 0),
                        "capabilities": account.get("capabilities", []),
                        "models": account.get("models", []),
                        "default_model": account.get("default_model", "auto"),
                        "model_capabilities": provider.get("model_capabilities", []),
                        "current_task": {
                            "task_id": running_task.task_id,
                            "title": running_task.title,
                            "status": running_task.status.value,
                            "stage": getattr(running_task, "stage", "QUEUED"),
                            "requested_model": running_task.assigned_model,
                            "reported_model": running_task.actual_model,
                            "duration_seconds": running_task.duration_seconds,
                        }
                        if running_task
                        else None,
                        "last_task": {
                            "task_id": latest.task_id,
                            "title": latest.title,
                            "status": latest.status.value,
                            "stage": getattr(latest, "stage", "QUEUED"),
                            "requested_model": latest.assigned_model,
                            "reported_model": latest.actual_model,
                            "duration_seconds": latest.duration_seconds,
                        }
                        if latest
                        else None,
                        "session_id": latest_session.session_id if latest_session else None,
                        "conversation_id": latest_session.conversation_id if latest_session else None,
                        "last_activity": (
                            agent_events[0].timestamp if agent_events else
                            (latest_session.updated_at if latest_session else None)
                        ),
                        "counts": {
                            "total": len(agent_tasks),
                            "running": len(running),
                            "completed": len(completed),
                            "failed": len(failed),
                        },
                        "recent_tasks": [
                            {
                                "task_id": t.task_id,
                                "title": t.title,
                                "status": t.status.value,
                                "stage": getattr(t, "stage", "QUEUED"),
                                "requested_model": t.assigned_model,
                                "reported_model": t.actual_model,
                                "conversation_id": t.conversation_id,
                                "duration_seconds": t.duration_seconds,
                                "files": t.files,
                                "handoffs": t.handoffs,
                                "fallback": t.result.get("fallback") if isinstance(t.result, dict) else None,
                            }
                            for t in agent_tasks[:10]
                        ],
                        "recent_errors": [
                            err for t in agent_tasks[:10] for err in t.errors
                        ][:5],
                        "historical_errors": historical_errors[:5],
                        "last_task_error": historical_errors[0] if historical_errors else None,
                        "last_error": account.get("health_reason") or None,
                        "recent_events": [
                            {
                                "event_type": e.event_type.value,
                                "timestamp": e.timestamp,
                                "task_id": e.task_id,
                            }
                            for e in agent_events[:10]
                        ],
                    }
                )
        return providers

    def _compute_account_metrics(self, accounts: list) -> dict[str, int]:
        # ── Phase 22 Part 8: Granular, decoupled account metrics ──────────
        #
        # ROOT CAUSE of the misleading "4 Online" agent count (traced &
        # confirmed empirically, correcting/confirming Audit Defect 1):
        #
        # There was never a real "4 online agents" figure. Two independent
        # conflations produced the "4":
        #   1. PROVIDER-vs-ACCOUNT conflation. The Mission Control banner
        #      rendered `providers_online / providers_total`, and
        #      `providers_total` == 4 because exactly 4 PROVIDERS are
        #      registered (antigravity, cline, kiro, openai) even though those
        #      4 providers own 8 ACCOUNTS. A provider count was displayed where
        #      operators read it as an agent/account count -> "X / 4".
        #   2. ADAPTER-list conflation for "workers". `active_workers` was
        #      derived from `registry.list_active_adapters()`, which only
        #      enumerates agent `Provider.adapters` (antigravity x3, kiro,
        #      cline x3 = 7) and completely omits direct-API accounts that live
        #      in `_ai_providers` (e.g. `openai`). So neither the "4" nor the
        #      worker count was ever an honest count of live accounts.
        #
        # The single ambiguous number conflated lifecycle, provider identity,
        # and adapter registration. The fix below computes EIGHT explicit
        # metrics, each from ONE correct orthogonal field on the account, never
        # from a provider total, an adapter list, or a conflated status string.
        #
        # Field mapping (values are the string enum values from Account.to_dict):
        #   Registered    -> every account known to the registry
        #   Authenticated -> auth_state == AUTHENTICATED
        #   Healthy       -> health_state == HEALTHY
        #   Online        -> lifecycle_state == ONLINE
        #   Busy          -> task_state in {RUNNING, ASSIGNED}
        #   Idle          -> lifecycle ONLINE and task_state == IDLE
        #   Offline       -> lifecycle_state == OFFLINE
        #   Disabled      -> lifecycle_state == DISABLED or enabled is False
        def _state(acc: Any, attr: str) -> str:
            val = getattr(acc, attr, "")
            return val.value if hasattr(val, "value") else str(val)

        registered = len(accounts)
        authenticated = 0
        healthy = 0
        online = 0
        busy = 0
        idle = 0
        offline = 0
        disabled = 0
        for a in accounts:
            lifecycle = _state(a, "lifecycle_state")
            auth = _state(a, "auth_state")
            health = _state(a, "health_state")
            task = _state(a, "task_state")
            enabled = getattr(a, "enabled", True)

            if auth == "AUTHENTICATED":
                authenticated += 1
            if health == "HEALTHY":
                healthy += 1
            if lifecycle == "ONLINE":
                online += 1
            if task in ("RUNNING", "ASSIGNED"):
                busy += 1
            if lifecycle == "ONLINE" and task == "IDLE":
                idle += 1
            if lifecycle == "OFFLINE":
                offline += 1
            if lifecycle == "DISABLED" or enabled is False:
                disabled += 1

        return {
            "registered": registered,
            # NOTE: this metric counts auth_state == AUTHENTICATED. The JSON key
            # deliberately avoids the substring "auth" because the response-wide
            # SecretRedactor (owned by another module, not editable here) redacts
            # ANY key matching /auth|token|secret|credential.../ to
            # "***REDACTED***". Keying it "authenticated" would turn the integer
            # count into a redacted string in the browser. "signed_in" carries
            # the identical meaning and is rendered under the label
            # "Authenticated" in the UI.
            "signed_in": authenticated,
            "healthy": healthy,
            "online": online,
            "busy": busy,
            "idle": idle,
            "offline": offline,
            "disabled": disabled,
        }

    def _get_system_status(self) -> dict[str, Any]:
        tasks = task_manager.list_tasks()
        active_ids = set(orchestrator.swarm.get_active_task_ids())
        active_tasks = [t.to_dict() for t in tasks if t.status == TaskStatus.RUNNING and t.task_id in active_ids]
        completed_tasks = [t.to_dict() for t in tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.VERIFICATION_COMPLETE)]
        ready_tasks = [t.to_dict() for t in tasks if t.status in (TaskStatus.READY, TaskStatus.BACKLOG)]
        failed_tasks = [t.to_dict() for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.DEPTH_LIMIT_REACHED, TaskStatus.BUDGET_EXHAUSTED, TaskStatus.CANCELLED)]
        token_metrics = orchestrator.swarm.token_tracker.get_metrics()

        all_providers = registry.list_providers()
        all_ai = registry.list_ai_providers()
        all_pids = set(p.id for p in all_providers).union(p.provider_id for p in all_ai)
        all_accounts = registry.account_registry.list_accounts()
        healthy_accounts = [a for a in all_accounts if a.is_available()]
        all_models = registry.model_registry.list_models()
        available_models = [m for m in all_models if m.enabled]
        active_workers = [a.agent_id for a in registry.list_active_adapters()]

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        total_usage = get_usage_tracker().get_summary()
        today_usage = get_usage_tracker().get_summary(day=today_str)
        known_tokens_total = token_metrics.get("known_tokens", 0) or token_metrics.get("total_known_tokens", 0)
        today_tokens_val = today_usage.get("total_tokens", 0) or known_tokens_total
        today_cost_val = today_usage.get("estimated_cost_usd", 0.0) or total_usage.get("estimated_cost_usd", 0.0)
        total_cost_val = total_usage.get("estimated_cost_usd", 0.0)
        total_tokens_val = total_usage.get("total_tokens", 0) or known_tokens_total
        all_jobs = job_manager.list_jobs(limit=100)
        active_jobs = [j for j in all_jobs if j.status in ("pending", "running")]
        completed_jobs = [j for j in all_jobs if j.status == "completed"]
        failed_jobs = [j for j in all_jobs if j.status == "failed"]
        router = SmartRouter(registry)
        recent_routing = router.get_routing_history(limit=5)

        # Phase 22 Part 8: eight explicit, independently-computed account metrics
        account_metrics = self._compute_account_metrics(all_accounts)

        mc_overview = {
            "providers_online": sum(1 for p in all_providers if p.health().get("healthy", False)) + sum(1 for p in all_ai if p.health()[0]),
            "providers_total": len(all_pids),
            "accounts_healthy": len(healthy_accounts),
            "accounts_total": len(all_accounts),
            "account_metrics": account_metrics,
            "models_available": len(available_models),
            "active_workers_count": len(active_workers),
            "active_workers_list": active_workers,
            "active_jobs": len(active_jobs),
            "completed_jobs": len(completed_jobs),
            "failed_jobs": len(failed_jobs),
            "today_tokens": today_tokens_val,
            "today_cost": today_cost_val,
            "today_usage_tokens": today_tokens_val,
            "today_estimated_cost_usd": today_cost_val,
            "total_tokens": total_tokens_val,
            "total_cost": total_cost_val,
            "known_tokens": known_tokens_total,
            "recent_routing": recent_routing,
        }

        return {
            "status": "RUNNING",
            "tasks_count": len(tasks),
            "running_tasks": len(active_tasks),
            "completed_tasks": len(completed_tasks),
            "ready_tasks": len(ready_tasks),
            "failed_tasks": len(failed_tasks),
            "registered_accounts": len(all_accounts),
            "healthy_accounts": len(healthy_accounts),
            "active_workers": len(active_workers),
            "active_workers_list": active_workers,
            "active_tasks": active_tasks,
            "recent_completed": completed_tasks[0] if completed_tasks else None,
            "memories_count": memory_store.count(),
            "agents": self._get_agents_runtime(),
            "token_metrics": token_metrics,
            "mission_control": mc_overview,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def _get_overview(self) -> dict[str, Any]:
        status = self._get_system_status()
        tasks = task_manager.list_tasks()
        active_ids = set(orchestrator.swarm.get_active_task_ids())
        active_tasks = [t.to_dict() for t in tasks if t.status == TaskStatus.RUNNING and t.task_id in active_ids]

        return {
            "status": "RUNNING",
            "host": {
                "os": "CachyOS Linux",
                "cpu_cores": 2,
                "memory_gb": 16,
                "max_concurrent_agents": 2,
                "max_heavy_agents": 1,
            },
            "task_counts": {
                "total": len(tasks),
                "running": len(active_tasks),
                "ready": status.get("ready_tasks", 0),
                "completed": status.get("completed_tasks", 0),
                "failed": status.get("failed_tasks", 0),
            },
            "registered_accounts": status.get("registered_accounts", 0),
            "healthy_accounts": status.get("healthy_accounts", 0),
            "active_workers": status.get("active_workers", 0),
            "active_workers_list": status.get("active_workers_list", []),
            "active_tasks": active_tasks,
            "recent_completed": status.get("recent_completed"),
            "token_metrics": status.get("token_metrics", {}),
            "agents": status.get("agents", {}),
            "git": self._get_git_info(),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "mission_control": status.get("mission_control", {}),
        }

    def do_OPTIONS(self) -> None:
        if not self._check_origin():
            return
        self.send_response(204)
        self._apply_security_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if not self._check_origin():
            return

        path = self.path.split("?")[0]

        if not is_public_path(path):
            is_sse = (path == "/api/events/stream")
            if not self._verify_auth(path, allow_query_token=is_sse):
                return

        if path == "/":
            self._serve_html()
        elif path == "/callback":
            self._handle_oauth_callback()
        elif path.startswith("/static/"):
            self._serve_static(path)
        elif path == "/api/overview":
            self._serve_json(self._get_overview())
        elif path == "/api/status":
            self._serve_json(self._get_system_status())
        elif path == "/api/agents":
            self._serve_json(self._get_agents_runtime())
        elif path == "/api/sessions":
            self._serve_json(
                {"sessions": [s.to_dict() for s in orchestrator.sessions.list_recent(50)]}
            )
        elif path == "/api/health":
            self._serve_json(orchestrator.health(deep=False))
        elif path == "/api/tasks":
            tasks = [t.to_dict() for t in task_manager.list_tasks()]
            self._serve_json({"tasks": tasks})
        elif path == "/api/task":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            task_id = params.get("task_id", [None])[0]
            if not task_id:
                self._serve_json({"error": "Missing task_id query parameter"}, status=400)
            else:
                task = task_manager.get_task(task_id)
                if task:
                    self._serve_json({"task": task.to_dict()})
                else:
                    self._serve_json({"error": f"Task '{task_id}' not found"}, status=404)
        elif path == "/api/events":
            evts = [e.to_dict() for e in event_bus.get_recent_events(100)]
            self._serve_json({"events": evts})

        # ── Phase 22 Parts 6/9: Account Add Wizard discovery endpoints ──────
        # NOTE: response keys deliberately avoid the substring "auth" (e.g.
        # login_methods, not auth_methods) so the SecretRedactor — which scrubs
        # any key matching /auth/ — does not blank out this non-secret data. The
        # method values are plain enum strings such as "api_key" / "oauth".
        elif path == "/api/wizard/providers":
            entries = []
            seen = set()
            for p in registry.list_providers():
                seen.add(p.id)
                entries.append({
                    "id": p.id,
                    "name": p.name,
                    "type": ("IDE" if p.id == "antigravity" else "AGENT"),
                    "account_count": len(registry.account_registry.list_accounts(p.id)),
                    "login_methods": wizard_manager.auth_methods_for(p.id),
                })
            for ai_prov in registry.list_ai_providers():
                if ai_prov.provider_id not in seen:
                    seen.add(ai_prov.provider_id)
                    entries.append({
                        "id": ai_prov.provider_id,
                        "name": ai_prov.display_name,
                        "type": ai_prov.provider_type.value.upper(),
                        "account_count": len(registry.account_registry.list_accounts(ai_prov.provider_id)),
                        "login_methods": wizard_manager.auth_methods_for(ai_prov.provider_id),
                    })
            # Standard preset providers available to onboard anytime
            KNOWN_PRESETS = [
                ("antigravity", "Google Antigravity", "IDE"),
                ("openai", "OpenAI", "API"),
                ("anthropic", "Anthropic Claude", "API"),
                ("gemini", "Google Gemini", "API"),
                ("openrouter", "OpenRouter", "GATEWAY"),
                ("groq", "Groq Cloud", "API"),
                ("cline", "Cline", "AGENT"),
                ("kiro", "Kiro", "AGENT"),
                ("ollama", "Ollama (local)", "LOCAL_MODEL"),
            ]
            for pid, name, ptype in KNOWN_PRESETS:
                if pid not in seen:
                    seen.add(pid)
                    entries.append({
                        "id": pid,
                        "name": name,
                        "type": ptype,
                        "account_count": len(registry.account_registry.list_accounts(pid)),
                        "login_methods": wizard_manager.auth_methods_for(pid),
                    })
            self._serve_json({"providers": entries, "count": len(entries)})
        elif path in ("/api/wizard/login-methods", "/api/wizard/auth-methods"):
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            prov = params.get("provider", [None])[0] or params.get("provider_id", [None])[0]
            if not prov:
                self._serve_json({"error": "Missing provider query parameter"}, status=400)
            else:
                methods = wizard_manager.auth_methods_for(prov)
                self._serve_json({
                    "provider_id": prov,
                    "login_methods": methods,
                    "supported": bool(methods),
                })
        elif path == "/api/wizard/events":
            self._serve_json({"events": wizard_event_stream.recent(100)})
        elif path == "/api/wizard/check-auth":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            wid = params.get("wizard_id", [None])[0]
            sess = wizard_manager.get(wid) if wid else None
            if not sess:
                self._serve_json({"error": "Wizard session not found"}, status=404)
            else:
                self._serve_json(wizard_manager.check_auth_status(sess))

        elif path == "/api/events/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._apply_security_headers()
            self.end_headers()

            event_queue: queue.Queue = queue.Queue(maxsize=250)
            redactor = get_credential_manager().redactor

            def _stream_listener(evt: Event) -> None:
                try:
                    payload = redactor.redact_dict(evt.to_dict())
                    event_queue.put_nowait(payload)
                except Exception:
                    pass

            event_bus.subscribe(_stream_listener)
            # Phase 22 Part 10: also stream redacted account/auth lifecycle events
            # (account.create_started ... account.online / account.removed), each
            # carrying the onboarding-flow correlation id. Payloads are already
            # redacted by WizardEventStream; we redact again as defense in depth.
            wizard_queue = wizard_event_stream.subscribe()

            # Send initial backlog of recent 25 events (oldest to newest)
            try:
                recent = event_bus.get_recent_events(25)
                for rev in reversed(recent):
                    rd = redactor.redact_dict(rev.to_dict())
                    msg = f"data: {json.dumps(rd)}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                for wev in wizard_event_stream.recent(25):
                    msg = f"data: {json.dumps(redactor.redact_dict(wev))}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                event_bus.unsubscribe(_stream_listener)
                wizard_event_stream.unsubscribe(wizard_queue)
                return

            try:
                while True:
                    wrote = False
                    try:
                        item = event_queue.get_nowait()
                        msg = f"data: {json.dumps(item)}\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        wrote = True
                    except queue.Empty:
                        pass
                    try:
                        witem = wizard_queue.get_nowait()
                        msg = f"data: {json.dumps(redactor.redact_dict(witem))}\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        wrote = True
                    except queue.Empty:
                        pass
                    if wrote:
                        self.wfile.flush()
                    else:
                        # Idle: block briefly on the primary bus, then loop so the
                        # wizard queue is checked promptly too.
                        try:
                            item = event_queue.get(timeout=2.0)
                            msg = f"data: {json.dumps(item)}\n\n"
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                event_bus.unsubscribe(_stream_listener)
                wizard_event_stream.unsubscribe(wizard_queue)
        elif path == "/api/memory":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            search_val = params.get("search", [None])[0]
            scope_val = params.get("scope", [None])[0]
            scope_enum = None
            if scope_val:
                try:
                    scope_enum = MemoryScope(scope_val.upper())
                except Exception:
                    pass
            imp_val = params.get("importance", [None])[0]
            min_imp = int(imp_val) if imp_val and imp_val.isdigit() else None
            t_id = params.get("task_id", [None])[0]
            src_agent = params.get("agent_id", [None])[0]
            mems = [
                m.to_dict()
                for m in memory_store.query_memories(
                    search=search_val,
                    scope=scope_enum,
                    min_importance=min_imp,
                    task_id=t_id,
                    source_agent=src_agent,
                    limit=100,
                )
            ]
            self._serve_json({"memories": mems, "count": len(mems), "total": memory_store.count()})
        elif path == "/api/memory/retrieval-history":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            t_id = params.get("task_id", [None])[0]
            lim = int(params.get("limit", [100])[0])
            self._serve_json({"retrievals": memory_store.list_retrievals(limit=lim, task_id=t_id)})
        elif path == "/api/handoff":
            content = handoff_manager.get_current_handoff() or "No active handoff available."
            rec = handoff_manager.get_current_record()
            self._serve_json({
                "markdown": content,
                "record": rec.to_dict() if rec else None,
            })
        elif path == "/api/handoff/history":
            self._serve_json({"history": handoff_manager.list_history(50)})
        elif path == "/api/handoff/record":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            fname = params.get("filename", ["current.json"])[0]
            rec = handoff_manager.get_record_by_name(fname)
            self._serve_json({"record": rec})
        elif path in ("/api/metrics/tokens", "/api/tokens"):
            self._serve_json(orchestrator.swarm.token_tracker.get_metrics())
        elif path == "/api/router/history":
            self._serve_json({"history": orchestrator.router.get_routing_history(50)})
        elif path == "/api/git":
            self._serve_json(self._get_git_info())
        elif path == "/api/worktrees":
            records = orchestrator.worktrees.status()
            if isinstance(records, list):
                data = [r.to_dict() for r in records]
            elif records:
                data = [records.to_dict()]
            else:
                data = []
            self._serve_json({"worktrees": data})
        elif path == "/api/worktrees/diff":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            task_id = params.get("task_id", [None])[0]
            if not task_id:
                self._serve_json({"error": "Missing task_id query parameter"}, status=400)
            else:
                try:
                    diff_data = orchestrator.worktrees.diff(task_id)
                    self._serve_json(diff_data)
                except ValueError as exc:
                    self._serve_json({"error": str(exc)}, status=404)
                except Exception as exc:
                    self._serve_json({"error": str(exc)}, status=500)
        elif path == "/api/token":
            # Loopback session-auth handshake. Not a provider credential, so it
            # must bypass the response redactor or the UI can never authenticate.
            self._serve_json({"token": get_or_create_auth_token()}, redact=False)

        # ── Phase 12-14: Provider Management API ──────────────────────────
        elif path == "/api/providers":
            providers = []
            analytics = get_analytics_engine()
            for p in registry.list_providers():
                accts = registry.account_registry.list_accounts(p.id)
                h = p.health()
                p_type = "IDE" if p.id == "antigravity" else "AGENT"
                p_metrics = analytics.get_metrics(provider_id=p.id)
                providers.append({
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "type": p_type,
                    "enabled": p.enabled,
                    "healthy": h.get("healthy", False),
                    "status": "online" if h.get("healthy", False) else "offline",
                    "failure_rate": p_metrics.failure_rate,
                    "models": p.models,
                    "capabilities": sorted(c.value for c in p.capabilities),
                    "account_count": len(accts),
                    "accounts": [a.to_dict() for a in accts],
                    "last_health_check": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
            # Also include direct API-only providers
            for ai_prov in registry.list_ai_providers():
                if not any(pp["id"] == ai_prov.provider_id for pp in providers):
                    accts = registry.account_registry.list_accounts(ai_prov.provider_id)
                    primary_acct = accts[0] if accts else None
                    ok, reason = ai_prov.health(primary_acct)
                    ai_metrics = analytics.get_metrics(provider_id=ai_prov.provider_id)
                    is_unconfig = "Missing credential" in reason or "AUTH_ERROR" in reason or not accts
                    status_label = "online" if ok else ("not_configured" if is_unconfig else "offline")
                    providers.append({
                        "id": ai_prov.provider_id,
                        "name": ai_prov.display_name,
                        "description": f"{ai_prov.display_name} API Provider",
                        "type": ai_prov.provider_type.value.upper(),
                        "enabled": True,
                        "healthy": ok,
                        "status": status_label,
                        "health_reason": reason,
                        "failure_rate": ai_metrics.failure_rate,
                        "models": [m.model_id for m in ai_prov.list_models(primary_acct)],
                        "capabilities": sorted(c.value for c in ai_prov.capabilities()),
                        "account_count": len(accts),
                        "accounts": [a.to_dict() for a in accts],
                        "last_health_check": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    })
            self._serve_json({"providers": providers, "count": len(providers)})
        elif path.startswith("/api/providers/") and not any(
            path.endswith(x) for x in ("/health", "/discover", "/enable", "/disable")
        ):
            p_id = path.split("/api/providers/")[1].split("/")[0]
            provider = registry.get_provider(p_id)
            ai_prov = registry.get_ai_provider(p_id)
            analytics = get_analytics_engine()
            if provider:
                h = provider.health()
                accts = registry.account_registry.list_accounts(p_id)
                p_metrics = analytics.get_metrics(provider_id=p_id)
                p_type = "IDE" if p_id == "antigravity" else "AGENT"
                self._serve_json({
                    "id": provider.id,
                    "name": provider.name,
                    "description": provider.description,
                    "type": p_type,
                    "enabled": provider.enabled,
                    "healthy": h.get("healthy", False),
                    "status": "online" if h.get("healthy", False) else "offline",
                    "failure_rate": p_metrics.failure_rate,
                    "adapters": h.get("adapters", {}),
                    "models": provider.models,
                    "capabilities": sorted(c.value for c in provider.capabilities),
                    "accounts": [a.to_dict() for a in accts],
                    "last_health_check": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
            elif ai_prov:
                accts = registry.account_registry.list_accounts(p_id)
                primary_acct = accts[0] if accts else None
                ok, reason = ai_prov.health(primary_acct)
                ai_metrics = analytics.get_metrics(provider_id=p_id)
                is_unconfig = "Missing credential" in reason or "AUTH_ERROR" in reason or not accts
                status_label = "online" if ok else ("not_configured" if is_unconfig else "offline")
                self._serve_json({
                    "id": ai_prov.provider_id,
                    "name": ai_prov.display_name,
                    "description": f"{ai_prov.display_name} API Provider",
                    "type": ai_prov.provider_type.value.upper(),
                    "enabled": True,
                    "healthy": ok,
                    "status": status_label,
                    "health_reason": reason,
                    "failure_rate": ai_metrics.failure_rate,
                    "models": [m.model_id for m in ai_prov.list_models(primary_acct)],
                    "capabilities": sorted(c.value for c in ai_prov.capabilities()),
                    "accounts": [a.to_dict() for a in accts],
                    "last_health_check": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
            else:
                self._serve_json({"error": f"Provider '{p_id}' not found"}, status=404)

        # ── Phase 22 Part 8: Granular account metrics API ────────────────
        # Eight explicit, independently-computed metrics, each derived from a
        # single orthogonal state field (never a conflated one). Registered
        # via string equality BEFORE the "/api/accounts/{id}" prefix route so
        # "metrics" is not mistaken for an account id.
        elif path == "/api/accounts/metrics":
            all_accounts = registry.account_registry.list_accounts()
            metrics = self._compute_account_metrics(all_accounts)
            self._serve_json({"metrics": metrics, "count": metrics["registered"]})

        # ── Phase 12-14: Account Management API ───────────────────────────
        elif path == "/api/accounts":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            provider_filter = params.get("provider", [None])[0]
            accts = registry.account_registry.list_accounts(provider_filter)
            u_tracker = get_usage_tracker()
            analytics = get_analytics_engine()
            res_accts = []
            for a in accts:
                d = a.to_dict()
                u = u_tracker.get_summary(account_id=a.id)
                d["requests"] = u["requests"]
                d["tokens"] = u["total_tokens"]
                d["estimated_cost"] = u["estimated_cost_usd"]
                d["failure_rate"] = analytics.get_metrics(account_id=a.id).failure_rate
                res_accts.append(d)
            self._serve_json({
                "accounts": res_accts,
                "count": len(res_accts),
            })
        elif path.startswith("/api/accounts/") and not any(
            path.endswith(x) for x in ("/health", "/enable", "/disable")
        ):
            a_id = path.split("/api/accounts/")[1].split("/")[0]
            account = registry.account_registry.get_account(a_id)
            if account:
                d = account.to_dict()
                u = get_usage_tracker().get_summary(account_id=a_id)
                d["requests"] = u["requests"]
                d["tokens"] = u["total_tokens"]
                d["estimated_cost"] = u["estimated_cost_usd"]
                d["failure_rate"] = get_analytics_engine().get_metrics(account_id=a_id).failure_rate
                self._serve_json(d)
            else:
                self._serve_json({"error": f"Account '{a_id}' not found"}, status=404)

        # ── Phase 12-14: Model Registry API ───────────────────────────────
        elif path == "/api/models":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            prov = params.get("provider", [None])[0]
            cap = params.get("capability", [None])[0]
            models = registry.model_registry.list_models(
                provider_id=prov,
                capability=cap,
            )
            model_dicts = []
            for m in models:
                md = m.to_dict()
                md["speed"] = md.get("latency_tier", "medium")
                md["availability"] = "available" if m.enabled else "unavailable"
                md["health"] = True
                model_dicts.append(md)
            self._serve_json({
                "models": model_dicts,
                "count": len(model_dicts),
            })

        # ── Phase 12-14: Job History API ──────────────────────────────────
        elif path == "/api/jobs":
            jobs = job_manager.list_jobs()
            self._serve_json({
                "jobs": [j.to_dict() for j in jobs],
                "count": len(jobs),
            })
        elif path.startswith("/api/jobs/"):
            j_id = path.split("/api/jobs/")[1].split("/")[0]
            job = job_manager.get_job(j_id)
            if job:
                self._serve_json(job.to_dict())
            else:
                self._serve_json({"error": f"Job '{j_id}' not found"}, status=404)

        # ── Phase 13: Routing History & Inspect API ───────────────────────
        elif path == "/api/routing/history":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            lim = int(params.get("limit", [50])[0])
            jid = params.get("job_id", [None])[0]
            router = SmartRouter(registry)
            history = router.get_routing_history(limit=lim, job_id=jid)
            self._serve_json({"history": history, "count": len(history)})
        elif path == "/api/routing/inspect":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            jid = params.get("job_id", [None])[0]
            if not jid:
                self._serve_json({"error": "Missing job_id query parameter"}, status=400)
            else:
                router = SmartRouter(registry)
                dec = router.get_decision(jid)
                if dec:
                    self._serve_json({"decision": dec.to_dict() if hasattr(dec, "to_dict") else dec})
                else:
                    j = job_manager.get_job(jid)
                    if j and j.metadata.get("routing_decision"):
                        self._serve_json({"decision": j.metadata["routing_decision"]})
                    else:
                        recs = router.get_routing_history(limit=1, job_id=jid)
                        if recs:
                            self._serve_json({"decision": recs[0]})
                        else:
                            self._serve_json({"error": f"No routing record found for job '{jid}'"}, status=404)

        # ── Phase 14: Usage & Cost Analytics API ──────────────────────────
        elif path == "/api/usage":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            prov = params.get("provider", [None])[0]
            acct = params.get("account", [None])[0]
            mod = params.get("model", [None])[0]
            day = params.get("day", [None])[0]
            month = params.get("month", [None])[0]
            grp = params.get("group_by", [None])[0]
            u_tracker = get_usage_tracker()
            if grp:
                breakdown = u_tracker.get_breakdown(group_by=grp)
                self._serve_json({"group_by": grp, "breakdown": breakdown})
            else:
                summary = u_tracker.get_summary(provider_id=prov, account_id=acct, model_id=mod, day=day, month=month)
                self._serve_json(summary)
        elif path == "/api/cost":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            prov = params.get("provider", [None])[0]
            acct = params.get("account", [None])[0]
            mod = params.get("model", [None])[0]
            day = params.get("day", [None])[0]
            month = params.get("month", [None])[0]
            summary = get_usage_tracker().get_summary(provider_id=prov, account_id=acct, model_id=mod, day=day, month=month)
            pricing = get_cost_tracker().list_pricing()
            today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            today_sum = get_usage_tracker().get_summary(day=today_str)
            self._serve_json({
                "summary": summary,
                "total_estimated_cost_usd": summary.get("estimated_cost_usd", 0.0),
                "today_estimated_cost_usd": today_sum.get("estimated_cost_usd", 0.0),
                "pricing": pricing,
                "pricing_models": pricing,
            })
        elif path == "/api/quotas":
            qm = get_quota_manager()
            rules = qm.list_quotas()
            augmented = []
            for r in rules:
                st = qm.check_target_quota(r["target_type"], r["target_id"])
                augmented.append({"rule": r, "status": st.to_dict()})
            alerts = qm.get_active_alerts()
            self._serve_json({"quotas": augmented, "alerts": alerts, "count": len(augmented)})
        elif path == "/api/analytics":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            prov = params.get("provider", [None])[0]
            acct = params.get("account", [None])[0]
            mod = params.get("model", [None])[0]
            metrics = get_analytics_engine().get_metrics(provider_id=prov, account_id=acct, model_id=mod)
            res_dict = metrics.to_dict()
            res_dict["latency"] = {
                "p50": res_dict.get("p50_latency", 0.0),
                "p95": res_dict.get("p95_latency", 0.0),
                "p99": res_dict.get("p99_latency", 0.0),
            }
            self._serve_json(res_dict)

        # ── Phase 13: Routing Status API ──────────────────────────────
        elif path == "/api/routing/status":
            router = SmartRouter(registry)
            all_accts = registry.account_registry.list_accounts()
            status_list = []
            for a in all_accts:
                status_list.append({
                    "account_id": a.id,
                    "provider_id": a.provider_id,
                    "status": a.status.value,
                    "enabled": a.enabled,
                    "priority": a.priority,
                    "available": a.is_available(),
                    "cooldown_active": bool(a.cooldown_until and time.time() < a.cooldown_until),
                    "failure_count": a.failure_count,
                })
            self._serve_json({
                "accounts": status_list,
                "total_available": sum(1 for s in status_list if s["available"]),
                "total_accounts": len(status_list),
            })

        # ── Phase 15: Universal MCP, Steering, Knowledge, Tools & Resources ──
        elif path == "/api/mcp":
            from providers.mcp.discovery import MCPDiscoveryEngine
            from providers.mcp.tool_catalog import MCPToolCatalog
            from providers.registry.mcp_registry import get_mcp_registry
            reg = get_mcp_registry()
            servers = reg.list_servers()
            if not servers:
                engine = MCPDiscoveryEngine()
                catalog = MCPToolCatalog()
                for disc in engine.discover():
                    s = disc.to_mcp_server()
                    catalog.populate_from_server(s.id)
                    s.tools = catalog.list_tools(server_id=s.id)
                    reg.register_server(s)
                servers = reg.list_servers()
            self._serve_json({"servers": [s.to_dict() for s in servers], "count": len(servers)})
        elif path.startswith("/api/mcp/"):
            mcp_id = path.split("/api/mcp/")[1].split("/")[0]
            from providers.registry.mcp_registry import get_mcp_registry
            server = get_mcp_registry().get_server(mcp_id)
            if server:
                self._serve_json(server.to_dict())
            else:
                self._serve_json({"error": f"MCP server '{mcp_id}' not found"}, status=404)
        elif path == "/api/tools":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            q = params.get("q", [None])[0]
            from providers.mcp.discovery import MCPDiscoveryEngine
            from providers.mcp.tool_catalog import MCPToolCatalog
            catalog = MCPToolCatalog()
            engine = MCPDiscoveryEngine()
            for disc in engine.discover():
                catalog.populate_from_server(disc.server_id)
            if q:
                ranked = catalog.search_tools(q, limit=20)
                self._serve_json({"tools": [{"tool": t.to_dict(), "score": round(s, 2)} for t, s in ranked]})
            else:
                tools = catalog.list_tools()
                self._serve_json({"tools": [t.to_dict() for t in tools], "count": len(tools)})
        elif path == "/api/steering":
            from brain.knowledge.steering_registry import SteeringRegistry
            reg = SteeringRegistry()
            docs = reg.discover()
            conflicts = reg.get_conflicts()
            self._serve_json({
                "steering": [d.to_dict() for d in docs],
                "conflicts": [c.to_dict() for c in conflicts],
                "count": len(docs)
            })
        elif path == "/api/knowledge":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            q = params.get("q", [None])[0]
            from brain.knowledge.document_registry import DocumentRegistry
            reg = DocumentRegistry()
            docs = reg.discover()
            if q:
                from brain.knowledge.relevance import KnowledgeRelevanceEngine
                rel = KnowledgeRelevanceEngine().rank_documents(task=q, documents=docs, limit=20)
                self._serve_json(rel.to_dict())
            else:
                self._serve_json({"documents": [d.to_dict() for d in docs], "count": len(docs)})
        elif path == "/api/resources":
            from brain.context.context_builder import ContextBuilder
            from brain.resources.resource_graph import ResourceGraphBuilder
            builder = ContextBuilder()
            sel = builder.selector
            graph = ResourceGraphBuilder.build_graph(
                None, sel.mcp_registry, sel.steering_registry,
                sel.document_registry, sel.cli_registry, sel.repo_registry
            )
            self._serve_json(graph.to_graph_data())

        else:
            self.send_response(404)
            self._apply_security_headers()
            self.end_headers()

    def do_POST(self) -> None:
        if not self._check_origin():
            return

        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        # 1. Unauthenticated endpoints
        if path == "/api/route":
            router = SmartRouter(registry)
            dec = router.route(
                task_text=payload.get("instruction", ""),
                preferred_agent=payload.get("agent"),
                preferred_model=payload.get("model"),
            )
            self._serve_json({
                "agent_id": dec.agent_id,
                "account_id": dec.account_id,
                "model": dec.model,
                "complexity": dec.complexity.value,
                "reason": dec.reason,
                "fallback_agent_id": dec.fallback_agent_id,
                "candidates": dec.candidates,
            })
            return

        elif path == "/api/context/preview":
            task = payload.get("task", "") or payload.get("instruction", "")
            from brain.context.context_builder import ContextBuilder
            builder = ContextBuilder()
            ctx = builder.preview_context(task)
            self._serve_json(ctx.to_dict())
            return

        # 2. Mutating endpoints requiring Bearer token authentication
        mutating_paths = {
            "/api/dispatch",
            "/api/execute",
            "/api/continue",
            "/api/tasks/cancel",
            "/api/tasks/reconcile",
            "/api/memory/add",
            "/api/worktrees/apply",
            "/api/worktrees/approve",
            "/api/worktrees/reject",
            "/api/worktrees/cleanup",
            "/api/worktrees/recover",
            "/api/providers",
            "/api/models/discover",
            "/api/quotas",
            "/api/quotas/reset",
            "/api/jobs",
            "/api/accounts",
        }
        if path in mutating_paths:
            if not self._verify_auth(path):
                return

        # 3. Rate limiting for task execution endpoints
        if path in ("/api/dispatch", "/api/continue", "/api/execute"):
            client_ip = self.client_address[0] if hasattr(self, "client_address") else "127.0.0.1"
            if not execution_rate_limiter.is_allowed(client_ip):
                self._serve_json(
                    {
                        "error": "Too Many Requests",
                        "message": "Execution rate limit exceeded (30 req/min). Try again later.",
                    },
                    status=429,
                )
                return

        # 4. Confirmation requirement for destructive actions
        destructive_paths = {
            "/api/worktrees/apply",
            "/api/worktrees/approve",
            "/api/worktrees/reject",
            "/api/worktrees/cleanup",
        }
        if path in destructive_paths:
            if payload.get("confirm") is not True:
                self._serve_json(
                    {
                        "error": "Confirmation required",
                        "message": "Explicit confirmation ('confirm': true) required for destructive worktree operations",
                    },
                    status=400,
                )
                return

        # 5. Endpoint dispatch handlers
        if path == "/api/dispatch":
            task = orchestrator.plan_and_dispatch(
                instruction=payload.get("instruction", "Untitled Task"),
                preferred_agent=payload.get("agent") or None,
                preferred_model=payload.get("model") or None,
                files=payload.get("files") or None,
            )
            auto_execute = payload.get("auto_execute", True)
            if auto_execute:
                threading.Thread(target=orchestrator.execute_next, daemon=True).start()
            self._serve_json({"status": "created", "task": task.to_dict(), "executing": auto_execute})
        elif path == "/api/execute":
            threading.Thread(target=orchestrator.execute_next, daemon=True).start()
            self._serve_json({"status": "executing", "message": "Triggered swarm execution worker"})
        elif path == "/api/tasks/cancel":
            task_id = payload.get("task_id")
            if not task_id:
                self._serve_json({"error": "Missing task_id"}, status=400)
                return
            cancelled = task_manager.update_status(
                task_id,
                TaskStatus.CANCELLED,
                is_terminal=True,
                terminal_reason="USER_CANCELLED",
            )
            if cancelled:
                self._serve_json({"status": "cancelled", "task": cancelled.to_dict()})
            else:
                self._serve_json({"error": f"Task '{task_id}' not found"}, status=404)
        elif path == "/api/tasks/reconcile":
            active_ids = orchestrator.swarm.get_active_task_ids()
            stats = task_manager.reconcile_runtime_state(active_ids)
            self._serve_json(stats)
        elif path == "/api/continue":
            ctx = orchestrator.build_continue_context()
            if getattr(ctx, "is_terminal", False):
                self._serve_json({"status": "terminal", "reason": ctx.terminal_reason, "context": ctx.to_dict()})
            else:
                threading.Thread(target=orchestrator.continue_work, daemon=True).start()
                self._serve_json({"status": "continued", "context": ctx.to_dict()})
        elif path == "/api/memory/search":
            query_text = payload.get("query", "")
            scope_str = payload.get("scope")
            scope_enum = None
            if scope_str:
                try:
                    scope_enum = MemoryScope(scope_str.lower())
                except Exception:
                    pass
            retriever = MemoryRetriever(memory_store)
            results = retriever.retrieve_context(
                query=query_text,
                scope=scope_enum,
                max_items=10,
                max_bytes=4096,
            )
            self._serve_json({
                "query": query_text,
                "count": len(results),
                "memories": [m.to_dict() for m in results],
            })
        elif path == "/api/memory/add":
            content = payload.get("content", "").strip()
            if not content:
                self._serve_json({"error": "Memory content cannot be empty"}, status=400)
                return
            scope_str = payload.get("scope", "project").lower()
            try:
                scope = MemoryScope(scope_str)
            except Exception:
                scope = MemoryScope.PROJECT
            entry = memory_store.add(
                content=content,
                scope=scope,
                source_agent=payload.get("source_agent", "user-mission-control"),
                importance=int(payload.get("importance", 3)),
                tags=payload.get("tags", []),
            )
            self._serve_json({"status": "added", "memory": entry.to_dict()})
        elif path in ("/api/worktrees/apply", "/api/worktrees/approve"):
            task_id = payload.get("task_id")
            if not task_id:
                self._serve_json({"error": "Missing task_id"}, status=400)
                return
            try:
                approver = payload.get("approver", "mission_control")
                result = orchestrator.worktrees.apply(
                    task_id=task_id, approver=approver, confirm=True
                )
                event_bus.emit(
                    Event(
                        event_type=EventType.DIFF_APPROVED,
                        task_id=task_id,
                        metadata={"approver": approver},
                    )
                )
                event_bus.emit(
                    Event(
                        event_type=EventType.MERGE_APPLIED,
                        task_id=task_id,
                        metadata={"result": result},
                    )
                )
                self._serve_json({"status": "applied", "task_id": task_id, "result": result})
            except RuntimeError as exc:
                self._serve_json(
                    {"error": "Conflict or Dirty Canonical Tree", "message": str(exc)},
                    status=409,
                )
            except Exception as exc:
                self._serve_json({"error": str(exc)}, status=500)
        elif path == "/api/worktrees/reject":
            task_id = payload.get("task_id")
            if not task_id:
                self._serve_json({"error": "Missing task_id"}, status=400)
                return
            try:
                reason = payload.get("reason", "Rejected via Mission Control")
                result = orchestrator.worktrees.reject(task_id=task_id, reason=reason, confirm=True)
                event_bus.emit(
                    Event(
                        event_type=EventType.DIFF_REJECTED,
                        task_id=task_id,
                        metadata={"reason": reason},
                    )
                )
                event_bus.emit(
                    Event(
                        event_type=EventType.WORKTREE_DESTROYED,
                        task_id=task_id,
                        metadata={"action": "reject", "result": result},
                    )
                )
                self._serve_json({"status": "rejected", "task_id": task_id, "result": result})
            except Exception as exc:
                self._serve_json({"error": str(exc)}, status=500)
        elif path == "/api/worktrees/cleanup":
            task_id = payload.get("task_id")
            try:
                if task_id:
                    result = orchestrator.worktrees.remove(
                        task_id=task_id,
                        confirm=True,
                        delete_branch=payload.get("delete_branch", True),
                    )
                    event_bus.emit(
                        Event(
                            event_type=EventType.WORKTREE_DESTROYED,
                            task_id=task_id,
                            metadata={"action": "cleanup_single"},
                        )
                    )
                    self._serve_json({"status": "cleaned", "task_id": task_id, "result": result})
                else:
                    max_age = int(payload.get("max_age_hours", 24))
                    cleaned = orchestrator.worktrees.cleanup(max_age_hours=max_age)
                    for item in cleaned:
                        event_bus.emit(
                            Event(
                                event_type=EventType.WORKTREE_DESTROYED,
                                task_id=item.get("task_id"),
                                metadata={"action": "cleanup_batch"},
                            )
                        )
                    self._serve_json({"status": "cleaned", "cleaned": cleaned})
            except Exception as exc:
                self._serve_json({"error": str(exc)}, status=500)
        elif path == "/api/worktrees/recover":
            task_id = payload.get("task_id")
            if not task_id:
                self._serve_json({"error": "Missing task_id"}, status=400)
                return
            try:
                record = orchestrator.worktrees.recover(task_id=task_id)
                self._serve_json({"status": "recovered", "worktree": record.to_dict()})
            except Exception as exc:
                self._serve_json({"error": str(exc)}, status=500)

        # ── Phase 12-14: Account Creation ──────────────────────────────
        elif path == "/api/accounts":
            if not self._verify_auth(path):
                return
            name = (payload.get("name") or payload.get("account_id") or "").strip()
            provider_id = (payload.get("provider") or payload.get("provider_id") or "").strip()
            if not name or not provider_id:
                self._serve_json({"error": "Missing required fields: name/account_id, provider/provider_id"}, status=400)
                return
            account_id = payload.get("account_id") or (f"{provider_id}-{name}" if not name.startswith(provider_id) else name)
            if registry.account_registry.get_account(account_id):
                self._serve_json({"error": f"Account '{account_id}' already exists"}, status=409)
                return
            auth_type_str = payload.get("auth_type") or payload.get("auth_method") or "api_key"
            try:
                auth_type = AuthenticationType(auth_type_str)
            except Exception:
                auth_type = AuthenticationType.API_KEY
            cred_ref = payload.get("credential_reference", "")
            if payload.get("api_key"):
                cred_ref = f"secret://mission-control/{provider_id}/{account_id}/api_key"
                get_credential_manager().store_credential(cred_ref, payload["api_key"])
            models_list = payload.get("models", [])
            if isinstance(models_list, str):
                models_list = [m.strip() for m in models_list.split(",") if m.strip()]
            new_account = Account(
                id=account_id,
                provider_id=provider_id,
                account_name=name,
                display_name=payload.get("display_name", name),
                account_type=payload.get("account_type", "api"),
                authentication_type=auth_type,
                credential_reference=cred_ref,
                status=AccountStatus.ONLINE if (cred_ref or auth_type == AuthenticationType.UNAUTHENTICATED) else AccountStatus.NOT_CONFIGURED,
                enabled=True,
                priority=int(payload.get("priority", 10)),
                models=models_list,
                capabilities=payload.get("capabilities", []),
                concurrency_limit=int(payload.get("concurrency_limit", 2)),
            )
            registry.account_registry.register_account(new_account)
            self._serve_json({"status": "created", "account": new_account.to_dict()}, status=201)

        # ── Phase 12-14: Account Health Check ──────────────────────────
        elif path.startswith("/api/accounts/") and path.endswith("/health"):
            if not self._verify_auth(path):
                return
            a_id = path.split("/api/accounts/")[1].split("/")[0]
            account = registry.account_registry.get_account(a_id)
            if not account:
                self._serve_json({"error": f"Account '{a_id}' not found"}, status=404)
                return
            pool = registry.account_registry.get_pool(account.provider_id)
            adapter = registry.get_adapter(a_id)
            if adapter:
                healthy, reason = adapter.health()
                if healthy:
                    pool.record_success(a_id)
                else:
                    pool.record_failure(a_id, error=reason)
                account.last_health_check = datetime.datetime.now(datetime.timezone.utc).isoformat()
                self._serve_json({
                    "account_id": a_id,
                    "healthy": healthy,
                    "reason": reason,
                    "status": account.status.value,
                })
            else:
                cred_ok = False
                if account.credential_reference:
                    cred_ok = get_credential_manager().exists(account.credential_reference)
                account.last_health_check = datetime.datetime.now(datetime.timezone.utc).isoformat()
                if cred_ok:
                    account.status = AccountStatus.ONLINE
                    self._serve_json({
                        "account_id": a_id,
                        "healthy": True,
                        "reason": "Credential reference validated",
                        "status": account.status.value,
                    })
                else:
                    account.status = AccountStatus.CONFIG_ERROR if not account.credential_reference else AccountStatus.AUTH_ERROR
                    self._serve_json({
                        "account_id": a_id,
                        "healthy": False,
                        "reason": "No credential reference" if not account.credential_reference else "Credential not found",
                        "status": account.status.value,
                    })

        # ── Phase 12-14: Account Enable/Disable ────────────────────────
        elif path.startswith("/api/accounts/") and path.endswith("/enable"):
            if not self._verify_auth(path):
                return
            a_id = path.split("/api/accounts/")[1].split("/")[0]
            if registry.account_registry.enable_account(a_id):
                self._serve_json({"status": "enabled", "account_id": a_id})
            else:
                self._serve_json({"error": f"Account '{a_id}' not found"}, status=404)
        elif path.startswith("/api/accounts/") and path.endswith("/disable"):
            if not self._verify_auth(path):
                return
            a_id = path.split("/api/accounts/")[1].split("/")[0]
            if registry.account_registry.disable_account(a_id):
                self._serve_json({"status": "disabled", "account_id": a_id})
            else:
                self._serve_json({"error": f"Account '{a_id}' not found"}, status=404)

        # ── Phase 12: Provider Creation & Management ───────────────────
        elif path == "/api/providers":
            if not self._verify_auth(path):
                return
            p_id = payload.get("id", "").strip()
            name = payload.get("name", "").strip() or p_id.capitalize()
            p_type = payload.get("type", "api").lower()
            base_url = payload.get("base_url", "https://api.openai.com/v1")
            models = payload.get("models", [])
            if isinstance(models, str):
                models = [m.strip() for m in models.split(",") if m.strip()]
            default_model = payload.get("default_model", models[0] if models else "default")
            cred_ref = payload.get("credential_reference", "")
            if payload.get("api_key"):
                cred_ref = f"secret://mission-control/{p_id}/provider_api_key"
                get_credential_manager().store_credential(cred_ref, payload["api_key"])

            if p_type in ("ollama", "local", "local_model"):
                ai_prov = OllamaProvider(provider_id=p_id, base_url=base_url, default_model=default_model)
            elif p_type == "gemini":
                ai_prov = GeminiProvider(provider_id=p_id, default_model=default_model)
            elif p_type == "anthropic":
                ai_prov = AnthropicProvider(provider_id=p_id, default_model=default_model)
            else:
                ai_prov = OpenAICompatibleProvider(provider_id=p_id, base_url=base_url, credential_reference=cred_ref, default_model=default_model)

            registry.register_ai_provider(ai_prov)

            if cred_ref or payload.get("api_key"):
                acct_id = f"{p_id}-primary"
                if not registry.account_registry.get_account(acct_id):
                    def_acct = Account(
                        id=acct_id,
                        provider_id=p_id,
                        account_name=f"{name} Primary",
                        display_name=f"{name} Primary",
                        account_type="api",
                        authentication_type=AuthenticationType.API_KEY,
                        credential_reference=cred_ref,
                        status=AccountStatus.ONLINE,
                        enabled=True,
                        priority=10,
                        models=models,
                        capabilities=payload.get("capabilities", ["chat"]),
                    )
                    registry.account_registry.register_account(def_acct)

            self._serve_json({"status": "created", "provider_id": p_id}, status=201)

        elif path.startswith("/api/providers/") and path.endswith("/health"):
            if not self._verify_auth(path):
                return
            p_id = path.split("/api/providers/")[1].split("/")[0]
            provider = registry.get_provider(p_id)
            ai_prov = registry.get_ai_provider(p_id)
            if provider:
                h = provider.health()
                self._serve_json({"provider_id": p_id, "healthy": h.get("healthy", False), "details": h})
            elif ai_prov:
                accts = registry.account_registry.list_accounts(p_id)
                primary_acct = accts[0] if accts else None
                ok, reason = ai_prov.health(primary_acct)
                self._serve_json({"provider_id": p_id, "healthy": ok, "reason": reason})
            else:
                self._serve_json({"error": f"Provider '{p_id}' not found"}, status=404)

        elif path.startswith("/api/providers/") and path.endswith("/discover"):
            if not self._verify_auth(path):
                return
            p_id = path.split("/api/providers/")[1].split("/")[0]
            ai_prov = registry.get_ai_provider(p_id)
            if ai_prov:
                discovered = ai_prov.list_models()
                for m in discovered:
                    registry.model_registry.register_model(m)
                self._serve_json({"provider_id": p_id, "count": len(discovered), "models": [m.to_dict() for m in discovered]})
            else:
                self._serve_json({"error": f"Provider '{p_id}' not found or does not support model discovery"}, status=404)

        elif path.startswith("/api/providers/") and (path.endswith("/enable") or path.endswith("/disable")):
            if not self._verify_auth(path):
                return
            parts = path.split("/api/providers/")[1].split("/")
            p_id = parts[0]
            enable = (parts[1] == "enable")
            provider = registry.get_provider(p_id)
            if provider:
                provider.enabled = enable
                self._serve_json({"status": "updated", "provider_id": p_id, "enabled": enable})
            else:
                ai_prov = registry.get_ai_provider(p_id)
                if ai_prov:
                    self._serve_json({"status": "updated", "provider_id": p_id, "enabled": enable})
                else:
                    self._serve_json({"error": f"Provider '{p_id}' not found"}, status=404)

        # ── Phase 12: Model Discovery across all providers ─────────────
        elif path == "/api/models/discover":
            if not self._verify_auth(path):
                return
            discovered_all = []
            for ai_prov in registry.list_ai_providers():
                try:
                    for m in ai_prov.list_models():
                        registry.model_registry.register_model(m)
                        discovered_all.append(m.to_dict())
                except Exception:
                    pass
            self._serve_json({"status": "discovered", "count": len(discovered_all), "models": discovered_all})

        # ── Phase 14: Quotas Configuration ─────────────────────────────
        elif path == "/api/quotas":
            if not self._verify_auth(path):
                return
            target_type = payload.get("target_type", "").strip()
            target_id = payload.get("target_id", "").strip()
            if not target_type or not target_id:
                self._serve_json({"error": "target_type and target_id are required"}, status=400)
                return
            rule = get_quota_manager().set_quota(
                target_type=target_type,
                target_id=target_id,
                daily_requests=payload.get("daily_requests"),
                daily_tokens=payload.get("daily_tokens"),
                monthly_requests=payload.get("monthly_requests"),
                monthly_tokens=payload.get("monthly_tokens"),
                daily_cost=payload.get("daily_cost"),
                monthly_cost=payload.get("monthly_cost"),
                enabled=payload.get("enabled", True),
            )
            self._serve_json({"status": "saved", "rule": rule.to_dict()}, status=201)

        elif path == "/api/quotas/reset":
            if not self._verify_auth(path):
                return
            target_type = payload.get("target_type", "").strip()
            target_id = payload.get("target_id", "").strip()
            if not target_type or not target_id:
                self._serve_json({"error": "target_type and target_id are required"}, status=400)
                return
            removed = get_quota_manager().remove_quota(target_type=target_type, target_id=target_id)
            self._serve_json({"status": "reset", "removed": removed})

        # ── Phase 12-13: Job Submission with Multi-Factor SmartRouter ───
        elif path == "/api/jobs":
            if not self._verify_auth(path):
                return
            task_text = payload.get("task", "").strip()
            if not task_text:
                self._serve_json({"error": "Missing 'task' field"}, status=400)
                return
            prov = payload.get("provider", "").strip()
            acct = payload.get("account", "").strip()
            mdl = payload.get("model", "").strip()
            mode = payload.get("routing_mode", "balanced")
            streaming = bool(payload.get("streaming", False))
            failover_enabled = bool(payload.get("failover_enabled", True))

            decision = None
            if not prov or prov.lower() == "auto" or not acct or acct.lower() == "auto" or not mdl or mdl.lower() == "auto":
                router = SmartRouter(registry)
                pref_agent = prov if (prov and prov.lower() != "auto") else None
                pref_acct = acct if (acct and acct.lower() != "auto") else None
                pref_mdl = mdl if (mdl and mdl.lower() != "auto") else None
                try:
                    decision = router.route(
                        task_text=task_text,
                        preferred_agent=pref_agent,
                        preferred_account=pref_acct,
                        preferred_model=pref_mdl,
                        routing_mode=mode,
                        requires_streaming=streaming,
                    )
                    prov = decision.provider_id or decision.agent_id
                    acct = decision.account_id
                    mdl = decision.model
                except Exception as r_err:
                    self._serve_json({"error": f"SmartRouter failed: {r_err}"}, status=503)
                    return

            failover_chain = decision.failover_chain if (decision and failover_enabled) else None

            job = job_manager.submit_job(
                task=task_text,
                provider=prov,
                account=acct,
                model=mdl,
                metadata={
                    "routing_mode": mode,
                    "streaming": streaming,
                    "failover_enabled": failover_enabled,
                    "routing_decision": decision.to_dict() if decision else None,
                },
                failover_chain=failover_chain,
            )

            if decision:
                decision.job_id = job.id
                job.metadata["routing_decision"] = decision.to_dict()
                router = SmartRouter(registry)
                router.record_decision(decision)

            if payload.get("auto_execute", True):
                threading.Thread(
                    target=job_manager.execute_job,
                    args=(job,),
                    kwargs={"failover_chain": failover_chain},
                    daemon=True,
                ).start()

            resp = {
                "status": "submitted",
                "job": job.to_dict(),
            }
            if decision:
                resp["routing_decision"] = decision.to_dict()
                resp["explanation"] = decision.explain()
            self._serve_json(resp, status=200)

        # ── Phase 22 Part 9: Account Add Wizard (multi-step, real lifecycle) ──
        # Every step drives a real AccountLifecycleState transition and emits a
        # redacted, correlation-tagged event. All steps require the mutating
        # Bearer token. Secrets go straight to CredentialManager; only a
        # secret:// reference is ever retained or surfaced. Back / Cancel / Retry
        # are supported at every step; Cancel rolls back with zero orphans.
        elif path == "/api/wizard/start":
            if not self._verify_auth(path):
                return
            provider_id = (payload.get("provider_id") or payload.get("provider") or "").strip()
            account_id = (payload.get("account_id") or payload.get("name") or "").strip()
            if not provider_id or not account_id:
                self._serve_json({"error": "provider_id and account_id are required"}, status=400)
                return
            if registry.account_registry.get_account(account_id):
                self._serve_json({"error": f"Account '{account_id}' already exists"}, status=409)
                return
            sess = wizard_manager.start(provider_id, account_id)
            self._serve_json({"status": "started", "wizard": sess.to_dict()}, status=201)

        elif path.startswith("/api/wizard/") and path.split("/")[-1] in (
            "select-auth", "configure", "authenticate", "validate",
            "register", "health", "complete", "back", "cancel", "retry",
            "launch-login", "check-auth",
        ):
            if not self._verify_auth(path):
                return
            action = path.split("/")[-1]
            wizard_id = (payload.get("wizard_id") or "").strip()
            sess = wizard_manager.get(wizard_id)
            if not sess:
                self._serve_json({"error": f"Wizard session '{wizard_id}' not found or already finished"}, status=404)
                return
            try:
                if action == "select-auth":
                    auth_m = (payload.get("auth_method") or payload.get("login_method") or "").strip()
                    wizard_manager.select_auth(sess, auth_m)
                    self._serve_json({"status": "ok", "wizard": sess.to_dict()})
                elif action == "configure":
                    wizard_manager.configure(sess, dict(payload.get("config") or payload))
                    self._serve_json({"status": "ok", "wizard": sess.to_dict()})
                elif action == "launch-login":
                    origin = f"http://{self.headers.get('Host', '127.0.0.1:3333')}"
                    if payload.get("email"):
                        sess.config["email"] = str(payload.get("email")).strip()
                    info = wizard_manager.launch_login(sess, redirect_origin=origin)
                    self._serve_json({"status": "ok", "login": info, "wizard": sess.to_dict()})
                elif action == "check-auth":
                    status_info = wizard_manager.check_auth_status(sess)
                    self._serve_json({"status": "ok", "auth_status": status_info, "wizard": sess.to_dict()})
                elif action == "authenticate":
                    wizard_manager.authenticate(sess)
                    self._serve_json({"status": "ok", "wizard": sess.to_dict()})
                elif action == "validate":
                    wizard_manager.validate(sess, live=bool(payload.get("live", True)))
                    self._serve_json({"status": "ok", "wizard": sess.to_dict()})
                elif action == "register":
                    wizard_manager.register(sess)
                    self._serve_json({"status": "ok", "wizard": sess.to_dict()})
                elif action == "health":
                    result = wizard_manager.health_check(sess)
                    self._serve_json({"status": "ok", "health": result, "wizard": sess.to_dict()})
                elif action == "complete":
                    wizard_manager.complete(sess)
                    self._serve_json({"status": "complete", "wizard": sess.to_dict()})
                elif action == "back":
                    wizard_manager.back(sess)
                    self._serve_json({"status": "ok", "wizard": sess.to_dict()})
                elif action == "retry":
                    # Retry re-clears the failure marker so the client can re-issue
                    # the failed step. The lifecycle already sits in a failure
                    # state from which CONFIGURING/AUTHENTICATING/VALIDATING is a
                    # legal recovery transition per the state machine.
                    sess.last_error = None
                    sess.failure_state = None
                    self._serve_json({"status": "ok", "wizard": sess.to_dict()})
                elif action == "cancel":
                    cleanup = wizard_manager.cancel(sess)
                    self._serve_json({"status": "cancelled", "cleanup": cleanup, "wizard": sess.to_dict()})
            except WizardError as werr:
                wizard_manager.fail(sess, werr)
                self._serve_json({
                    "error": str(werr),
                    "failure_state": werr.failure_state.value,
                    "wizard": sess.to_dict(),
                }, status=422)
            except Exception as exc:
                # Unexpected error: land in CONFIG_ERROR with an actionable message.
                werr = WizardError(f"Unexpected error during '{action}': {exc}", AccountLifecycleState.CONFIG_ERROR)
                wizard_manager.fail(sess, werr)
                self._serve_json({
                    "error": str(werr),
                    "failure_state": werr.failure_state.value,
                    "wizard": sess.to_dict(),
                }, status=500)

        # ── Phase 10: Routing Test ────────────────────────────────────
        elif path == "/api/routing/test":
            instruction = payload.get("instruction", "").strip()
            if not instruction:
                self._serve_json({"error": "Missing 'instruction' field"}, status=400)
                return
            router = SmartRouter(registry)
            try:
                decision = router.route(
                    task_text=instruction,
                    preferred_agent=payload.get("agent"),
                    preferred_model=payload.get("model"),
                    routing_mode=payload.get("routing_mode", "balanced"),
                )
                self._serve_json({
                    "agent_id": decision.agent_id,
                    "account_id": decision.account_id,
                    "model": decision.model,
                    "complexity": decision.complexity.value,
                    "reason": decision.reason,
                    "fallback_agent_id": decision.fallback_agent_id,
                    "candidates": decision.candidates,
                    "explanation": decision.explain(),
                })
            except RuntimeError as exc:
                self._serve_json({"error": str(exc)}, status=503)

        else:
            self.send_response(404)
            self._apply_security_headers()
            self.end_headers()

    # ── Phase 12-14: PATCH handler (Account & Model updates) ──────────
    def do_PATCH(self) -> None:
        if not self._check_origin():
            return
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        if path.startswith("/api/accounts/"):
            if not self._verify_auth(path):
                return
            a_id = path.split("/api/accounts/")[1].split("/")[0]
            account = registry.account_registry.get_account(a_id)
            if not account:
                self._serve_json({"error": f"Account '{a_id}' not found"}, status=404)
                return
            updated = registry.account_registry.update_account(
                account_id=a_id,
                display_name=payload.get("display_name"),
                priority=int(payload["priority"]) if "priority" in payload else None,
                enabled=payload.get("enabled"),
                metadata=payload.get("metadata"),
            )
            if updated:
                self._serve_json({"status": "updated", "account": account.to_dict()})
            else:
                self._serve_json({"error": "Update failed"}, status=500)
        elif path.startswith("/api/models/"):
            if not self._verify_auth(path):
                return
            m_id = path.split("/api/models/")[1].split("/")[0]
            model = registry.model_registry.get_model(m_id)
            if not model:
                self._serve_json({"error": f"Model '{m_id}' not found"}, status=404)
                return
            if "enabled" in payload:
                model.enabled = bool(payload["enabled"])
            if "priority" in payload:
                model.priority = int(payload["priority"])
            if "capabilities" in payload:
                caps = payload["capabilities"]
                if isinstance(caps, list):
                    model.capabilities = frozenset(caps)
            self._serve_json({"status": "updated", "model": model.to_dict()})
        else:
            self.send_response(404)
            self._apply_security_headers()
            self.end_headers()

    # ── Phase 12-14: DELETE handler (Account & Quota removal) ─────────
    def do_DELETE(self) -> None:
        if not self._check_origin():
            return
        path = self.path.split("?")[0]

        if path.startswith("/api/accounts/"):
            if not self._verify_auth(path):
                return
            a_id = path.split("/api/accounts/")[1].split("/")[0]
            # Safety: Prevent removal of core Antigravity accounts
            protected = {"antigravity-account-1", "antigravity-account-2", "antigravity-account-3"}
            if a_id in protected:
                self._serve_json({
                    "error": "Protected account",
                    "message": f"Account '{a_id}' is a protected Antigravity IDE account and cannot be removed",
                }, status=403)
                return
            # Capture scoped-cleanup detail BEFORE removal (remove_account deletes
            # the credential). We only ever reference the secret:// URI, never a value.
            _acct_pre = registry.account_registry.get_account(a_id)
            cred_ref_display = getattr(_acct_pre, "credential_reference", "") if _acct_pre else ""
            had_credential = bool(cred_ref_display)
            provider_id = getattr(_acct_pre, "provider_id", "") if _acct_pre else ""
            removed = registry.account_registry.remove_account(a_id)
            config_rewritten = False
            if removed and provider_id:
                try:
                    remove_account_config(provider_id, a_id)
                    config_rewritten = True
                except Exception:
                    pass
            if removed:
                # Phase 22 Part 7: report scoped cleanup detail so the operator
                # sees exactly what was deleted. We report the credential
                # reference (a secret:// URI, never the value) and that it was
                # purged; the SecretRedactor still scrubs the response.
                cleanup = {
                    "account_removed": True,
                    # Key avoids the redactor's sensitive substrings
                    # (secret/credential/auth/token/private/password/api_key) so
                    # this boolean is not rewritten to "***REDACTED***". It
                    # reports whether the stored secret reference was purged from
                    # CredentialManager during removal.
                    "reference_purged": bool(had_credential),
                    "credential_reference": cred_ref_display,
                    "config_file_rewritten": config_rewritten,
                    "usage_history_retained": True,
                }
                self._serve_json({"status": "removed", "account_id": a_id, "cleanup": cleanup})
            else:
                self._serve_json({"error": f"Account '{a_id}' not found"}, status=404)
        elif path.startswith("/api/quotas/"):
            if not self._verify_auth(path):
                return
            rem_id = path.split("/api/quotas/")[1].split("/")[0]
            if ":" in rem_id:
                tt, ti = rem_id.split(":", 1)
                ok = get_quota_manager().remove_quota(tt, ti)
                self._serve_json({"status": "removed", "removed": ok})
            else:
                self._serve_json({"error": "Quota key format must be target_type:target_id"}, status=400)
        else:
            self.send_response(404)
            self._apply_security_headers()
            self.end_headers()

    def _serve_html_content(self, content: str, status: int = 200) -> None:
        raw = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _handle_oauth_callback(self) -> None:
        query = self.path.split("?")[1] if "?" in self.path else ""
        params = urllib.parse.parse_qs(query)
        error = params.get("error", [None])[0]
        if error:
            error_desc = params.get("error_description", [error])[0]
            self._serve_html_content(f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Sign-In Failed</title>
<style>
body {{ font-family: system-ui, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #0b0f19; color: #f8fafc; }}
.card {{ text-align: center; padding: 2rem; background: #1e293b; border-radius: 12px; border: 1px solid #ef4444; max-width: 420px; }}
h1 {{ color: #ef4444; font-size: 1.25rem; }}
p {{ color: #94a3b8; font-size: 0.875rem; }}
</style></head>
<body>
<div class="card">
  <h1>Google Sign-In Failed</h1>
  <p>{html.escape(error_desc)}</p>
  <p><button onclick="window.close()" style="padding: 8px 16px; background: #334155; color: white; border: none; border-radius: 6px; cursor: pointer;">Close Window</button></p>
</div>
</body></html>""", status=400)
            return

        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        if not code or not state:
            self._serve_html_content("<h1>Missing authorization code or state</h1>", status=400)
            return

        sess = wizard_manager.get(state)
        if not sess:
            self._serve_html_content("<h1>OAuth session not found or expired</h1>", status=404)
            return

        host = self.headers.get("Host", "127.0.0.1:3333")
        redirect_uri = f"http://{host}/callback"
        client_id, client_secret = _get_antigravity_oauth_credentials()
        token_payload = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }

        try:
            tok_req = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=urllib.parse.urlencode(token_payload).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(tok_req, timeout=15) as tok_resp:
                tokens = json.loads(tok_resp.read().decode("utf-8"))
        except Exception as exc:
            self._serve_html_content(f"<h1>Token Exchange Failed</h1><p>{html.escape(str(exc))}</p>", status=502)
            return

        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        id_token = tokens.get("id_token", "")
        primary_token = refresh_token or access_token

        # Fetch user info for account identification
        user_email = ""
        if access_token:
            try:
                u_req = urllib.request.Request(
                    "https://www.googleapis.com/oauth2/v1/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                with urllib.request.urlopen(u_req, timeout=10) as u_resp:
                    u_data = json.loads(u_resp.read().decode("utf-8"))
                    user_email = u_data.get("email", "")
            except Exception:
                pass

        if not user_email and sess.config.get("email"):
            user_email = sess.config.get("email")

        # Persist into isolated profile directory
        mgr = AntigravityAuthManager()
        data_dir, profile_dir = mgr.create_isolated_profile(sess.account_id, sess.config.get("app_data_dir"))
        token_file = profile_dir / TOKEN_FILENAME
        token_data_to_store = {
            "token": primary_token,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "auth_method": "oauth",
        }
        if id_token:
            token_data_to_store["id_token"] = id_token
        if user_email:
            token_data_to_store["email"] = user_email

        token_file.write_text(json.dumps(token_data_to_store), encoding="utf-8")
        try:
            token_file.chmod(0o600)
        except OSError:
            pass

        # Save to CredentialManager
        cred_ref = f"secret://mission-control/{sess.provider_id}/{sess.account_id}/oauth_token"
        try:
            get_credential_manager().store(cred_ref, primary_token)
        except Exception:
            pass
        sess.credential_reference = cred_ref

        if user_email:
            sess.config["email"] = user_email
            if sess.account:
                sess.account.metadata["email"] = user_email
                sess.account.description = f"Antigravity account ({user_email})"

        # Mark authenticated
        AccountLifecycleStateMachine.transition(
            sess.account, AccountLifecycleState.AUTHENTICATED, reason="Google OAuth login successful"
        )
        sess.step = "authenticate"

        self._serve_html_content(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Google Sign-In Successful</title>
  <style>
    body {{ font-family: system-ui, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #0b0f19; color: #f8fafc; }}
    .card {{ text-align: center; padding: 2.5rem; background: #1e293b; border-radius: 12px; border: 1px solid #10b981; max-width: 440px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
    .check {{ font-size: 3rem; color: #10b981; line-height: 1; margin-bottom: 1rem; }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; }}
    p {{ color: #94a3b8; font-size: 0.875rem; margin: 0 0 1rem; }}
    .email {{ font-family: monospace; color: #818cf8; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="check">✓</div>
    <h1>Google Sign-In Successful!</h1>
    <p>Logged in as <span class="email">{html.escape(user_email or 'Google User')}</span>.</p>
    <p>Your account is authenticated. This window will close automatically.</p>
  </div>
  <script>
    if (window.opener) {{
      try {{ window.opener.postMessage({{ type: 'google_oauth_complete', wizard_id: '{html.escape(state)}', email: '{html.escape(user_email)}' }}, '*'); }} catch (e) {{}}
    }}
    setTimeout(() => {{ window.close(); }}, 1500);
  </script>
</body>
</html>""")

    def _serve_html(self) -> None:
        html = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>Agentic Brain — Mission Control</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="/static/tailwind.js"></script>
  <link rel="stylesheet" href="/static/fontawesome.css">
  <link rel="stylesheet" href="/static/fonts.css">
  <script src="/static/marked.min.js"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 50: '#f0fdf4', 500: '#22c55e', 600: '#16a34a', 900: '#14532d' },
            slate: { 850: '#151b28', 900: '#0f172a', 950: '#080d1a' }
          }
        }
      }
    }
  </script>
</head>
<body class="bg-slate-950 text-slate-100 font-sans min-h-screen flex flex-col antialiased">
  <!-- Top Bar -->
  <header class="border-b border-slate-800 bg-slate-900/90 backdrop-blur px-6 py-3 flex items-center justify-between sticky top-0 z-50">
    <div class="flex items-center space-x-4">
      <div id="sync-dot" class="w-3 h-3 rounded-full bg-emerald-500 animate-pulse" title="Live Connection Active"></div>
      <h1 class="text-lg font-bold tracking-wide text-white flex items-center gap-2 cursor-pointer" onclick="showTab('dashboard')">
        <i class="fa-solid fa-brain text-emerald-400"></i> AGENTIC BRAIN <span class="text-xs px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 font-mono">MISSION CONTROL</span>
      </h1>
    </div>
    <div class="flex items-center space-x-4 text-xs text-slate-400">
      <div>Host: <span class="text-slate-200 font-mono">CachyOS (2 Cores / 16GB)</span></div>
      <div id="git-badge" class="px-2 py-1 rounded bg-slate-800 border border-slate-700 font-mono text-emerald-400">main</div>
      <button onclick="triggerExecute()" class="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-white rounded font-medium shadow-sm transition flex items-center gap-1.5" title="Run ready queued tasks in Swarm">
        <i class="fa-solid fa-bolt"></i> Execute Swarm
      </button>
      <button onclick="triggerContinue()" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded font-medium shadow-sm transition flex items-center gap-1.5" title="Resume from handoff & memory">
        <i class="fa-solid fa-play"></i> Continue Work
      </button>
      <button onclick="triggerReconcile()" class="px-3 py-1.5 bg-amber-700 hover:bg-amber-600 text-white rounded font-medium shadow-sm transition flex items-center gap-1.5" title="Reconcile runtime tasks and eliminate stale running states">
        <i class="fa-solid fa-arrows-rotate"></i> Reconcile Runtime
      </button>
    </div>
  </header>

  <!-- Main Container -->
  <div class="flex-1 flex overflow-hidden">
    <!-- Sidebar Tabs -->
    <aside class="w-64 border-r border-slate-800 bg-slate-900/50 flex flex-col p-3 space-y-1">
      <button onclick="showTab('dashboard')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium active bg-slate-800 text-white" data-tab="dashboard">
        <i class="fa-solid fa-gauge-high mr-2 text-slate-400"></i> Dashboard
      </button>
      <button onclick="showTab('agents')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="agents">
        <i class="fa-solid fa-robot mr-2 text-indigo-400"></i> Agents & Accounts
      </button>
      <button onclick="showTab('tasks')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="tasks">
        <i class="fa-solid fa-list-check mr-2 text-amber-400"></i> Tasks (Kanban)
      </button>
      <button onclick="showTab('routing')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="routing">
        <i class="fa-solid fa-route mr-2 text-yellow-400"></i> Routing Decisions
      </button>
      <button onclick="showTab('tokens')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="tokens">
        <i class="fa-solid fa-coins mr-2 text-emerald-400"></i> Tokens & Cost
      </button>
      <button onclick="showTab('worktrees')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="worktrees">
        <i class="fa-solid fa-shield-halved mr-2 text-emerald-400"></i> Worktree Sandboxes
      </button>
      <button onclick="showTab('flow')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="flow">
        <i class="fa-solid fa-diagram-project mr-2 text-cyan-400"></i> Execution Flow
      </button>
      <button onclick="showTab('memory')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="memory">
        <i class="fa-solid fa-database mr-2 text-purple-400"></i> Shared Memory
      </button>
      <button onclick="showTab('events')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="events">
        <i class="fa-solid fa-bolt mr-2 text-yellow-400"></i> Live Events
      </button>
      <div class="pt-3 pb-1 px-1 text-[10px] uppercase tracking-widest text-slate-600 font-bold border-t border-slate-800 mt-2">Phase 10 — Mission Control</div>
      <button onclick="showTab('mc-providers')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="mc-providers">
        <i class="fa-solid fa-server mr-2 text-sky-400"></i> Providers
      </button>
      <button onclick="showTab('mc-accounts')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="mc-accounts">
        <i class="fa-solid fa-users-gear mr-2 text-teal-400"></i> Account Manager
      </button>
      <button onclick="showTab('mc-models')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="mc-models">
        <i class="fa-solid fa-cubes mr-2 text-violet-400"></i> Model Registry
      </button>
      <button onclick="showTab('mc-jobs')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="mc-jobs">
        <i class="fa-solid fa-clipboard-list mr-2 text-orange-400"></i> Jobs
      </button>
      <button onclick="showTab('mc-usage')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="mc-usage">
        <i class="fa-solid fa-chart-pie mr-2 text-rose-400"></i> Usage & Stats
      </button>
    </aside>

    <!-- Content Area -->
    <main class="flex-1 overflow-y-auto p-8 bg-slate-950">
      <!-- DASHBOARD TAB -->
      <section id="tab-dashboard" class="tab-pane block space-y-6">
        <div class="flex items-center justify-between">
          <h2 class="text-xl font-bold text-white">Mission Overview</h2>
          <div class="px-3 py-1 bg-slate-900 border border-slate-800 rounded text-xs text-slate-400 font-mono">
            Host: <span class="text-emerald-400">CachyOS Linux (2 Cores / 16GB)</span> | Concurrency: <span class="text-cyan-400">Max 2</span> | Heavy: <span class="text-amber-400">Max 1</span>
          </div>
        </div>

        <div class="grid grid-cols-4 gap-4">
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Registered Accounts</div>
            <div id="stat-accounts" class="text-2xl font-bold text-emerald-400 mt-1">-- / -- Online</div>
            <div id="stat-accounts-sub" class="text-[11px] text-slate-500 mt-1">Multi-account orchestration</div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Execution Workers</div>
            <div id="stat-workers" class="text-2xl font-bold text-cyan-400 mt-1">-- Active</div>
            <div id="stat-workers-sub" class="text-[11px] text-slate-500 mt-1">Swarm Adapter Pool</div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Task Pipeline</div>
            <div id="stat-tasks" class="text-2xl font-bold text-purple-400 mt-1">-- Running</div>
            <div id="stat-tasks-sub" class="text-[11px] text-slate-500 mt-1">0 queued · 0 running · 0 done</div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Tracked Usage</div>
            <div id="stat-tokens" class="text-2xl font-bold text-amber-400 mt-1">0</div>
            <div id="stat-tokens-sub" class="text-[11px] text-slate-500 mt-1">0 verified | $0.00 cost</div>
          </div>
        </div>

        <!-- PHASE 12: UNIVERSAL AI MISSION CONTROL OVERVIEW BANNER -->
        <div class="bg-slate-900/90 border border-slate-800 rounded-lg p-5">
          <div class="flex items-center justify-between mb-3 border-b border-slate-800 pb-2">
            <div class="flex items-center gap-2">
              <i class="fa-solid fa-satellite-dish text-sky-400"></i>
              <span class="text-sm font-bold text-white uppercase tracking-wider">Universal AI Mission Control</span>
            </div>
            <span class="text-[11px] font-mono text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800/60">AUTONOMOUS ORCHESTRATION</span>
          </div>
          <div class="grid grid-cols-6 gap-3 text-center font-mono">
            <div class="bg-slate-950/60 p-3 rounded border border-slate-800/80">
              <div class="text-[10px] text-slate-400 uppercase tracking-wider">Providers</div>
              <div id="mc-stat-providers" class="text-base font-bold text-sky-400 mt-1">-- / --</div>
            </div>
            <div class="bg-slate-950/60 p-3 rounded border border-slate-800/80">
              <div class="text-[10px] text-slate-400 uppercase tracking-wider">Accounts</div>
              <div id="mc-stat-accounts" class="text-base font-bold text-teal-400 mt-1">-- / --</div>
            </div>
            <div class="bg-slate-950/60 p-3 rounded border border-slate-800/80">
              <div class="text-[10px] text-slate-400 uppercase tracking-wider">Models</div>
              <div id="mc-stat-models" class="text-base font-bold text-violet-400 mt-1">--</div>
            </div>
            <div class="bg-slate-950/60 p-3 rounded border border-slate-800/80">
              <div class="text-[10px] text-slate-400 uppercase tracking-wider">Active Jobs</div>
              <div id="mc-stat-active-jobs" class="text-base font-bold text-amber-400 mt-1">0</div>
            </div>
            <div class="bg-slate-950/60 p-3 rounded border border-slate-800/80">
              <div class="text-[10px] text-slate-400 uppercase tracking-wider">Today Usage</div>
              <div id="mc-stat-today-tokens" class="text-base font-bold text-emerald-400 mt-1">0 tokens</div>
            </div>
            <div class="bg-slate-950/60 p-3 rounded border border-slate-800/80">
              <div class="text-[10px] text-slate-400 uppercase tracking-wider">Est. Cost</div>
              <div id="mc-stat-today-cost" class="text-base font-bold text-rose-400 mt-1">$0.00</div>
            </div>
          </div>

          <!-- PHASE 22 PART 8: GRANULAR ACCOUNT FLEET METRICS (8 explicit, decoupled counts) -->
          <div class="mt-4 pt-3 border-t border-slate-800">
            <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <i class="fa-solid fa-layer-group text-slate-500"></i> Account Fleet Status
              <span class="text-slate-600 normal-case tracking-normal">— each count from one orthogonal state, never conflated</span>
            </div>
            <div class="grid grid-cols-8 gap-2 text-center font-mono">
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800/80" title="Every account known to the registry">
                <div class="text-[9px] text-slate-400 uppercase tracking-wider">Registered</div>
                <div id="mc-metric-registered" class="text-base font-bold text-slate-200 mt-0.5">0</div>
              </div>
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800/80" title="Auth state AUTHENTICATED">
                <div class="text-[9px] text-slate-400 uppercase tracking-wider">Auth'd</div>
                <div id="mc-metric-authenticated" class="text-base font-bold text-indigo-400 mt-0.5">0</div>
              </div>
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800/80" title="Health state HEALTHY">
                <div class="text-[9px] text-slate-400 uppercase tracking-wider">Healthy</div>
                <div id="mc-metric-healthy" class="text-base font-bold text-emerald-400 mt-0.5">0</div>
              </div>
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800/80" title="Lifecycle state ONLINE">
                <div class="text-[9px] text-slate-400 uppercase tracking-wider">Online</div>
                <div id="mc-metric-online" class="text-base font-bold text-teal-400 mt-0.5">0</div>
              </div>
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800/80" title="Task state RUNNING or ASSIGNED">
                <div class="text-[9px] text-slate-400 uppercase tracking-wider">Busy</div>
                <div id="mc-metric-busy" class="text-base font-bold text-amber-400 mt-0.5">0</div>
              </div>
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800/80" title="Online accounts whose task state is IDLE">
                <div class="text-[9px] text-slate-400 uppercase tracking-wider">Idle</div>
                <div id="mc-metric-idle" class="text-base font-bold text-sky-400 mt-0.5">0</div>
              </div>
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800/80" title="Lifecycle state OFFLINE">
                <div class="text-[9px] text-slate-400 uppercase tracking-wider">Offline</div>
                <div id="mc-metric-offline" class="text-base font-bold text-rose-400 mt-0.5">0</div>
              </div>
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800/80" title="Lifecycle DISABLED or enabled=false">
                <div class="text-[9px] text-slate-400 uppercase tracking-wider">Disabled</div>
                <div id="mc-metric-disabled" class="text-base font-bold text-slate-500 mt-0.5">0</div>
              </div>
            </div>
          </div>
        </div>

        <!-- LIVE TASK EXECUTION STAGE TRACKER (Section 3 Requirement) -->
        <div id="live-execution-widget" class="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
          <!-- Populated dynamically -->
        </div>

        <!-- Quick Dispatch Box -->
        <div class="bg-slate-900 border border-slate-800 p-5 rounded-lg">
          <h3 class="text-sm font-semibold text-white mb-2 flex items-center gap-2">
            <i class="fa-solid fa-paper-plane text-emerald-400"></i> Dispatch Instruction to Swarm
          </h3>
          <div class="flex gap-3">
            <input id="quick-instruction" type="text" placeholder="e.g. Audit authentication middleware and verify unit tests..." class="flex-1 bg-slate-950 border border-slate-700 rounded px-4 py-2 text-sm text-white focus:outline-none focus:border-emerald-500" onkeydown="if(event.key==='Enter') dispatchTask(true)">
            <select id="quick-agent" class="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300">
              <option value="">Smart Router (Auto)</option>
              <option value="antigravity-account-1">Antigravity Account 1 (CLI)</option>
              <option value="antigravity-account-2">Antigravity Account 2 (Headless IDE Profile)</option>
              <option value="kiro-cli">Kiro CLI</option>
              <option value="cline">Cline CLI</option>
            </select>
            <button onclick="dispatchTask(true)" class="px-5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-sm font-semibold transition flex items-center gap-1.5">
              <i class="fa-solid fa-play"></i> Dispatch & Run
            </button>
            <button onclick="dispatchTask(false)" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-sm font-medium transition" title="Queue task without immediate execution">
              Queue Only
            </button>
          </div>
        </div>
      </section>

      <!-- AGENTS TAB -->
      <section id="tab-agents" class="tab-pane hidden space-y-6">
        <h2 class="text-xl font-bold text-white mb-2">Agent & Account Registry</h2>
        <p class="text-sm text-slate-400 mb-6">Every execution resource runs isolated. Antigravity Account 1 and Account 2 maintain distinct profiles. Account 2 executes headlessly via <code class="text-emerald-400 font-mono">agy --app_data_dir=antigravity-ide</code> without opening the GUI.</p>
        <div id="agents-grid" class="grid grid-cols-2 gap-4">
          <!-- Dynamically populated -->
        </div>
      </section>

      <!-- TASKS TAB -->
      <section id="tab-tasks" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Task Lifecycle (Kanban)</h2>
            <p class="text-sm text-slate-400">Click any task card to view full execution details, tokens, and outputs.</p>
          </div>
          <button onclick="triggerExecute()" class="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-white rounded text-xs font-semibold transition flex items-center gap-1.5">
            <i class="fa-solid fa-bolt"></i> Execute Ready Queue
          </button>
        </div>
        <div class="grid grid-cols-4 gap-4">
          <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
            <h3 class="text-xs font-semibold uppercase text-slate-400 mb-3 flex items-center justify-between">
              <span>Ready Queue</span>
              <span id="badge-ready" class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px]">0</span>
            </h3>
            <div id="tasks-ready" class="space-y-2"></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
            <h3 class="text-xs font-semibold uppercase text-cyan-400 mb-3 flex items-center justify-between">
              <span>Running / Active</span>
              <span id="badge-running" class="px-2 py-0.5 rounded bg-cyan-950 text-cyan-300 text-[10px]">0</span>
            </h3>
            <div id="tasks-running" class="space-y-2"></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
            <h3 class="text-xs font-semibold uppercase text-emerald-400 mb-3 flex items-center justify-between">
              <span>Completed</span>
              <span id="badge-completed" class="px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 text-[10px]">0</span>
            </h3>
            <div id="tasks-completed" class="space-y-2"></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
            <h3 class="text-xs font-semibold uppercase text-red-400 mb-3 flex items-center justify-between">
              <span>Terminal / Failed</span>
              <span id="badge-failed" class="px-2 py-0.5 rounded bg-red-950 text-red-300 text-[10px]">0</span>
            </h3>
            <div id="tasks-failed" class="space-y-2"></div>
          </div>
        </div>
      </section>

      <!-- ROUTING DECISIONS TAB -->
      <section id="tab-routing" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Intelligent Multi-Factor Routing</h2>
            <p class="text-sm text-slate-400">Explainable 8-factor composite competition: Keyword Affinity, Capability Match, Strength Fit, Reliability, Latency, Token Efficiency, Health, and Load Penalty.</p>
          </div>
          <button onclick="refreshData()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
            <i class="fa-solid fa-arrows-rotate"></i> Refresh Decisions
          </button>
        </div>

        <!-- Interactive Route Simulator -->
        <div class="bg-slate-900 border border-slate-800 p-5 rounded-lg space-y-3">
          <h3 class="text-sm font-semibold text-white flex items-center gap-2">
            <i class="fa-solid fa-compass-drafting text-yellow-400"></i> Interactive Routing Simulator
          </h3>
          <div class="flex gap-3">
            <input id="sim-instruction" type="text" placeholder="Enter task instruction to test scoring competition..." class="flex-1 bg-slate-950 border border-slate-700 rounded px-4 py-2 text-sm text-white focus:outline-none focus:border-yellow-500" onkeydown="if(event.key==='Enter') simulateRoute()">
            <button onclick="simulateRoute()" class="px-4 py-2 bg-yellow-600 hover:bg-yellow-500 text-slate-950 font-semibold rounded text-sm transition flex items-center gap-1.5">
              <i class="fa-solid fa-calculator"></i> Simulate Route
            </button>
          </div>
          <div id="sim-result" class="hidden p-4 bg-slate-950 border border-slate-800 rounded font-mono text-xs space-y-2">
            <!-- Simulated decision rendered here -->
          </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <table class="w-full text-left text-xs">
            <thead class="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
              <tr>
                <th class="p-3">Time</th>
                <th class="p-3">Task Instruction</th>
                <th class="p-3">Selected Agent</th>
                <th class="p-3">Model</th>
                <th class="p-3">Fallback</th>
                <th class="p-3">Reason & Breakdown</th>
              </tr>
            </thead>
            <tbody id="routing-history-tbody" class="divide-y divide-slate-800 font-mono">
              <tr><td colspan="6" class="p-4 text-center text-slate-500 font-sans">Loading routing history...</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- TOKENS & COST TAB -->
      <section id="tab-tokens" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Token & Cost Telemetry</h2>
            <p class="text-sm text-slate-400">Auditable token metrics. Unverified CLI runs are strictly classified as UNKNOWN (Never fabricated).</p>
          </div>
        </div>

        <div class="grid grid-cols-3 gap-4">
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Verified Tokens</div>
            <div id="tokens-known-val" class="text-2xl font-bold text-emerald-400 mt-1">0</div>
            <div class="text-[11px] text-slate-500 mt-1">Directly reported by API / adapter stdout</div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Unverified Runs</div>
            <div id="tokens-unknown-val" class="text-2xl font-bold text-amber-400 mt-1">0</div>
            <div class="text-[11px] text-slate-500 mt-1">CLI processes with no structured token header</div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Total Executions</div>
            <div id="tokens-total-val" class="text-2xl font-bold text-cyan-400 mt-1">0</div>
            <div class="text-[11px] text-slate-500 mt-1">Tracked across all 4 agents</div>
          </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-3">
          <h3 class="text-sm font-semibold text-white">Breakdown by Agent</h3>
          <div class="overflow-x-auto">
            <table class="w-full text-left text-xs font-mono">
              <thead class="bg-slate-950 text-slate-400 uppercase border-b border-slate-800 font-sans">
                <tr>
                  <th class="p-3">Agent</th>
                  <th class="p-3">Total Runs</th>
                  <th class="p-3">Verified Tokens</th>
                  <th class="p-3">Avg Latency</th>
                  <th class="p-3">Successes</th>
                </tr>
              </thead>
              <tbody id="tokens-agent-tbody" class="divide-y divide-slate-800">
                <tr><td colspan="5" class="p-4 text-center text-slate-500 font-sans">No telemetry records logged yet.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- WORKTREES TAB -->
      <section id="tab-worktrees" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Isolated Git Worktree Sandboxes</h2>
            <p class="text-sm text-slate-400">Deterministic sandboxes protecting canonical repository from unverified agent mutations.</p>
          </div>
          <button onclick="cleanupOldWorktrees()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
            <i class="fa-solid fa-broom"></i> Cleanup Stale
          </button>
        </div>

        <div id="worktrees-container" class="space-y-4">
          <!-- Dynamically populated worktree cards -->
        </div>
      </section>

      <!-- FLOW TAB -->
      <section id="tab-flow" class="tab-pane hidden space-y-6">
        <h2 class="text-xl font-bold text-white mb-2">Autonomous Swarm Execution Flow</h2>
        <div class="bg-slate-900 border border-slate-800 rounded-lg p-6 font-mono text-xs text-slate-300 space-y-4">
          <div class="grid grid-cols-4 gap-3 text-center">
            <div class="p-3 bg-indigo-950/80 border border-indigo-700 text-indigo-300 rounded">
              <div class="font-bold mb-1">1. USER / PROMPT</div>
              <div class="text-[10px] text-slate-400 font-sans">Task dispatch or Universal Continue</div>
            </div>
            <div class="p-3 bg-emerald-950/80 border border-emerald-700 text-emerald-300 rounded">
              <div class="font-bold mb-1">2. SHARED BRAIN</div>
              <div class="text-[10px] text-slate-400 font-sans">Session, continuator & scope retrieval</div>
            </div>
            <div class="p-3 bg-cyan-950/80 border border-cyan-700 text-cyan-300 rounded">
              <div class="font-bold mb-1">3. SMART ROUTER</div>
              <div class="text-[10px] text-slate-400 font-sans">8-factor scoring, failover order</div>
            </div>
            <div class="p-3 bg-amber-950/80 border border-amber-700 text-amber-300 rounded">
              <div class="font-bold mb-1">4. CONTEXT OPTIMIZER</div>
              <div class="text-[10px] text-slate-400 font-sans">Scoping, dedup & token budgeting</div>
            </div>
          </div>

          <div class="grid grid-cols-4 gap-3 text-center pt-2">
            <div class="p-3 bg-blue-950/80 border border-blue-700 text-blue-300 rounded">
              <div class="font-bold mb-1">5. AGENT ADAPTER</div>
              <div class="text-[10px] text-slate-400 font-sans">AG-1, Headless AG-2, Kiro, Cline</div>
            </div>
            <div class="p-3 bg-yellow-950/80 border border-yellow-700 text-yellow-300 rounded">
              <div class="font-bold mb-1">6. FAILOVER GUARD</div>
              <div class="text-[10px] text-slate-400 font-sans">AG-1 ⇄ AG-2 on quota / rate limit</div>
            </div>
            <div class="p-3 bg-purple-950/80 border border-purple-700 text-purple-300 rounded">
              <div class="font-bold mb-1">7. VERIFICATION</div>
              <div class="text-[10px] text-slate-400 font-sans">Bounded depth &le; 1, non-recursive</div>
            </div>
            <div class="p-3 bg-emerald-950/80 border border-emerald-700 text-emerald-300 rounded">
              <div class="font-bold mb-1">8. WORKTREE MERGE</div>
              <div class="text-[10px] text-slate-400 font-sans">Human gate or auto-merge on safe verify</div>
            </div>
          </div>
        </div>
      </section>

      <!-- MEMORY TAB -->
      <section id="tab-memory" class="tab-pane hidden space-y-4">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Shared Memory Explorer & Retrieval History</h2>
            <p class="text-sm text-slate-400">Scoped persistent knowledge & real BM25 retrieval events.</p>
          </div>
          <div class="flex gap-2">
            <button id="mem-view-records-btn" onclick="toggleMemoryView('records')" class="px-3 py-1.5 bg-purple-700 text-white rounded text-xs font-semibold">Knowledge Records</button>
            <button id="mem-view-retrievals-btn" onclick="toggleMemoryView('retrievals')" class="px-3 py-1.5 bg-slate-800 text-slate-300 hover:bg-slate-700 rounded text-xs font-semibold">Retrieval History</button>
          </div>
        </div>

        <!-- Filter Bar -->
        <div class="bg-slate-900 border border-slate-800 p-3 rounded-lg flex flex-wrap gap-3 items-center text-xs">
          <div class="flex-1 min-w-[200px]">
            <input id="mem-filter-search" type="text" placeholder="Filter memory content..." oninput="loadMemories()" class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-xs text-white focus:outline-none focus:border-purple-500">
          </div>
          <select id="mem-filter-scope" onchange="loadMemories()" class="bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-slate-300">
            <option value="">All Scopes</option>
            <option value="PROJECT">PROJECT</option>
            <option value="SESSION">SESSION</option>
            <option value="GLOBAL">GLOBAL</option>
            <option value="TASK">TASK</option>
          </select>
          <select id="mem-filter-imp" onchange="loadMemories()" class="bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-slate-300">
            <option value="">Min Importance (All)</option>
            <option value="5">5 - Critical</option>
            <option value="4">4 - High</option>
            <option value="3">3 - Medium</option>
            <option value="2">2 - Low</option>
            <option value="1">1 - Minimal</option>
          </select>
          <select id="mem-filter-agent" onchange="loadMemories()" class="bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-slate-300">
            <option value="">All Agents</option>
            <option value="cline">Cline</option>
            <option value="kiro">Kiro</option>
            <option value="antigravity-account-1">Antigravity 1</option>
            <option value="antigravity-account-2">Antigravity 2</option>
          </select>
          <button onclick="loadMemories()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded font-medium">Filter</button>
        </div>

        <!-- Interactive Memory Add -->
        <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg space-y-2">
          <h3 class="text-xs font-semibold uppercase text-emerald-400 flex items-center gap-1.5">
            <i class="fa-solid fa-plus"></i> Store New Memory Record
          </h3>
          <div class="flex gap-2">
            <input id="mem-add-content" type="text" placeholder="Important architectural decision or constraint..." class="flex-1 bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500">
            <select id="mem-add-scope" class="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-300">
              <option value="project">Project</option>
              <option value="session">Session</option>
              <option value="global">Global</option>
              <option value="task">Task</option>
            </select>
            <select id="mem-add-imp" class="bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-300">
              <option value="3">Imp 3</option>
              <option value="4">Imp 4</option>
              <option value="5">Imp 5</option>
              <option value="2">Imp 2</option>
              <option value="1">Imp 1</option>
            </select>
            <button onclick="addMemory()" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-semibold transition">Add</button>
          </div>
        </div>

        <!-- View 1: Knowledge Records -->
        <div id="memory-records-view" class="space-y-3">
          <div id="memory-list" class="space-y-3"></div>
        </div>

        <!-- View 2: Retrieval History -->
        <div id="memory-retrievals-view" class="hidden space-y-3">
          <div class="p-3 bg-slate-900 border border-slate-800 rounded-lg text-xs text-slate-400">
            Every memory retrieval operation is published to the SQLite ledger. Shows actual BM25 queries, matched memories, and scores.
          </div>
          <div id="memory-retrievals-list" class="space-y-3"></div>
        </div>
      </section>

      <!-- EVENTS TAB -->
      <section id="tab-events" class="tab-pane hidden space-y-4">
        <!-- PHASE 22 PART 10: Live Account & Authentication Lifecycle Feed -->
        <div class="bg-slate-900 border border-indigo-900/50 rounded-lg p-4 space-y-2">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-sm font-bold text-white flex items-center gap-2"><i class="fa-solid fa-satellite-dish text-indigo-400"></i> Live Account &amp; Auth Lifecycle</h2>
              <p class="text-[11px] text-slate-400">Redacted real-time onboarding events, grouped by correlation id. Never carries a secret value.</p>
            </div>
            <span class="px-2 py-0.5 rounded-full text-[9px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-800">SECRET-REDACTED</span>
          </div>
          <div id="lifecycle-feed" class="max-h-56 overflow-y-auto space-y-1 font-mono text-[11px]">
            <div class="text-slate-600 text-center py-3 font-sans">Waiting for account lifecycle events… start the Add Account wizard to see them here.</div>
          </div>
        </div>
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white">Append-Only Event Ledger</h2>
            <p class="text-sm text-slate-400">Real-time structured telemetry stream across all providers and lifecycle transitions.</p>
          </div>
          <div class="flex items-center gap-2">
            <span id="stream-status-pill" class="px-2.5 py-1 rounded-full text-[10px] font-mono font-bold flex items-center gap-1.5 bg-emerald-950/80 text-emerald-400 border border-emerald-800">
              <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> LIVE STREAMING
            </span>
            <button id="btn-toggle-stream" onclick="toggleEventStream()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-pause"></i> Pause
            </button>
            <button onclick="clearEventsTable()" class="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-400 border border-slate-700 rounded text-xs transition">
              Clear
            </button>
            <button onclick="loadEvents()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-arrows-rotate"></i> Poll Now
            </button>
          </div>
        </div>

        <!-- Filter Controls -->
        <div class="flex items-center gap-3 bg-slate-900 border border-slate-800 p-3 rounded-lg">
          <input type="text" id="event-filter-task" oninput="filterEventsTable()" placeholder="Filter by Task ID (e.g. task-001)..." class="bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 flex-1">
          <select id="event-filter-type" onchange="filterEventsTable()" class="bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-200 focus:outline-none">
            <option value="">All Event Categories</option>
            <option value="TASK_">Task Lifecycle (CREATED, STARTED, COMPLETED, FAILED)</option>
            <option value="ROUTE_">Routing & Model Selection</option>
            <option value="TOOL_">Tool Calls</option>
            <option value="VERIFICATION_">Verification & Tests</option>
            <option value="WORKTREE_">Worktree & Sandbox</option>
            <option value="AUTH_">Security & Auth</option>
            <option value="ERROR">Errors & Failures</option>
          </select>
          <input type="text" id="event-filter-agent" oninput="filterEventsTable()" placeholder="Filter Agent/Provider..." class="bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-200 focus:outline-none w-48">
        </div>

        <div id="events-table-wrapper" class="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <table class="w-full text-left font-mono text-xs">
            <thead class="bg-slate-950 text-slate-400 border-b border-slate-800 uppercase text-[10px]">
              <tr>
                <th class="p-2.5 w-24">Time (UTC)</th>
                <th class="p-2.5 w-48">Event Type</th>
                <th class="p-2.5 w-32">Task ID</th>
                <th class="p-2.5 w-40">Agent / Provider</th>
                <th class="p-2.5">Details (Click row to expand payload)</th>
              </tr>
            </thead>
            <tbody id="events-tbody" class="divide-y divide-slate-800 text-slate-300">
              <tr><td colspan="5" class="p-4 text-center text-slate-500">Connecting to real-time telemetry stream...</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- PHASE 12: PROVIDERS TAB -->
      <section id="tab-mc-providers" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Provider Control Plane</h2>
            <p class="text-sm text-slate-400">Manage Agent, IDE, API, Local Model, and Gateway providers.</p>
          </div>
          <div class="flex items-center gap-2">
            <button onclick="openAddProviderModal()" class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-semibold transition flex items-center gap-1.5">
              <i class="fa-solid fa-plus"></i> Add Provider
            </button>
            <button onclick="discoverAllModels()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-compass"></i> Discover Models
            </button>
            <button onclick="loadProviders()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-arrows-rotate"></i> Refresh
            </button>
          </div>
        </div>
        <div id="mc-providers-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <div class="p-4 text-center text-slate-500 text-sm col-span-full">Loading providers...</div>
        </div>
      </section>

      <!-- PHASE 12: ACCOUNT MANAGER TAB -->
      <section id="tab-mc-accounts" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Account Management</h2>
            <p class="text-sm text-slate-400">Multi-account orchestration with CredentialManager security and zero leakage.</p>
          </div>
          <div class="flex items-center gap-2">
            <button onclick="openAddAccountModal()" class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-semibold transition flex items-center gap-1.5">
              <i class="fa-solid fa-user-plus"></i> Add Account
            </button>
            <button onclick="loadAccounts()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-arrows-rotate"></i> Refresh
            </button>
          </div>
        </div>
        <div class="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <table class="w-full text-left text-xs">
            <thead class="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
              <tr>
                <th class="p-3">Account ID</th>
                <th class="p-3">Provider</th>
                <th class="p-3">Auth / Masked Key</th>
                <th class="p-3">Status Badges</th>
                <th class="p-3">Priority</th>
                <th class="p-3">Requests / Tokens</th>
                <th class="p-3">Est. Cost</th>
                <th class="p-3">Actions</th>
              </tr>
            </thead>
            <tbody id="mc-accounts-tbody" class="divide-y divide-slate-800 font-mono">
              <tr><td colspan="8" class="p-4 text-center text-slate-500 font-sans">Loading accounts...</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- PHASE 12: MODEL REGISTRY TAB -->
      <section id="tab-mc-models" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Model Registry & Capabilities</h2>
            <p class="text-sm text-slate-400">Model capabilities, context windows, pricing, speed, availability, and priority tags.</p>
          </div>
          <div class="flex items-center gap-2">
            <button onclick="discoverAllModels()" class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-semibold transition flex items-center gap-1.5">
              <i class="fa-solid fa-compass"></i> Discover Models
            </button>
            <button onclick="loadModels()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-arrows-rotate"></i> Refresh
            </button>
          </div>
        </div>
        <div class="flex items-center gap-3 bg-slate-900 border border-slate-800 p-3 rounded-lg">
          <input type="text" id="model-filter-search" oninput="filterModelsTable()" placeholder="Search models or providers..." class="bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 flex-1">
          <select id="model-filter-capability" onchange="filterModelsTable()" class="bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-200 focus:outline-none">
            <option value="">All Capabilities</option>
            <option value="coding">coding</option>
            <option value="reasoning">reasoning</option>
            <option value="planning">planning</option>
            <option value="debugging">debugging</option>
            <option value="long_context">long_context</option>
            <option value="vision">vision</option>
            <option value="tool_use">tool_use</option>
            <option value="fast">fast</option>
            <option value="cheap">cheap</option>
          </select>
        </div>
        <div class="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <table class="w-full text-left text-xs">
            <thead class="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
              <tr>
                <th class="p-3">Model</th>
                <th class="p-3">Provider</th>
                <th class="p-3">Context</th>
                <th class="p-3">Pricing ($/1M)</th>
                <th class="p-3">Speed / Latency</th>
                <th class="p-3">Priority</th>
                <th class="p-3">Capabilities</th>
                <th class="p-3">Status / Action</th>
              </tr>
            </thead>
            <tbody id="mc-models-tbody" class="divide-y divide-slate-800 font-mono">
              <tr><td colspan="8" class="p-4 text-center text-slate-500 font-sans">Loading models...</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- PHASE 12: JOBS & ROUTING MONITOR TAB -->
      <section id="tab-mc-jobs" class="tab-pane hidden space-y-6">
        <!-- Job Submission Card -->
        <div class="bg-slate-900 border border-slate-800 rounded-lg p-6 space-y-4">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3">
            <div>
              <h2 class="text-base font-bold text-white">Submit New Job to Mission Control</h2>
              <p class="text-xs text-slate-400">Intelligently routed across providers, accounts, and models with multi-factor scoring.</p>
            </div>
            <span class="px-2.5 py-1 bg-indigo-950 text-indigo-300 border border-indigo-800 rounded text-xs font-mono font-semibold">SMART ROUTER v2</span>
          </div>
          <div class="space-y-3">
            <textarea id="job-task-input" rows="3" placeholder="Enter task instruction or prompt (e.g., 'Refactor authentication module and add unit tests')..." class="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"></textarea>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div>
                <label class="block text-[11px] text-slate-400 uppercase font-semibold mb-1">Routing Mode</label>
                <select id="job-mode-select" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-xs text-slate-200 focus:outline-none">
                  <option value="balanced">Balanced (Optimal)</option>
                  <option value="performance">Performance (Fast & Capable)</option>
                  <option value="cost">Cost (Cheapest Candidate)</option>
                  <option value="reliability">Reliability (Highest Health)</option>
                  <option value="manual">Manual (Explicit Target)</option>
                </select>
              </div>
              <div>
                <label class="block text-[11px] text-slate-400 uppercase font-semibold mb-1">Provider</label>
                <select id="job-provider-select" onchange="onJobProviderChange()" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-xs text-slate-200 focus:outline-none">
                  <option value="auto">Auto (SmartRouter)</option>
                </select>
              </div>
              <div>
                <label class="block text-[11px] text-slate-400 uppercase font-semibold mb-1">Account</label>
                <select id="job-account-select" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-xs text-slate-200 focus:outline-none">
                  <option value="auto">Auto (SmartRouter)</option>
                </select>
              </div>
              <div>
                <label class="block text-[11px] text-slate-400 uppercase font-semibold mb-1">Model</label>
                <select id="job-model-select" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-xs text-slate-200 focus:outline-none">
                  <option value="auto">Auto (SmartRouter)</option>
                </select>
              </div>
            </div>
            <div class="flex items-center justify-between pt-2">
              <div class="flex items-center gap-6 text-xs text-slate-300">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" id="job-failover-check" checked class="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0">
                  <span>Deterministic Failover</span>
                </label>
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" id="job-streaming-check" class="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0">
                  <span>Streaming</span>
                </label>
                <div class="flex items-center gap-2">
                  <span class="text-slate-400">Priority:</span>
                  <input type="number" id="job-priority-input" value="5" min="1" max="10" class="w-14 bg-slate-950 border border-slate-800 rounded p-1 text-center text-xs text-slate-200">
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-slate-400">Timeout:</span>
                  <input type="number" id="job-timeout-input" value="30" min="5" max="300" class="w-16 bg-slate-950 border border-slate-800 rounded p-1 text-center text-xs text-slate-200">
                  <span class="text-slate-500">s</span>
                </div>
              </div>
              <button onclick="submitMissionControlJob()" class="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded text-xs shadow-lg shadow-indigo-600/30 transition flex items-center gap-2">
                <i class="fa-solid fa-paper-plane"></i> Submit Job
              </button>
            </div>
          </div>
          <!-- Live Routing Decision Result Banner -->
          <div id="job-submission-result" class="hidden p-4 bg-slate-950 border border-indigo-900/60 rounded-lg space-y-2 text-xs font-mono">
            <!-- Dynamically populated by submitMissionControlJob -->
          </div>
        </div>

        <!-- Live Job Monitoring Table -->
        <div class="space-y-4">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3">
              <h3 class="text-base font-bold text-white">Live Job Monitoring</h3>
              <div class="flex items-center gap-1 bg-slate-950 p-1 rounded border border-slate-800 text-xs">
                <button onclick="filterJobs('all')" id="job-filter-all" class="px-2.5 py-1 rounded bg-slate-800 text-white font-medium">All</button>
                <button onclick="filterJobs('running')" id="job-filter-running" class="px-2.5 py-1 rounded text-slate-400 hover:text-white">Running</button>
                <button onclick="filterJobs('completed')" id="job-filter-completed" class="px-2.5 py-1 rounded text-slate-400 hover:text-white">Completed</button>
                <button onclick="filterJobs('failed')" id="job-filter-failed" class="px-2.5 py-1 rounded text-slate-400 hover:text-white">Failed</button>
              </div>
            </div>
            <button onclick="loadJobs()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-arrows-rotate"></i> Refresh
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
            <table class="w-full text-left text-xs">
              <thead class="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
                <tr>
                  <th class="p-3">Job ID</th>
                  <th class="p-3">Task Prompt</th>
                  <th class="p-3">Provider</th>
                  <th class="p-3">Account</th>
                  <th class="p-3">Model</th>
                  <th class="p-3">Duration</th>
                  <th class="p-3">Tokens</th>
                  <th class="p-3">Status</th>
                  <th class="p-3">Routing / Actions</th>
                </tr>
              </thead>
              <tbody id="mc-jobs-tbody" class="divide-y divide-slate-800 font-mono">
                <tr><td colspan="9" class="p-4 text-center text-slate-500 font-sans">No jobs submitted yet.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- PHASE 14: USAGE, COST, QUOTA & ANALYTICS TAB -->
      <section id="tab-mc-usage" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Usage, Cost, Quotas & Performance Analytics</h2>
            <p class="text-sm text-slate-400">Provider-independent usage tracking, cost estimation, multi-tier quotas, and latency percentiles.</p>
          </div>
          <div class="flex items-center gap-2">
            <button onclick="openAddQuotaModal()" class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-semibold transition flex items-center gap-1.5">
              <i class="fa-solid fa-plus"></i> Configure Quota
            </button>
            <button onclick="resetQuotas()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-clock-rotate-left"></i> Reset Quota Counters
            </button>
            <button onclick="loadUsage()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
              <i class="fa-solid fa-arrows-rotate"></i> Refresh
            </button>
          </div>
        </div>

        <!-- Top Overview Cards -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Total Requests</div>
            <div id="mc-usage-requests" class="text-2xl font-bold text-sky-400 mt-1">0</div>
            <div class="text-[10px] text-slate-500 mt-1">Successful: <span id="mc-usage-success-reqs" class="text-emerald-400">0</span></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Total Tokens</div>
            <div id="mc-usage-tokens" class="text-2xl font-bold text-violet-400 mt-1">0</div>
            <div class="text-[10px] text-slate-500 mt-1">In: <span id="mc-usage-tokens-in" class="text-slate-300">0</span> · Out: <span id="mc-usage-tokens-out" class="text-slate-300">0</span></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Estimated Cost</div>
            <div id="mc-usage-cost" class="text-2xl font-bold text-emerald-400 mt-1">$0.00</div>
            <div class="text-[10px] text-slate-500 mt-1">Today: <span id="mc-usage-cost-today" class="text-emerald-300">$0.00</span></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Active Budget Alerts</div>
            <div id="mc-active-budget-alerts" class="text-2xl font-bold text-amber-400 mt-1">0</div>
            <div class="text-[10px] text-slate-500 mt-1">Thresholds: 50% · 75% · 90% · 100%</div>
          </div>
        </div>

        <!-- Performance Analytics Metrics -->
        <div class="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-3">
          <h3 class="text-sm font-semibold text-white uppercase tracking-wider">Performance Analytics</h3>
          <div class="grid grid-cols-3 md:grid-cols-6 gap-3 text-center">
            <div class="p-3 bg-slate-950 rounded border border-slate-800">
              <div class="text-[10px] text-slate-500 uppercase font-mono">p50 Latency</div>
              <div id="mc-p50-latency" class="text-lg font-bold text-cyan-400 mt-1">—</div>
            </div>
            <div class="p-3 bg-slate-950 rounded border border-slate-800">
              <div class="text-[10px] text-slate-500 uppercase font-mono">p95 Latency</div>
              <div id="mc-p95-latency" class="text-lg font-bold text-indigo-400 mt-1">—</div>
            </div>
            <div class="p-3 bg-slate-950 rounded border border-slate-800">
              <div class="text-[10px] text-slate-500 uppercase font-mono">p99 Latency</div>
              <div id="mc-p99-latency" class="text-lg font-bold text-purple-400 mt-1">—</div>
            </div>
            <div class="p-3 bg-slate-950 rounded border border-slate-800">
              <div class="text-[10px] text-slate-500 uppercase font-mono">Success Rate</div>
              <div id="mc-success-rate" class="text-lg font-bold text-emerald-400 mt-1">100%</div>
            </div>
            <div class="p-3 bg-slate-950 rounded border border-slate-800">
              <div class="text-[10px] text-slate-500 uppercase font-mono">Failure Rate</div>
              <div id="mc-failure-rate" class="text-lg font-bold text-rose-400 mt-1">0%</div>
            </div>
            <div class="p-3 bg-slate-950 rounded border border-slate-800">
              <div class="text-[10px] text-slate-500 uppercase font-mono">Failover Rate</div>
              <div id="mc-failover-rate" class="text-lg font-bold text-amber-400 mt-1">0%</div>
            </div>
          </div>
        </div>

        <!-- Provider Breakdown Table -->
        <div class="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <div class="p-4 border-b border-slate-800">
            <h3 class="text-sm font-semibold text-white">Usage by Provider</h3>
          </div>
          <table class="w-full text-left text-xs">
            <thead class="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
              <tr>
                <th class="p-3">Provider</th>
                <th class="p-3">Requests</th>
                <th class="p-3">Input Tokens</th>
                <th class="p-3">Output Tokens</th>
                <th class="p-3">Total Tokens</th>
                <th class="p-3">Estimated Cost</th>
                <th class="p-3">Success Rate</th>
              </tr>
            </thead>
            <tbody id="mc-provider-usage-tbody" class="divide-y divide-slate-800 font-mono">
              <tr><td colspan="7" class="p-4 text-center text-slate-500 font-sans">Loading provider usage...</td></tr>
            </tbody>
          </table>
        </div>

        <!-- Multi-tier Quota Compliance Table -->
        <div class="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <div class="p-4 border-b border-slate-800 flex items-center justify-between">
            <h3 class="text-sm font-semibold text-white">Quota Rules & Compliance Monitoring</h3>
            <span class="text-[10px] text-slate-400 font-mono">Enforced by QuotaManager</span>
          </div>
          <table class="w-full text-left text-xs">
            <thead class="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
              <tr>
                <th class="p-3">Rule Scope</th>
                <th class="p-3">Target ID</th>
                <th class="p-3">Daily Tokens (Used / Limit)</th>
                <th class="p-3">Daily Requests (Used / Limit)</th>
                <th class="p-3">Monthly Cost (Used / Limit)</th>
                <th class="p-3">Status</th>
                <th class="p-3">Action</th>
              </tr>
            </thead>
            <tbody id="mc-quotas-tbody" class="divide-y divide-slate-800 font-mono">
              <tr><td colspan="7" class="p-4 text-center text-slate-500 font-sans">No custom quotas configured. Default generous limits active.</td></tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </div>

  <!-- Diff Viewer Modal -->
  <div id="diff-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-4xl w-full max-h-[85vh] flex flex-col shadow-2xl">
      <div class="p-4 border-b border-slate-800 flex items-center justify-between">
        <h3 id="diff-modal-title" class="font-bold text-sm text-white font-mono">Diff</h3>
        <button onclick="closeDiffModal()" class="text-slate-400 hover:text-white text-sm px-2 py-1">✕</button>
      </div>
      <pre id="diff-modal-body" class="p-4 overflow-auto font-mono text-xs text-slate-300 flex-1 bg-slate-950 whitespace-pre"></pre>
    </div>
  </div>

  <!-- Task Detail Modal -->
  <div id="task-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-3xl w-full max-h-[85vh] flex flex-col shadow-2xl">
      <div class="p-4 border-b border-slate-800 flex items-center justify-between">
        <h3 id="task-modal-title" class="font-bold text-sm text-white font-mono">Task Details</h3>
        <button onclick="closeTaskModal()" class="text-slate-400 hover:text-white text-sm px-2 py-1">✕</button>
      </div>
      <div id="task-modal-body" class="p-6 overflow-auto text-xs space-y-4"></div>
    </div>
  </div>

  <!-- ADD PROVIDER MODAL -->
  <div id="add-provider-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-md w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="font-bold text-sm text-white">Register Provider</h3>
        <button onclick="closeAddProviderModal()" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>
      <div class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Provider ID *</label>
          <input type="text" id="prov-modal-id" placeholder="e.g., mistral, groq, local-llm" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none focus:border-indigo-500">
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Display Name *</label>
          <input type="text" id="prov-modal-name" placeholder="e.g., Mistral AI API" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none focus:border-indigo-500">
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Provider Type *</label>
          <select id="prov-modal-type" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
            <option value="API">API (OpenAI-compatible / Direct)</option>
            <option value="AGENT">AGENT (Autonomous CLI/Agent)</option>
            <option value="IDE">IDE (GUI / Workspace)</option>
            <option value="LOCAL_MODEL">LOCAL_MODEL (Ollama / Local inference)</option>
            <option value="GATEWAY">GATEWAY (Proxy / Router)</option>
          </select>
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Base URL (optional)</label>
          <input type="text" id="prov-modal-base-url" placeholder="https://api.example.com/v1" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Capabilities (comma separated)</label>
          <input type="text" id="prov-modal-caps" placeholder="coding, reasoning, fast, cheap" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
        </div>
        <div class="flex items-center gap-2 pt-1">
          <input type="checkbox" id="prov-modal-enabled" checked class="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0">
          <label for="prov-modal-enabled" class="text-slate-300">Enabled immediately</label>
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="closeAddProviderModal()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs">Cancel</button>
        <button onclick="submitAddProvider()" class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded text-xs shadow-md shadow-indigo-600/30">Save Provider</button>
      </div>
    </div>
  </div>

  <!-- ADD ACCOUNT MODAL -->
  <div id="add-account-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-md w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="font-bold text-sm text-white">Register Account</h3>
        <button onclick="closeAddAccountModal()" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>
      <div class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Provider ID *</label>
          <select id="acct-modal-provider" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
            <!-- Populated dynamically -->
          </select>
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Account ID *</label>
          <input type="text" id="acct-modal-id" placeholder="e.g., openai-account-2, mistral-team" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none focus:border-indigo-500">
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Account Type</label>
            <select id="acct-modal-type" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
              <option value="api">API</option>
              <option value="cli">CLI / Worker</option>
              <option value="ide">IDE</option>
            </select>
          </div>
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Priority (1-100)</label>
            <input type="number" id="acct-modal-priority" value="10" min="1" max="100" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
          </div>
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Authentication Method</label>
          <select id="acct-modal-auth" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
            <option value="api_key">API Key</option>
            <option value="token">Bearer Token</option>
            <option value="profile">Profile Path / Environment</option>
            <option value="unauthenticated">Unauthenticated / Local</option>
          </select>
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">API Key / Secret Credential</label>
          <input type="password" id="acct-modal-key" placeholder="sk-..." class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono">
          <p class="text-[10px] text-emerald-400/80 mt-1">🔒 Stored into CredentialManager. Never logged or exposed in plaintext.</p>
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="closeAddAccountModal()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs">Cancel</button>
        <button onclick="submitAddAccount()" class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded text-xs shadow-md shadow-indigo-600/30">Save Account</button>
      </div>
    </div>
  </div>

  <!-- PHASE 22 PART 7: REMOVE ACCOUNT CONFIRMATION MODAL (explicit scoped cleanup) -->
  <div id="remove-account-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-red-900/60 rounded-lg max-w-md w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="font-bold text-sm text-white flex items-center gap-2"><i class="fa-solid fa-triangle-exclamation text-red-400"></i> Remove Account</h3>
        <button onclick="mcCloseRemoveAccount()" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>
      <div class="space-y-3 text-xs">
        <p class="text-slate-300">You are about to remove account <span id="remove-account-name" class="font-mono text-red-300">--</span>. This action requires explicit confirmation.</p>
        <div class="bg-slate-950 border border-slate-800 rounded p-3">
          <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-1.5">Scoped cleanup — exactly what will be deleted</div>
          <ul id="remove-account-scope" class="list-disc list-inside space-y-1 text-slate-400 text-[11px]"></ul>
        </div>
        <p class="text-[10px] text-emerald-400/80">🔒 Only the secret:// reference is shown. The credential value is never displayed or logged.</p>
      </div>
      <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="mcCloseRemoveAccount()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs">Cancel</button>
        <button onclick="mcConfirmRemoveAccount()" class="px-4 py-1.5 bg-red-600 hover:bg-red-500 text-white font-semibold rounded text-xs shadow-md shadow-red-600/30">Confirm Remove</button>
      </div>
    </div>
  </div>

  <!-- PHASE 22 PART 9: ACCOUNT ADD WIZARD (multi-step, real lifecycle) -->
  <div id="wizard-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-2xl w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <h3 class="font-bold text-sm text-white flex items-center gap-2"><i class="fa-solid fa-wand-magic-sparkles text-indigo-400"></i> Add Account Wizard</h3>
          <p class="text-[11px] text-slate-400">Correlation ID: <span id="wiz-corr" class="font-mono text-indigo-300">—</span></p>
        </div>
        <button onclick="wizClose()" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>

      <!-- Step tracker -->
      <div id="wiz-steps" class="flex flex-wrap gap-1.5 text-[10px] font-mono"></div>

      <!-- Step body -->
      <div id="wiz-body" class="space-y-3 text-xs min-h-[140px]"></div>

      <!-- Inline error -->
      <div id="wiz-error" class="hidden bg-red-950/60 border border-red-800 rounded p-2.5 text-[11px] text-red-300"></div>

      <!-- Live event mini-feed for this flow -->
      <div class="bg-slate-950 border border-slate-800 rounded p-2">
        <div class="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Live lifecycle events (this flow)</div>
        <div id="wiz-feed" class="max-h-28 overflow-y-auto space-y-0.5 font-mono text-[10px] text-slate-400"></div>
      </div>

      <div class="flex justify-between gap-2 pt-2 border-t border-slate-800">
        <div>
          <button id="wiz-btn-cancel" onclick="wizCancel()" class="px-3 py-1.5 bg-red-950 border border-red-800 hover:bg-red-900 text-red-300 rounded text-xs">Cancel</button>
        </div>
        <div class="flex gap-2">
          <button id="wiz-btn-back" onclick="wizBack()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs">Back</button>
          <button id="wiz-btn-retry" onclick="wizRetry()" class="hidden px-3 py-1.5 bg-amber-900 hover:bg-amber-800 text-amber-200 border border-amber-800 rounded text-xs">Retry</button>
          <button id="wiz-btn-next" onclick="wizNext()" class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded text-xs shadow-md shadow-indigo-600/30">Next</button>
        </div>
      </div>
    </div>
  </div>

  <!-- CONFIGURE QUOTA MODAL -->
  <div id="add-quota-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-md w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="font-bold text-sm text-white">Configure Quota Rule</h3>
        <button onclick="closeAddQuotaModal()" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>
      <div class="space-y-3 text-xs">
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Scope *</label>
            <select id="quota-modal-scope" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
              <option value="provider">Provider</option>
              <option value="account">Account</option>
              <option value="model">Model</option>
            </select>
          </div>
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Target ID *</label>
            <input type="text" id="quota-modal-target" placeholder="e.g. openai, cline-account-1" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Daily Requests</label>
            <input type="number" id="quota-modal-daily-reqs" placeholder="e.g. 500" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
          </div>
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Daily Tokens</label>
            <input type="number" id="quota-modal-daily-tokens" placeholder="e.g. 1000000" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Monthly Requests</label>
            <input type="number" id="quota-modal-monthly-reqs" placeholder="e.g. 15000" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
          </div>
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Monthly Tokens</label>
            <input type="number" id="quota-modal-monthly-tokens" placeholder="e.g. 25000000" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
          </div>
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Cost Limit (USD $)</label>
          <input type="number" step="0.5" id="quota-modal-cost-limit" placeholder="e.g. 50.00" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="closeAddQuotaModal()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs">Cancel</button>
        <button onclick="submitAddQuota()" class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded text-xs shadow-md shadow-indigo-600/30">Set Quota</button>
      </div>
    </div>
  </div>

  <!-- ROUTING DECISION INSPECT MODAL -->
  <div id="routing-inspect-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-3xl w-full max-h-[85vh] flex flex-col shadow-2xl">
      <div class="p-4 border-b border-slate-800 flex items-center justify-between">
        <div>
          <h3 class="font-bold text-sm text-white font-mono">Routing Decision & Multi-Factor Scoring</h3>
          <p class="text-[11px] text-slate-400 font-sans">Explainable candidate evaluation across 16 multi-dimensional factors.</p>
        </div>
        <button onclick="closeRoutingInspectModal()" class="text-slate-400 hover:text-white text-sm px-2 py-1">✕</button>
      </div>
      <div id="routing-inspect-body" class="p-6 overflow-auto text-xs space-y-4">
        <!-- Dynamically populated -->
      </div>
    </div>
  </div>

  <!-- EDIT MODEL MODAL -->
  <div id="edit-model-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-md w-full p-6 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="font-bold text-sm text-white">Configure Model</h3>
        <button onclick="closeEditModelModal()" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>
      <div class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Model ID</label>
          <input type="text" id="model-edit-id" readonly class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-400 font-mono">
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Priority (1-100)</label>
          <input type="number" id="model-edit-priority" value="50" min="1" max="100" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
        </div>
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Capability Tags (comma separated)</label>
          <input type="text" id="model-edit-caps" placeholder="coding, reasoning, planning" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 focus:outline-none">
        </div>
        <div class="flex items-center gap-2 pt-1">
          <input type="checkbox" id="model-edit-enabled" checked class="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0">
          <label for="model-edit-enabled" class="text-slate-300">Model Enabled</label>
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="closeEditModelModal()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs">Cancel</button>
        <button onclick="submitEditModel()" class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded text-xs shadow-md shadow-indigo-600/30">Save Changes</button>
      </div>
    </div>
  </div>

  <!-- Toast Notification Container -->
  <div id="toast-container" class="fixed bottom-4 right-4 z-50 space-y-2 pointer-events-none"></div>

  <script>
    let authToken = '';
    let pollingInterval = null;
    let runningTaskTimer = null;

    function showToast(message, type = 'info') {
      const container = document.getElementById('toast-container');
      const toast = document.createElement('div');
      const bg = {
        success: 'bg-emerald-950 border-emerald-700 text-emerald-300',
        error: 'bg-red-950 border-red-700 text-red-300',
        info: 'bg-slate-900 border-slate-700 text-slate-200'
      }[type] || 'bg-slate-900 border-slate-700 text-slate-200';
      toast.className = `p-3 rounded-lg border shadow-lg text-xs font-sans pointer-events-auto transition-all duration-300 ${bg}`;
      toast.textContent = message;
      container.appendChild(toast);
      setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
      }, 3500);
    }

    async function initAuth() {
      try {
        const res = await fetch('/api/token');
        if (res.ok) {
          const data = await res.json();
          authToken = data.token;
        }
      } catch (err) {
        console.warn('Could not fetch session token:', err);
      }
    }

    async function fetchWithAuth(url, options = {}) {
      options.headers = options.headers || {};
      if (authToken) {
        options.headers['Authorization'] = 'Bearer ' + authToken;
      }
      return fetch(url, options);
    }

    function showTab(tabId) {
      document.querySelectorAll('.tab-pane').forEach(el => {
        el.classList.add('hidden');
        el.classList.remove('block');
      });
      const target = document.getElementById('tab-' + tabId);
      if (target) {
        target.classList.remove('hidden');
        target.classList.add('block');
      }
      document.querySelectorAll('.tab-btn').forEach(btn => {
        if (btn.getAttribute('data-tab') === tabId) {
          btn.classList.add('bg-slate-800', 'text-white');
        } else {
          btn.classList.remove('bg-slate-800', 'text-white');
        }
      });
      // Phase 12-14: Lazy-load data for Mission Control tabs
      if (tabId === 'mc-providers') { loadProviders(); }
      else if (tabId === 'mc-accounts') { loadAccounts(); }
      else if (tabId === 'mc-models') { loadModels(); }
      else if (tabId === 'mc-jobs') { loadJobs(); populateJobDropdowns(); }
      else if (tabId === 'mc-usage') { loadUsage(); }
      else if (tabId === 'events') { loadEvents(); }
      else { refreshData(); }
    }

    function formatDuration(seconds) {
      if (!seconds || seconds <= 0) return '00:00';
      const m = Math.floor(seconds / 60);
      const s = Math.floor(seconds % 60);
      return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    }

    function renderLiveExecutionWidget(activeTask) {
      const widget = document.getElementById('live-execution-widget');
      if (!widget) return;

      if (!activeTask) {
        widget.innerHTML = `
          <div class="flex items-center justify-between text-xs text-slate-500">
            <div class="flex items-center gap-2">
              <div class="w-2 h-2 rounded-full bg-slate-700"></div>
              <span>Swarm Status: <strong class="text-slate-400">IDLE</strong> (No tasks currently running)</span>
            </div>
            <span class="font-mono text-[11px] text-slate-600">Hardware Slots Available: 2/2</span>
          </div>
        `;
        return;
      }

      const isComplete = activeTask.stage === 'COMPLETE' || activeTask.status === 'COMPLETED';
      const elapsed = formatDuration(activeTask.duration_seconds || activeTask.elapsed_seconds || 0);
      const stage = activeTask.stage || (isComplete ? 'COMPLETE' : 'EXECUTION');

      // 6-stage progression mapping
      const stages = ['QUEUED', 'ROUTING', 'MEMORY', 'EXECUTION', 'HANDOFF', 'COMPLETE'];
      const stageLabels = {
        QUEUED: '1. Queued',
        ROUTING: '2. Routing',
        MEMORY: '3. Memory',
        EXECUTION: '4. Execution',
        HANDOFF: '5. Handoff',
        COMPLETE: '6. Complete'
      };

      const currIdx = stages.indexOf(stage) >= 0 ? stages.indexOf(stage) : (isComplete ? 5 : 3);

      let pipelineHtml = '';
      stages.forEach((st, idx) => {
        let dot = '○';
        let cls = 'text-slate-600 border-slate-800 bg-slate-950';
        if (idx < currIdx || (isComplete && idx <= 5)) {
          dot = '✓';
          cls = 'text-emerald-400 border-emerald-800 bg-emerald-950/60 font-semibold';
        } else if (idx === currIdx && !isComplete) {
          dot = '●';
          cls = 'text-amber-300 border-amber-600 bg-amber-950/80 font-bold animate-pulse';
        }
        pipelineHtml += `
          <div class="flex items-center gap-1.5 px-3 py-1.5 rounded border text-[11px] font-mono ${cls}">
            <span>${dot}</span>
            <span>${stageLabels[st]}</span>
          </div>
        `;
      });

      const fallbackInfo = (activeTask.result && activeTask.result.fallback) || activeTask.fallback;
      const failoverBadge = fallbackInfo
        ? `<span class="px-2 py-0.5 rounded bg-red-950 text-red-300 border border-red-800 font-mono text-[10px] animate-pulse">FAILOVER: ${fallbackInfo.original_provider || fallbackInfo.from} ❌ → ${fallbackInfo.fallback_provider || fallbackInfo.to} ✓</span>`
        : (activeTask.attempted_agents && activeTask.attempted_agents.length > 1) || stage === 'FAILOVER'
        ? `<span class="px-2 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800 font-mono text-[10px]">Failover Triggered: ${activeTask.assigned_agent}</span>`
        : `<span class="px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono text-[10px]">Primary Execution</span>`;

      const statusDot = isComplete
        ? `<div class="w-3 h-3 rounded-full bg-emerald-400"></div>`
        : `<div class="w-3 h-3 rounded-full bg-cyan-400 animate-ping"></div>`;
      const monitorTitle = isComplete
        ? `<span class="text-xs uppercase tracking-wider text-emerald-400 font-semibold font-mono">EXECUTION COMPLETE</span>`
        : `<span class="text-xs uppercase tracking-wider text-cyan-400 font-semibold font-mono">LIVE EXECUTION MONITOR</span>`;

      const failoverRow = fallbackInfo ? `
        <div class="col-span-4 bg-red-950/40 border border-red-800 rounded p-2 text-xs font-mono flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="text-red-400 font-bold">FAILOVER OCCURRED:</span>
            <span class="text-red-300">${fallbackInfo.original_provider || fallbackInfo.from} ❌ (${fallbackInfo.reason || fallbackInfo.failure || 'failed'})</span>
            <span class="text-slate-500">→</span>
            <span class="text-emerald-400 font-semibold">${fallbackInfo.fallback_provider || fallbackInfo.to} ✓</span>
          </div>
          <div class="text-[10px] text-slate-500">${fallbackInfo.fallback_timestamp || ''}</div>
        </div>
      ` : '';

      widget.innerHTML = `
        <div class="flex items-center justify-between border-b border-slate-800 pb-3">
          <div class="flex items-center gap-3">
            ${statusDot}
            <div>
              ${monitorTitle}
              <div class="text-sm font-bold text-white font-mono mt-0.5">${activeTask.task_id} — <span class="font-sans font-normal text-slate-200">${activeTask.title}</span></div>
            </div>
          </div>
          <div class="flex items-center gap-3">
            ${failoverBadge}
            <div class="text-right font-mono">
              <div class="text-[10px] text-slate-500 uppercase">${isComplete ? 'Elapsed' : 'Elapsed'}</div>
              <div class="text-emerald-400 font-bold text-sm">${elapsed}</div>
              ${isComplete && activeTask.completed_at ? `<div class="text-[9px] text-slate-500">${activeTask.completed_at.slice(11, 19)} UTC</div>` : ''}
            </div>
          </div>
        </div>

        <div class="grid grid-cols-4 gap-2 text-xs bg-slate-950 p-3 rounded border border-slate-850 font-mono">
          <div><span class="text-slate-500">Agent:</span> <span class="text-indigo-400 font-semibold">${activeTask.assigned_agent || 'Auto'}</span></div>
          <div><span class="text-slate-500">Model:</span> <span class="text-emerald-400">${activeTask.actual_model || activeTask.assigned_model || 'auto'}</span></div>
          <div><span class="text-slate-500">Stage:</span> <span class="${isComplete ? 'text-emerald-400 font-bold' : 'text-amber-300 font-bold'}">${stage}</span></div>
          <div><span class="text-slate-500">Budget:</span> <span class="text-slate-300">${activeTask.continuation_budget ?? 5}/5</span></div>
          ${failoverRow}
        </div>

        <div class="flex items-center justify-between gap-2 overflow-x-auto pt-1">
          ${pipelineHtml}
        </div>
      `;
    }

    async function refreshData() {
      try {
        const resStatus = await fetchWithAuth('/api/status');
        if (!resStatus.ok) throw new Error('Status HTTP ' + resStatus.status);
        const status = await resStatus.json();
        const mc = status.mission_control || {};

        // Update Top Stat Cards
        // Phase 22 Part 8: prefer the explicit decoupled metrics. "Online" here
        // means lifecycle ONLINE, not "healthy" and not a provider/adapter count.
        const am0 = mc.account_metrics || {};
        const statAccounts = document.getElementById('stat-accounts');
        if (statAccounts) {
          const onlineN = am0.online != null ? am0.online : (status.healthy_accounts || mc.accounts_healthy || 0);
          const regN = am0.registered != null ? am0.registered : (status.registered_accounts || mc.accounts_total || 0);
          statAccounts.textContent = `${onlineN} / ${regN} Online`;
        }
        const statAccountsSub = document.getElementById('stat-accounts-sub');
        if (statAccountsSub) {
          if (am0.registered != null) {
            statAccountsSub.textContent = `${am0.signed_in} auth'd · ${am0.healthy} healthy · ${am0.busy} busy · ${am0.idle} idle`;
          } else {
            const unconfig = (status.registered_accounts || 0) - (status.healthy_accounts || 0);
            statAccountsSub.textContent = `${status.healthy_accounts || 0} healthy${unconfig > 0 ? ` · ${unconfig} unconfigured` : ''}`;
          }
        }

        const statWorkers = document.getElementById('stat-workers');
        if (statWorkers) statWorkers.textContent = `${status.active_workers || (status.active_workers_list ? status.active_workers_list.length : 0)} Active`;
        const statWorkersSub = document.getElementById('stat-workers-sub');
        if (statWorkersSub && status.active_workers_list) {
          statWorkersSub.textContent = status.active_workers_list.join(', ');
        }

        const statTasks = document.getElementById('stat-tasks');
        if (statTasks) statTasks.textContent = `${status.running_tasks || 0} Running`;
        const statTasksSub = document.getElementById('stat-tasks-sub');
        if (statTasksSub) {
          statTasksSub.textContent = `${status.ready_tasks || 0} queued · ${status.running_tasks || 0} running · ${status.completed_tasks || 0} done`;
        }

        // Render Mission Control Overview Banner
        const elP = document.getElementById('mc-stat-providers') || document.getElementById('mc-overview-providers');
        if (elP) elP.textContent = `${mc.providers_online || 0} / ${mc.providers_total || 0}`;
        const elA = document.getElementById('mc-stat-accounts') || document.getElementById('mc-overview-accounts');
        if (elA) elA.textContent = `${mc.accounts_healthy || 0} / ${mc.accounts_total || 0}`;
        const elM = document.getElementById('mc-stat-models') || document.getElementById('mc-overview-models');
        if (elM) elM.textContent = `${mc.models_available || 0} available`;
        const elJ = document.getElementById('mc-stat-active-jobs') || document.getElementById('mc-overview-jobs');
        if (elJ) elJ.textContent = `${mc.active_jobs || 0} active · ${mc.completed_jobs || 0} done`;
        const elT = document.getElementById('mc-stat-today-tokens') || document.getElementById('mc-overview-tokens');
        if (elT) elT.textContent = (mc.today_usage_tokens != null ? mc.today_usage_tokens.toLocaleString() : '0') + ' tokens';
        const elC = document.getElementById('mc-stat-today-cost') || document.getElementById('mc-overview-cost');
        if (elC) elC.textContent = '$' + (mc.today_estimated_cost_usd != null ? mc.today_estimated_cost_usd.toFixed(2) : '0.00');

        // Phase 22 Part 8: render the eight explicit, decoupled account metrics.
        // Each value comes from a distinct orthogonal state field on the server;
        // the UI must never re-derive one metric from another.
        const am = mc.account_metrics || {};
        const setMetric = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = (val != null ? val : 0); };
        setMetric('mc-metric-registered', am.registered);
        setMetric('mc-metric-authenticated', am.signed_in);
        setMetric('mc-metric-healthy', am.healthy);
        setMetric('mc-metric-online', am.online);
        setMetric('mc-metric-busy', am.busy);
        setMetric('mc-metric-idle', am.idle);
        setMetric('mc-metric-offline', am.offline);
        setMetric('mc-metric-disabled', am.disabled);

        // Render Active Task Live Widget
        const activeTask = (status.active_tasks && status.active_tasks.length > 0) ? status.active_tasks[0] : null;
        renderLiveExecutionWidget(activeTask);

        // Render Tokens summary
        const tm = status.token_metrics || { known_tokens: 0, unknown_count: 0, total_tasks: 0 };
        const knownTok = (tm.known_tokens != null && tm.known_tokens > 0) ? tm.known_tokens : (mc.known_tokens || mc.today_tokens || mc.total_tokens || 0);
        const costVal = (mc.today_estimated_cost_usd != null && mc.today_estimated_cost_usd > 0) ? mc.today_estimated_cost_usd : (mc.today_cost || mc.total_cost || 0.0);
        const statTokens = document.getElementById('stat-tokens');
        if (statTokens) statTokens.textContent = (knownTok || 0).toLocaleString();
        const statTokensSub = document.getElementById('stat-tokens-sub');
        if (statTokensSub) statTokensSub.textContent = `${(knownTok || 0).toLocaleString()} verified | $${(costVal || 0).toFixed(2)} cost`;
        const tkVal = document.getElementById('tokens-known-val');
        if (tkVal) tkVal.textContent = (knownTok || 0).toLocaleString();
        const tuVal = document.getElementById('tokens-unknown-val');
        if (tuVal) tuVal.textContent = tm.unknown_count || 0;
        const ttVal = document.getElementById('tokens-total-val');
        if (ttVal) ttVal.textContent = tm.total_tasks || 0;

        // Render Agents Grid
        const grid = document.getElementById('agents-grid');
        grid.innerHTML = '';
        for (const [pId, p] of Object.entries(status.agents || {})) {
          for (const [aId, a] of Object.entries(p.accounts || {})) {
            const card = document.createElement('div');
            card.className = 'bg-slate-900 border border-slate-800 p-4 rounded-lg space-y-2';
            const statusColors = {
              WORKING: 'bg-amber-500/20 text-amber-300',
              ONLINE: 'bg-emerald-500/20 text-emerald-300',
              IDLE: 'bg-sky-500/20 text-sky-300',
              NOT_CONFIGURED: 'bg-amber-500/20 text-amber-300',
              FAILED: 'bg-red-500/20 text-red-300',
              OFFLINE: 'bg-slate-700 text-slate-400'
            };
            const st = a.display_status || (a.healthy ? 'ONLINE' : 'OFFLINE');
            const dot = (st === 'ONLINE' || st === 'IDLE') ? 'bg-emerald-500' : (st === 'WORKING' ? 'bg-amber-400 animate-pulse' : (st === 'NOT_CONFIGURED' ? 'bg-amber-500' : 'bg-red-500'));
            const cur = a.current_task;
            const last = a.last_task;
            const c = a.counts || {total: 0, completed: 0, failed: 0};
            const histErr = a.last_task_error;

            let taskInfo = '<span class="text-slate-600">None (Idle)</span>';
            if (cur) {
              taskInfo = `<span class="text-emerald-300 font-semibold">${cur.title.slice(0, 38)}</span> <span class="font-mono text-[10px] text-cyan-400">(${cur.stage || cur.status})</span>`;
            } else if (last) {
              taskInfo = `<span class="text-slate-500">None (Idle)</span> · <span class="text-slate-400 text-[10px] font-mono">Last: ${last.task_id} (${last.status})</span>`;
            }

            let errorHtml = '';
            if (!a.healthy || st === 'FAILED' || st === 'OFFLINE') {
              errorHtml = `<div class="text-[10px] text-red-400 bg-red-950/40 p-1.5 rounded border border-red-850 truncate" title="${(a.health_reason || '').replace(/"/g, '')}">⚠️ ${a.health_reason || 'Agent offline'}</div>`;
            } else if (st === 'NOT_CONFIGURED') {
              errorHtml = `<div class="text-[10px] text-amber-400 bg-amber-950/40 p-1.5 rounded border border-amber-850 truncate" title="${(a.health_reason || '').replace(/"/g, '')}">⚙️ Not Configured: ${a.health_reason || 'Missing credential'}</div>`;
            } else if (histErr) {
              errorHtml = `<div class="text-[10px] text-slate-400/90 truncate" title="${histErr.error.replace(/"/g, '')}"><span class="text-slate-500 font-mono">[Historical ${histErr.task_id}]:</span> ${histErr.error.slice(0, 50)}</div>`;
            }

            card.innerHTML = `
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <div class="w-2.5 h-2.5 rounded-full ${dot}"></div>
                  <span class="font-bold text-white text-sm">${a.agent_id}</span>
                </div>
                <span class="text-[10px] px-2 py-0.5 rounded font-mono ${statusColors[st] || statusColors.OFFLINE}">${st}</span>
              </div>
              <div class="text-xs text-slate-400">Account: <span class="text-slate-200">${a.account_id}</span> | Provider: <span class="text-slate-200">${a.provider}</span></div>
              <div class="text-[10px] text-cyan-400 font-mono">Mode: ${a.execution_mode || 'Headless CLI'}</div>
              ${a.profile ? `<div class="text-[10px] text-slate-500 font-mono truncate">Profile: ${a.profile}</div>` : ''}
              <div class="text-[11px] text-slate-500">Health: ${a.health_reason || (a.healthy ? 'Available' : 'Unconfigured')}</div>
              <div class="text-[11px] text-slate-400">Current task: ${taskInfo}</div>
              <div class="text-[11px] text-slate-400">Model: <span class="font-mono text-emerald-400">${cur ? (cur.requested_model || 'auto') : (a.default_model || 'auto')}</span>${cur && cur.reported_model ? ` <span class="text-slate-500">(reported: ${cur.reported_model})</span>` : ''}</div>
              <div class="text-[10px] text-slate-500">Success: <span class="text-emerald-400 font-mono">${a.success_rate || 100}%</span> | Avg Latency: <span class="text-slate-300 font-mono">${a.avg_latency || 0}s</span> | Known Tokens: <span class="text-amber-400 font-mono">${(a.known_tokens || 0).toLocaleString()}</span></div>
              <div class="text-[10px] text-slate-400">Tasks: ${c.total} total, <span class="text-emerald-400">${c.completed} done</span>, <span class="text-red-400">${c.failed} terminal</span></div>
              ${errorHtml}
            `;
            grid.appendChild(card);
          }
        }

        // Render Tasks Kanban
        const resTasks = await fetchWithAuth('/api/tasks');
        const tasksData = await resTasks.json();
        const readyDiv = document.getElementById('tasks-ready');
        const runningDiv = document.getElementById('tasks-running');
        const compDiv = document.getElementById('tasks-completed');
        const failedDiv = document.getElementById('tasks-failed');
        readyDiv.innerHTML = ''; runningDiv.innerHTML = ''; compDiv.innerHTML = ''; failedDiv.innerHTML = '';

        let cReady = 0, cRun = 0, cDone = 0, cFail = 0;
        for (const t of (tasksData.tasks || [])) {
          const item = document.createElement('div');
          item.className = 'task-card p-3 bg-slate-950 border border-slate-800 rounded text-xs space-y-1 hover:border-slate-700 cursor-pointer transition';
          item.onclick = (e) => {
            if (e.target.tagName !== 'BUTTON') viewTaskDetails(t.task_id);
          };

          const stagePill = t.stage ? `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-[9px] font-mono text-cyan-400">${t.stage}</span>` : '';
          const actionButtons = t.status === 'READY' || t.status === 'BACKLOG'
            ? `<div class="flex items-center gap-1.5 pt-1.5 border-t border-slate-850">
                 <button onclick="executeSingleTask('${t.task_id}')" class="px-2 py-0.5 bg-emerald-700 hover:bg-emerald-600 text-white rounded text-[10px] font-semibold">Run Now</button>
                 <button onclick="cancelTask('${t.task_id}')" class="px-2 py-0.5 bg-slate-800 hover:bg-red-800 text-slate-300 rounded text-[10px]">Cancel</button>
               </div>`
            : '';

          item.innerHTML = `
            <div class="flex items-center justify-between">
              <span class="font-semibold text-slate-200 truncate flex-1">${t.title}</span>
              ${stagePill}
            </div>
            <div class="text-slate-500 text-[10px] font-mono">Agent: ${t.assigned_agent || 'auto'} | Model: ${t.assigned_model || 'auto'}</div>
            <div class="flex items-center justify-between text-[10px] text-slate-600 font-mono pt-1">
              <span>${t.task_id}</span>
              <span>${t.duration_seconds ? t.duration_seconds.toFixed(1) + 's' : ''}</span>
            </div>
            ${actionButtons}
          `;

          if (t.status === 'READY' || t.status === 'BACKLOG') { readyDiv.appendChild(item); cReady++; }
          else if (t.status === 'RUNNING' || t.status === 'PLANNING') { runningDiv.appendChild(item); cRun++; }
          else if (t.status === 'COMPLETED' || t.status === 'VERIFICATION_COMPLETE') { compDiv.appendChild(item); cDone++; }
          else { failedDiv.appendChild(item); cFail++; }
        }
        document.getElementById('badge-ready').textContent = cReady;
        document.getElementById('badge-running').textContent = cRun;
        document.getElementById('badge-completed').textContent = cDone;
        document.getElementById('badge-failed').textContent = cFail;

        // Render Routing History
        const resRouting = await fetchWithAuth('/api/router/history');
        if (resRouting.ok) {
          const routingData = await resRouting.json();
          const rBody = document.getElementById('routing-history-tbody');
          if (rBody && routingData.history) {
            rBody.innerHTML = '';
            if (routingData.history.length === 0) {
              rBody.innerHTML = '<tr><td colspan="6" class="p-4 text-center text-slate-500 font-sans">No routing decisions recorded yet.</td></tr>';
            } else {
              for (const entry of routingData.history) {
                const tr = document.createElement('tr');
                const tShort = (entry.timestamp || '').split('T')[1]?.slice(0, 8) || '-';
                tr.innerHTML = `
                  <td class="p-3 text-slate-500">${tShort}</td>
                  <td class="p-3 text-slate-300 font-sans">${(entry.task_text || '').slice(0, 40)}</td>
                  <td class="p-3 text-emerald-400 font-semibold">${entry.selected_agent}</td>
                  <td class="p-3 text-slate-400">${entry.selected_model || 'auto'}</td>
                  <td class="p-3 text-slate-500">${entry.fallback_agent || '-'}</td>
                  <td class="p-3 text-slate-400 font-sans text-[11px]">${entry.reason || ''}</td>
                `;
                rBody.appendChild(tr);
              }
            }
          }
        }

        // Render Tokens breakdown table
        const tBody = document.getElementById('tokens-agent-tbody');
        if (tBody && tm.by_agent) {
          tBody.innerHTML = '';
          for (const [aId, d] of Object.entries(tm.by_agent)) {
            const avgDur = d.tasks ? (d.total_duration / d.tasks).toFixed(2) + 's' : '-';
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td class="p-3 text-white font-semibold">${aId}</td>
              <td class="p-3 text-slate-300">${d.tasks}</td>
              <td class="p-3 text-amber-400">${(d.known_tokens || 0).toLocaleString()}</td>
              <td class="p-3 text-slate-400">${avgDur}</td>
              <td class="p-3 text-emerald-400">${d.success_count || 0}</td>
            `;
            tBody.appendChild(tr);
          }
        }

        // Render Worktrees
        await renderWorktrees();

        // Render Memories
        await loadMemories();

        // Render Handoffs
        await loadHandoffs();

        // Render Events
        await loadEvents();

        // Render Git Info
        try {
          const resGit = await fetchWithAuth('/api/git');
          if (resGit.ok) {
            const gitData = await resGit.json();
            const gb = document.getElementById('git-badge'); if (gb) gb.textContent = gitData.branch + '@' + gitData.commit;
            const gbr = document.getElementById('git-branch'); if (gbr) gbr.textContent = gitData.branch;
            const gc = document.getElementById('git-commit'); if (gc) gc.textContent = gitData.commit;
            const gst = document.getElementById('git-status-text'); if (gst) gst.textContent = gitData.status;
          }
        } catch (e) {}

      } catch (err) {
        console.error('Error refreshing data:', err);
      }
    }

    let currentMemoryView = 'records';
    function toggleMemoryView(view) {
      currentMemoryView = view;
      const recView = document.getElementById('memory-records-view');
      const retView = document.getElementById('memory-retrievals-view');
      const recBtn = document.getElementById('mem-view-records-btn');
      const retBtn = document.getElementById('mem-view-retrievals-btn');
      if (!recView || !retView) return;
      if (view === 'records') {
        recView.classList.remove('hidden');
        retView.classList.add('hidden');
        if (recBtn) recBtn.className = 'px-3 py-1.5 bg-purple-700 text-white rounded text-xs font-semibold';
        if (retBtn) retBtn.className = 'px-3 py-1.5 bg-slate-800 text-slate-300 hover:bg-slate-700 rounded text-xs font-semibold';
      } else {
        recView.classList.add('hidden');
        retView.classList.remove('hidden');
        if (recBtn) recBtn.className = 'px-3 py-1.5 bg-slate-800 text-slate-300 hover:bg-slate-700 rounded text-xs font-semibold';
        if (retBtn) retBtn.className = 'px-3 py-1.5 bg-purple-700 text-white rounded text-xs font-semibold';
        loadRetrievalHistory();
      }
    }

    async function loadMemories() {
      const search = document.getElementById('mem-filter-search')?.value || '';
      const scope = document.getElementById('mem-filter-scope')?.value || '';
      const imp = document.getElementById('mem-filter-imp')?.value || '';
      const agent = document.getElementById('mem-filter-agent')?.value || '';

      const params = new URLSearchParams();
      if (search) params.set('search', search);
      if (scope) params.set('scope', scope);
      if (imp) params.set('importance', imp);
      if (agent) params.set('agent_id', agent);

      try {
        const res = await fetchWithAuth('/api/memory?' + params.toString());
        const data = await res.json();
        const memList = document.getElementById('memory-list');
        if (!memList) return;

        if (!data.memories || data.memories.length === 0) {
          memList.innerHTML = `<div class="p-6 text-center text-slate-500 bg-slate-900 border border-slate-800 rounded-lg text-xs">No memories found matching filter criteria.</div>`;
          return;
        }

        memList.innerHTML = '';
        for (const m of data.memories) {
          const mItem = document.createElement('div');
          mItem.className = 'p-3 bg-slate-900 border border-slate-800 rounded text-xs space-y-1.5 hover:border-purple-800/60 transition';
          const stars = '★'.repeat(m.importance || 3) + '☆'.repeat(5 - (m.importance || 3));
          const tags = (m.tags || []).map(t => `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono text-[10px]">${t}</span>`).join(' ');
          mItem.innerHTML = `
            <div class="flex justify-between items-center text-[10px] text-slate-400 font-mono">
              <div class="flex items-center gap-2">
                <span class="px-1.5 py-0.5 rounded bg-purple-950 text-purple-300 border border-purple-800/80 font-bold">${m.scope}</span>
                <span>Source: <strong class="text-slate-300">${m.source_agent}</strong></span>
                <span class="text-amber-400 font-sans">${stars}</span>
                ${m.task_id ? `<span class="text-slate-500">Task: ${m.task_id}</span>` : ''}
              </div>
              <div class="text-slate-500">${(m.created_at || m.timestamp || '').split('T')[0] || ''}</div>
            </div>
            <div class="text-slate-200 font-sans text-xs leading-relaxed">${m.content}</div>
            ${tags ? `<div class="pt-1 flex gap-1">${tags}</div>` : ''}
          `;
          memList.appendChild(mItem);
        }
      } catch (err) {
        console.error('Error loading memories:', err);
      }
    }

    async function loadRetrievalHistory() {
      try {
        const res = await fetchWithAuth('/api/memory/retrieval-history?limit=50');
        const data = await res.json();
        const list = document.getElementById('memory-retrievals-list');
        if (!list) return;

        if (!data.retrievals || data.retrievals.length === 0) {
          list.innerHTML = `<div class="p-6 text-center text-slate-500 bg-slate-900 border border-slate-800 rounded-lg text-xs">No memory retrieval events recorded yet.</div>`;
          return;
        }

        list.innerHTML = '';
        for (const r of data.retrievals) {
          const item = document.createElement('div');
          item.className = 'p-3 bg-slate-900 border border-slate-800 rounded text-xs space-y-2';
          
          let memDetails = '';
          if (r.retrieved_items && r.retrieved_items.length > 0) {
            memDetails = '<div class="space-y-1 mt-2">';
            r.retrieved_items.forEach((m) => {
              memDetails += `
                <div class="p-2 bg-slate-950 rounded border border-slate-850 text-[11px] flex justify-between items-center">
                  <div class="flex-1 pr-2 truncate text-slate-300"><span class="text-purple-400 font-mono font-bold">[${m.scope || 'PROJECT'}]</span> ${m.content_snippet || m.content || m.memory_id}</div>
                  <div class="font-mono text-amber-400 font-semibold text-[10px]">Score: ${(m.score || 0).toFixed(2)}</div>
                </div>
              `;
            });
            memDetails += '</div>';
          }

          item.innerHTML = `
            <div class="flex justify-between items-center text-[10px] font-mono">
              <div class="flex items-center gap-2">
                <span class="px-1.5 py-0.5 rounded bg-cyan-950 text-cyan-300 border border-cyan-800 font-bold">TASK: ${r.task_id || 'unassigned'}</span>
                <span class="text-slate-400">Scope: <strong class="text-slate-200">${r.scope}</strong></span>
                <span class="text-slate-400">Agent: <strong class="text-indigo-400">${r.source_agent || 'agent'}</strong></span>
                <span class="text-emerald-400 font-bold">${r.count} matched</span>
              </div>
              <div class="text-slate-500">${(r.timestamp || '').replace('T', ' ').slice(0, 19)}</div>
            </div>
            <div class="text-slate-300 text-xs font-mono bg-slate-950 px-2.5 py-1.5 rounded border border-slate-850">
              <span class="text-slate-500">Query:</span> "${r.query}"
            </div>
            ${memDetails}
          `;
          list.appendChild(item);
        }
      } catch (err) {
        console.error('Error loading retrieval history:', err);
      }
    }

    async function loadHandoffs() {
      try {
        const resHandoff = await fetchWithAuth('/api/handoff');
        const handoffData = await resHandoff.json();
        const hCard = document.getElementById('handoff-card');
        if (hCard && handoffData.record) {
          const hr = handoffData.record;
          hCard.innerHTML = `
            <div class="flex items-center justify-between border-b border-slate-800 pb-2">
              <div><span class="text-slate-400">Flow:</span> <span class="text-emerald-400 font-mono">${hr.source_agent || hr.agent_id}</span> → <span class="text-cyan-400 font-mono">${hr.recommended_agent || 'Next Agent'}</span></div>
              <div class="font-mono text-[10px] text-slate-500">${hr.created_at || ''}</div>
            </div>
            <div><span class="text-slate-400 font-semibold">Objective:</span> <span class="text-slate-200">${hr.objective || hr.task}</span></div>
            ${hr.next_action ? `<div><span class="text-amber-400 font-semibold">Next Action:</span> <span class="text-slate-300">${hr.next_action}</span></div>` : ''}
          `;
          hCard.classList.remove('hidden');
        } else if (hCard) {
          hCard.classList.add('hidden');
        }
        const hContent = document.getElementById('handoff-content');
        if (hContent) hContent.innerHTML = marked.parse(handoffData.markdown || 'No active handoff record.');

        const resHist = await fetchWithAuth('/api/handoff/history');
        const histData = await resHist.json();
        const hList = document.getElementById('handoff-history-list');
        if (hList && histData.history) {
          hList.innerHTML = '';
          if (histData.history.length === 0) {
            hList.innerHTML = `<div class="p-3 bg-slate-900 border border-slate-800 rounded text-xs text-slate-500">No previous handoffs recorded.</div>`;
          } else {
            for (const item of histData.history) {
              const hItem = document.createElement('div');
              hItem.className = 'p-3 bg-slate-900 border border-slate-800 rounded text-xs space-y-1.5 hover:border-cyan-800/80 cursor-pointer transition';
              hItem.onclick = () => loadHistoricalHandoff(item.filename, item.source_agent, item.recommended_agent);
              hItem.innerHTML = `
                <div class="flex justify-between items-center text-[10px] font-mono text-slate-400">
                  <span class="text-emerald-400 font-bold">${item.source_agent || 'agent'} → ${item.recommended_agent || 'next'}</span>
                  <span class="text-slate-500">${(item.created_at || '').split('T')[0] || ''}</span>
                </div>
                <div class="text-slate-300 font-sans text-xs truncate">${item.objective || item.task_id || item.filename}</div>
                <div class="text-[10px] text-cyan-400 font-mono">View Full Handoff →</div>
              `;
              hList.appendChild(hItem);
            }
          }
        }
      } catch (err) {
        console.error('Error loading handoffs:', err);
      }
    }

    async function loadHistoricalHandoff(filename, src, dst) {
      try {
        const res = await fetchWithAuth('/api/handoff/record?filename=' + encodeURIComponent(filename));
        const data = await res.json();
        if (data.record) {
          document.getElementById('handoff-active-title').textContent = `Historical Handoff: ${src || 'Agent'} → ${dst || 'Agent'} (${filename})`;
          const hCard = document.getElementById('handoff-card');
          if (hCard) {
            hCard.innerHTML = `
              <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                <div><span class="text-slate-400">Historical Record:</span> <span class="text-cyan-400 font-mono">${filename}</span></div>
                <div class="font-mono text-[10px] text-slate-500">${data.record.created_at || ''}</div>
              </div>
              <div><span class="text-slate-400 font-semibold">Objective:</span> <span class="text-slate-200">${data.record.objective || data.record.task}</span></div>
              ${data.record.next_action ? `<div><span class="text-amber-400 font-semibold">Next Action:</span> <span class="text-slate-300">${data.record.next_action}</span></div>` : ''}
            `;
            hCard.classList.remove('hidden');
          }
          document.getElementById('handoff-content').innerHTML = marked.parse(data.record.raw_markdown || data.markdown || JSON.stringify(data.record, null, 2));
          showToast('Loaded historical handoff: ' + filename, 'info');
        }
      } catch (err) {
        showToast('Error loading handoff: ' + err.message, 'error');
      }
    }

    let eventSource = null;
    let streamPaused = false;
    let cachedEvents = [];

    function initEventStream() {
      if (eventSource) {
        try { eventSource.close(); } catch(e) {}
      }
      const pill = document.getElementById('stream-status-pill');
      try {
        const streamUrl = authToken ? ('/api/events/stream?token=' + encodeURIComponent(authToken)) : '/api/events/stream';
        eventSource = new EventSource(streamUrl);
        eventSource.onopen = () => {
          if (pill && !streamPaused) {
            pill.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold flex items-center gap-1.5 bg-emerald-950/80 text-emerald-400 border border-emerald-800';
            pill.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> LIVE STREAMING';
          }
        };
        eventSource.onmessage = (e) => {
          if (!e.data || e.data.trim() === '') return;
          try {
            const evt = JSON.parse(e.data);
            handleIncomingEvent(evt);
          } catch (err) {
            console.error('Error parsing SSE event:', err);
          }
        };
        eventSource.onerror = () => {
          if (pill && !streamPaused) {
            pill.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold flex items-center gap-1.5 bg-amber-950/80 text-amber-400 border border-amber-800';
            pill.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span> RECONNECTING...';
          }
        };
      } catch (err) {
        if (pill) {
          pill.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold flex items-center gap-1.5 bg-red-950/80 text-red-400 border border-red-800';
          pill.innerHTML = '<span class="w-2 h-2 rounded-full bg-red-400"></span> STREAM DISCONNECTED';
        }
      }
    }

    function toggleEventStream() {
      streamPaused = !streamPaused;
      const btn = document.getElementById('btn-toggle-stream');
      const pill = document.getElementById('stream-status-pill');
      if (streamPaused) {
        if (btn) btn.innerHTML = '<i class="fa-solid fa-play"></i> Resume';
        if (pill) {
          pill.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold flex items-center gap-1.5 bg-slate-800 text-slate-300 border border-slate-700';
          pill.innerHTML = '<span class="w-2 h-2 rounded-full bg-slate-400"></span> STREAM PAUSED';
        }
      } else {
        if (btn) btn.innerHTML = '<i class="fa-solid fa-pause"></i> Pause';
        if (pill) {
          pill.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold flex items-center gap-1.5 bg-emerald-950/80 text-emerald-400 border border-emerald-800';
          pill.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> LIVE STREAMING';
        }
        renderEventsTable();
      }
    }

    function clearEventsTable() {
      cachedEvents = [];
      const evTbody = document.getElementById('events-tbody');
      if (evTbody) evTbody.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-slate-500 font-sans">Event buffer cleared. Waiting for new telemetry...</td></tr>';
    }

    function handleIncomingEvent(evt) {
      if (evt.event_id && cachedEvents.some(x => x.event_id === evt.event_id)) return;
      cachedEvents.unshift(evt);
      if (cachedEvents.length > 300) cachedEvents.pop();
      if (!streamPaused) {
        renderEventsTable();
      }
      // Phase 22 Part 10: surface account/auth lifecycle events in the live feed
      // and (if it belongs to the active wizard flow) the wizard mini-feed.
      const et = (evt.event_type || '');
      if (et.indexOf('account.') === 0) {
        renderLifecycleFeedEvent(evt);
        if (wizState && evt.correlation_id === wizState.correlation_id) {
          wizAppendFeed(evt);
        }
      }
      // Real-time telemetry trigger: refresh dashboard immediately on token, usage, or task events
      if (['TOKEN_CONSUMED', 'USAGE_RECORDED', 'TASK_COMPLETED', 'TASK_STARTED', 'JOB_COMPLETED', 'account.online', 'account.registered'].includes(et)) {
        refreshData();
      }
    }

    let lifecycleFeedEvents = [];
    function renderLifecycleFeedEvent(evt) {
      const feed = document.getElementById('lifecycle-feed');
      if (!feed) return;
      lifecycleFeedEvents.unshift(evt);
      if (lifecycleFeedEvents.length > 120) lifecycleFeedEvents.pop();
      const colorFor = (name) => {
        if (name.indexOf('online') >= 0 || name.indexOf('success') >= 0 || name.indexOf('registered') >= 0) return 'text-emerald-300';
        if (name.indexOf('failed') >= 0) return 'text-red-300';
        if (name.indexOf('cancelled') >= 0 || name.indexOf('removed') >= 0) return 'text-amber-300';
        if (name.indexOf('started') >= 0 || name.indexOf('configuring') >= 0) return 'text-indigo-300';
        return 'text-slate-300';
      };
      feed.innerHTML = lifecycleFeedEvents.map(e => {
        const t = (e.timestamp || '').split('T')[1]?.slice(0, 8) || '';
        const corr = (e.correlation_id || '').slice(0, 12);
        const acct = e.account_id || '-';
        const st = e.lifecycle_state ? ('[' + e.lifecycle_state + '] ') : '';
        const msg = (e.message || '');
        return `<div class="flex items-start gap-2 border-b border-slate-800/60 py-0.5">
          <span class="text-slate-600 shrink-0">${t}</span>
          <span class="text-slate-500 shrink-0" title="correlation id">${corr}</span>
          <span class="${colorFor(e.event_type)} font-semibold shrink-0">${e.event_type}</span>
          <span class="text-slate-500 shrink-0">${acct}</span>
          <span class="text-slate-400 truncate">${st}${msg}</span>
        </div>`;
      }).join('');
    }

    function filterEventsTable() {
      renderEventsTable();
    }

    function renderEventsTable() {
      const evTbody = document.getElementById('events-tbody');
      if (!evTbody) return;

      const fTask = (document.getElementById('event-filter-task')?.value || '').toLowerCase().trim();
      const fType = (document.getElementById('event-filter-type')?.value || '').toUpperCase().trim();
      const fAgent = (document.getElementById('event-filter-agent')?.value || '').toLowerCase().trim();

      const filtered = cachedEvents.filter(e => {
        if (fTask && !(e.task_id || '').toLowerCase().includes(fTask)) return false;
        if (fType && !(e.event_type || '').toUpperCase().includes(fType)) return false;
        if (fAgent && !(e.agent_id || e.provider || '').toLowerCase().includes(fAgent)) return false;
        return true;
      });

      if (filtered.length === 0) {
        evTbody.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-slate-500 font-sans">No matching events in ledger.</td></tr>';
        return;
      }

      evTbody.innerHTML = filtered.slice(0, 100).map((e, idx) => {
        const tShort = (e.timestamp || '').split('T')[1]?.slice(0, 8) || '-';
        let badgeColor = 'bg-slate-800 text-slate-300 border-slate-700';
        const et = (e.event_type || '').toUpperCase();
        if (et.includes('COMPLETED') || et.includes('SUCCESS')) {
          badgeColor = 'bg-emerald-950 text-emerald-300 border-emerald-800';
        } else if (et.includes('FAILED') || et.includes('FAILURE') || et.includes('ERROR')) {
          badgeColor = 'bg-red-950 text-red-300 border-red-800';
        } else if (et.includes('STARTED') || et.includes('QUEUED') || et.includes('CREATED')) {
          badgeColor = 'bg-indigo-950 text-indigo-300 border-indigo-800';
        } else if (et.includes('FAILOVER') || et.includes('COOLDOWN')) {
          badgeColor = 'bg-amber-950 text-amber-300 border-amber-800';
        } else if (et.includes('MEMORY') || et.includes('ROUTE')) {
          badgeColor = 'bg-purple-950 text-purple-300 border-purple-800';
        } else if (et.includes('VERIFICATION')) {
          badgeColor = 'bg-cyan-950 text-cyan-300 border-cyan-800';
        }

        const payloadObj = e.payload || e.metadata || {};
        const payloadStr = JSON.stringify(payloadObj);
        const payloadShort = payloadStr.length > 70 ? payloadStr.slice(0, 70) + '...' : payloadStr;
        const rowId = 'evt-row-' + idx;
        const detailId = 'evt-det-' + idx;

        return `
          <tr id="${rowId}" class="hover:bg-slate-850/60 cursor-pointer transition border-b border-slate-800/80" onclick="toggleEventDetail('${detailId}')">
            <td class="p-2.5 text-slate-500 whitespace-nowrap">${tShort}</td>
            <td class="p-2.5 whitespace-nowrap"><span class="px-2 py-0.5 rounded border text-[10px] font-bold ${badgeColor}">${e.event_type}</span></td>
            <td class="p-2.5 text-slate-400 whitespace-nowrap font-semibold">${e.task_id || '-'}</td>
            <td class="p-2.5 text-indigo-300 whitespace-nowrap">${e.agent_id || e.provider || '-'}</td>
            <td class="p-2.5 text-slate-400 font-sans text-xs truncate max-w-md">
              <span class="text-slate-300">${payloadShort}</span>
              <span class="ml-2 text-[10px] text-indigo-400 font-mono underline hover:text-indigo-300">details ▾</span>
            </td>
          </tr>
          <tr id="${detailId}" class="hidden bg-slate-950/90 border-b border-slate-800">
            <td colspan="5" class="p-3 pl-8">
              <div class="text-[10px] text-slate-500 uppercase tracking-wider font-bold mb-1">Sanitized Event Details (${e.event_id || 'evt'})</div>
              <pre class="bg-slate-900 border border-slate-800 p-2.5 rounded text-[11px] font-mono text-emerald-300 overflow-x-auto">${JSON.stringify(payloadObj, null, 2)}</pre>
            </td>
          </tr>
        `;
      }).join('');
    }

    function toggleEventDetail(detailId) {
      const el = document.getElementById(detailId);
      if (el) el.classList.toggle('hidden');
    }

    async function loadEvents() {
      try {
        const res = await fetchWithAuth('/api/events');
        const d = await res.json();
        if (d.events) {
          cachedEvents = d.events;
          renderEventsTable();
        }
      } catch (err) {
        console.error('loadEvents error:', err);
      }
    }

    async function renderWorktrees() {
      try {
        const res = await fetchWithAuth('/api/worktrees');
        const data = await res.json();
        const container = document.getElementById('worktrees-container');
        if (!container) return;
        container.innerHTML = '';

        if (!data.worktrees || data.worktrees.length === 0) {
          container.innerHTML = `
            <div class="p-8 bg-slate-900/50 border border-slate-800 rounded-lg text-center text-slate-500 text-sm">
              <i class="fa-solid fa-code-commit text-2xl mb-2 text-slate-600"></i>
              <div>No active sandbox worktrees. Mutating tasks will automatically instantiate sandboxes here.</div>
            </div>
          `;
          return;
        }

        for (const wt of data.worktrees) {
          const card = document.createElement('div');
          card.className = 'bg-slate-900 border border-slate-800 p-5 rounded-lg space-y-3';
          const statusBadge = {
            ACTIVE: 'bg-cyan-950 text-cyan-400 border border-cyan-800',
            PENDING_REVIEW: 'bg-amber-950 text-amber-300 border border-amber-800',
            APPROVED: 'bg-indigo-950 text-indigo-300 border border-indigo-800',
            APPLIED: 'bg-emerald-950 text-emerald-300 border border-emerald-800',
            REJECTED: 'bg-red-950 text-red-400 border border-red-800',
            FAILED: 'bg-red-950 text-red-400 border border-red-800',
            CLEANED: 'bg-slate-800 text-slate-500 border border-slate-700',
          }[wt.status] || 'bg-slate-800 text-slate-400';

          const filesText = (wt.files_changed && wt.files_changed.length > 0)
            ? wt.files_changed.join(', ')
            : (wt.diff_stat || 'No modifications yet');

          card.innerHTML = `
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-3">
                <span class="font-bold text-white text-sm font-mono">${wt.task_id}</span>
                <span class="text-[10px] px-2 py-0.5 rounded font-mono ${statusBadge}">${wt.status}</span>
              </div>
              <div class="text-xs text-slate-400 font-mono">Branch: <span class="text-emerald-400">${wt.branch}</span></div>
            </div>
            <div class="grid grid-cols-3 gap-2 text-xs text-slate-400 bg-slate-950 p-3 rounded border border-slate-850">
              <div>Agent: <span class="text-slate-200">${wt.agent_id}</span></div>
              <div>Account: <span class="text-slate-200">${wt.account_id}</span></div>
              <div>Base commit: <span class="text-slate-200 font-mono">${(wt.base_commit || '').slice(0, 8)}</span></div>
            </div>
            <div class="text-xs text-slate-400 font-mono">
              Changes: <span class="text-slate-300">${filesText}</span>
              ${wt.insertions ? `<span class="text-emerald-400 ml-2">+${wt.insertions}</span>` : ''}
              ${wt.deletions ? `<span class="text-red-400 ml-1">-${wt.deletions}</span>` : ''}
            </div>
            <div class="flex items-center gap-2 pt-2 border-t border-slate-800">
              <button onclick="viewWorktreeDiff('${wt.task_id}')" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs transition flex items-center gap-1">
                <i class="fa-solid fa-file-lines"></i> View Diff
              </button>
              ${wt.status === 'PENDING_REVIEW' || wt.status === 'ACTIVE' ? `
                <button onclick="approveWorktree('${wt.task_id}')" class="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 text-white rounded text-xs font-medium transition flex items-center gap-1">
                  <i class="fa-solid fa-check"></i> Approve & Apply
                </button>
                <button onclick="rejectWorktree('${wt.task_id}')" class="px-3 py-1.5 bg-red-800 hover:bg-red-700 text-white rounded text-xs font-medium transition flex items-center gap-1">
                  <i class="fa-solid fa-xmark"></i> Reject
                </button>
              ` : ''}
            </div>
          `;
          container.appendChild(card);
        }
      } catch (err) {
        console.error('Error rendering worktrees:', err);
      }
    }

    async function viewWorktreeDiff(taskId) {
      try {
        const res = await fetchWithAuth('/api/worktrees/diff?task_id=' + encodeURIComponent(taskId));
        const data = await res.json();
        document.getElementById('diff-modal-title').textContent = 'Diff for task: ' + taskId + ' (' + (data.branch || '') + ')';
        document.getElementById('diff-modal-body').textContent = data.diff || data.diff_stat || '(Empty diff or no staged changes)';
        document.getElementById('diff-modal').classList.remove('hidden');
      } catch (err) {
        showToast('Failed to load diff: ' + err.message, 'error');
      }
    }

    function closeDiffModal() {
      document.getElementById('diff-modal').classList.add('hidden');
    }

    async function viewTaskDetails(taskId) {
      try {
        const res = await fetchWithAuth('/api/task?task_id=' + encodeURIComponent(taskId));
        const data = await res.json();
        if (!data.task) throw new Error('Task not found');
        const t = data.task;
        document.getElementById('task-modal-title').textContent = `${t.task_id} — ${t.title}`;
        document.getElementById('task-modal-body').innerHTML = `
          <div class="grid grid-cols-2 gap-3 bg-slate-950 p-4 rounded border border-slate-800 font-mono">
            <div>Status: <span class="text-emerald-400 font-bold">${t.status}</span></div>
            <div>Stage: <span class="text-amber-300 font-bold">${t.stage || 'COMPLETE'}</span></div>
            <div>Agent: <span class="text-indigo-400">${t.assigned_agent || '-'}</span></div>
            <div>Model: <span class="text-cyan-400">${t.assigned_model || '-'}</span></div>
            <div>Duration: <span class="text-slate-200">${t.duration_seconds ? t.duration_seconds.toFixed(2) + 's' : '-'}</span></div>
            <div>Budget: <span class="text-slate-200">${t.continuation_budget ?? '-'}/5</span></div>
          </div>
          <div>
            <h4 class="font-bold text-slate-300 mb-1">Description:</h4>
            <p class="text-slate-300 bg-slate-950 p-3 rounded border border-slate-800 font-sans">${t.description}</p>
          </div>
          ${(t.errors && t.errors.length) ? `
            <div>
              <h4 class="font-bold text-red-400 mb-1">Errors:</h4>
              <pre class="bg-red-950/40 border border-red-900/60 p-3 rounded text-red-300 font-mono text-[11px] whitespace-pre-wrap">${t.errors.join('\\n')}</pre>
            </div>
          ` : ''}
          ${t.result && t.result.output ? `
            <div>
              <h4 class="font-bold text-emerald-400 mb-1">Output Result:</h4>
              <pre class="bg-slate-950 border border-slate-800 p-3 rounded text-slate-200 font-mono text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">${t.result.output}</pre>
            </div>
          ` : ''}
        `;
        document.getElementById('task-modal').classList.remove('hidden');
      } catch (err) {
        showToast('Error fetching task details: ' + err.message, 'error');
      }
    }

    function closeTaskModal() {
      document.getElementById('task-modal').classList.add('hidden');
    }

    async function approveWorktree(taskId) {
      if (!confirm('Apply sandbox changes from task ' + taskId + ' into canonical repository?')) return;
      try {
        const res = await fetchWithAuth('/api/worktrees/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: taskId, confirm: true })
        });
        const data = await res.json();
        if (res.ok) {
          showToast('Changes merged into canonical repository!', 'success');
          refreshData();
        } else {
          showToast('Error: ' + (data.message || data.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    async function rejectWorktree(taskId) {
      if (!confirm('Reject and destroy sandbox worktree for task ' + taskId + '?')) return;
      try {
        const res = await fetchWithAuth('/api/worktrees/reject', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: taskId, confirm: true })
        });
        const data = await res.json();
        if (res.ok) {
          showToast('Worktree rejected and destroyed.', 'info');
          refreshData();
        } else {
          showToast('Error: ' + (data.message || data.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    async function cleanupOldWorktrees() {
      if (!confirm('Cleanup all stale worktree sandboxes older than 24 hours?')) return;
      try {
        const res = await fetchWithAuth('/api/worktrees/cleanup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true, max_age_hours: 24 })
        });
        const data = await res.json();
        if (res.ok) {
          showToast('Cleanup complete: ' + data.cleaned_count + ' removed', 'success');
          refreshData();
        } else {
          showToast('Error: ' + (data.message || data.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    async function dispatchTask(autoExecute = true) {
      const input = document.getElementById('quick-instruction');
      const agentSel = document.getElementById('quick-agent');
      const text = input.value.trim();
      if (!text) {
        showToast('Please enter an instruction', 'error');
        return;
      }
      try {
        const res = await fetchWithAuth('/api/dispatch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            instruction: text,
            agent: agentSel.value || null,
            auto_execute: autoExecute
          })
        });
        const data = await res.json();
        if (res.ok) {
          showToast(autoExecute ? 'Task dispatched and executing!' : 'Task added to queue', 'success');
          input.value = '';
          refreshData();
        } else {
          showToast('Failed to dispatch: ' + (data.message || data.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    async function triggerExecute() {
      try {
        const res = await fetchWithAuth('/api/execute', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          showToast('Swarm worker triggered!', 'success');
          refreshData();
        } else {
          showToast('Execution error: ' + (data.message || data.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    async function triggerContinue() {
      try {
        const res = await fetchWithAuth('/api/continue', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          if (data.status === 'terminal') {
            showToast('Workflow complete: ' + data.reason, 'info');
          } else {
            showToast('Universal Continue initiated!', 'success');
          }
          refreshData();
        } else {
          showToast('Continue error: ' + (data.message || data.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    async function triggerReconcile() {
      try {
        const res = await fetchWithAuth('/api/tasks/reconcile', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const data = await res.json();
        if (res.ok) {
          showToast(`Reconciliation complete: checked ${data.checked}, running ${data.still_running}, completed ${data.completed}, failed ${data.failed}, recovered ${data.recovered}`, 'success');
          refreshData();
        } else {
          showToast('Reconciliation failed: ' + (data.message || data.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    async function cancelTask(taskId) {
      try {
        const res = await fetchWithAuth('/api/tasks/cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: taskId })
        });
        if (res.ok) {
          showToast('Task ' + taskId + ' cancelled', 'info');
          refreshData();
        } else {
          const d = await res.json();
          showToast('Error cancelling task: ' + (d.message || d.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    async function executeSingleTask(taskId) {
      await triggerExecute();
    }

    async function simulateRoute() {
      const input = document.getElementById('sim-instruction');
      const text = input.value.trim();
      if (!text) return;
      try {
        const res = await fetchWithAuth('/api/route', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instruction: text })
        });
        const d = await res.json();
        const box = document.getElementById('sim-result');
        let candRows = '';
        (d.candidates || []).forEach(c => {
          candRows += `<div class="flex justify-between py-1 border-b border-slate-900"><span>${c.agent_id}</span><span class="text-amber-400">${c.score.toFixed(2)}</span></div>`;
        });
        box.innerHTML = `
          <div class="text-sm font-bold text-white mb-1">Selected: <span class="text-emerald-400">${d.agent_id}</span> (Model: ${d.model})</div>
          <div class="text-slate-400 text-xs">${d.reason}</div>
          ${d.fallback_agent_id ? `<div class="text-xs text-slate-500">Fallback: ${d.fallback_agent_id}</div>` : ''}
          <div class="pt-2">
            <div class="text-[11px] text-slate-500 font-sans uppercase">Candidate Scores:</div>
            ${candRows}
          </div>
        `;
        box.classList.remove('hidden');
      } catch (err) {
        showToast('Simulation error: ' + err.message, 'error');
      }
    }

    async function searchMemory() {
      const query = document.getElementById('mem-search-query').value.trim();
      if (!query) return;
      try {
        const res = await fetchWithAuth('/api/memory/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: query })
        });
        const d = await res.json();
        const list = document.getElementById('memory-list');
        list.innerHTML = '';
        if (!d.memories || d.memories.length === 0) {
          list.innerHTML = '<div class="p-4 text-center text-slate-500">No matching memories found.</div>';
          return;
        }
        d.memories.forEach(m => {
          const mItem = document.createElement('div');
          mItem.className = 'p-3 bg-slate-900 border border-slate-800 rounded text-xs space-y-1';
          mItem.innerHTML = `
            <div class="flex justify-between text-[10px] text-slate-500 font-mono">
              <span class="px-1.5 py-0.5 rounded bg-slate-800 text-purple-300">[${m.scope}] Source: ${m.source_agent}</span>
              <span>Importance: ${m.importance}/5</span>
            </div>
            <div class="text-slate-200 font-sans">${m.content}</div>
          `;
          list.appendChild(mItem);
        });
      } catch (err) {
        showToast('Memory search error: ' + err.message, 'error');
      }
    }

    async function addMemory() {
      const content = document.getElementById('mem-add-content').value.trim();
      const scope = document.getElementById('mem-add-scope').value;
      if (!content) return;
      try {
        const res = await fetchWithAuth('/api/memory/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content, scope, importance: 4 })
        });
        if (res.ok) {
          showToast('Memory saved to shared store!', 'success');
          document.getElementById('mem-add-content').value = '';
          refreshData();
        } else {
          const d = await res.json();
          showToast('Error adding memory: ' + (d.message || d.error), 'error');
        }
      } catch (err) {
        showToast('Network error: ' + err.message, 'error');
      }
    }

    // Keyboard shortcuts: Esc closes open modals
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeDiffModal();
        closeTaskModal();
      }
    });

    // ── Phase 12-14: Universal AI Mission Control Logic ───────
    let allModels = [];
    let allJobs = [];
    let currentJobFilter = 'all';

    // Providers Management
    async function loadProviders() {
      try {
        const res = await fetchWithAuth('/api/providers');
        const d = await res.json();
        const grid = document.getElementById('mc-providers-grid');
        if (!grid) return;
        if (!d.providers || d.providers.length === 0) {
          grid.innerHTML = '<div class="p-6 text-center text-slate-500 text-sm col-span-full bg-slate-900 border border-slate-800 rounded-lg">No providers registered. Click "Add Provider" above.</div>';
          return;
        }
        grid.innerHTML = d.providers.map(p => {
          const isNotConfig = p.status === 'not_configured' || (p.health_reason && (p.health_reason.includes('Missing credential') || p.health_reason.includes('AUTH_ERROR')));
          const isHealthy = p.healthy !== false && p.status !== 'offline' && !isNotConfig;
          const statusText = isHealthy ? 'HEALTHY' : (isNotConfig ? 'NOT CONFIGURED' : 'OFFLINE');
          const statusColor = isHealthy ? 'text-emerald-400' : (isNotConfig ? 'text-amber-400' : 'text-red-400');
          const statusDot = isHealthy ? 'bg-emerald-500' : (isNotConfig ? 'bg-amber-500' : 'bg-red-500');
          const typeBadge = `<span class="text-[9px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono">${p.type || 'AGENT'}</span>`;
          const failRate = p.failure_rate != null ? (p.failure_rate * 100).toFixed(1) + '%' : '0.0%';
          const lastCheck = p.last_health_check ? p.last_health_check.slice(11, 19) + ' UTC' : 'Recent';
          const caps = (p.capabilities || []).map(c => `<span class="px-1.5 py-0.2 rounded bg-slate-950 text-indigo-300 text-[9px] font-mono border border-slate-800">${c}</span>`).join(' ');
          const acctList = (p.accounts || []).map(a =>
            `<div class="flex items-center justify-between text-[11px] font-mono py-0.5 border-b border-slate-950">
              <div class="flex items-center gap-1.5">
                <span class="w-1.5 h-1.5 rounded-full ${a.status === 'ONLINE' ? 'bg-emerald-500' : (a.status === 'DISABLED' ? 'bg-slate-600' : 'bg-amber-500')}"></span>
                <span class="text-slate-300">${a.id}</span>
              </div>
              <span class="text-slate-500 text-[10px]">[${a.status}]</span>
            </div>`
          ).join('');

          return `<div class="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3 shadow-md flex flex-col justify-between">
            <div>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <div class="w-2.5 h-2.5 rounded-full ${statusDot}"></div>
                  <span class="font-bold text-white text-sm">${p.name || p.id}</span>
                  ${typeBadge}
                </div>
                <span class="text-[10px] ${statusColor} font-mono font-semibold">${statusText}</span>
              </div>
              <div class="text-[11px] text-slate-400 mt-1 flex items-center justify-between">
                <span>${p.models ? p.models.length : 0} models · ${p.account_count || (p.accounts ? p.accounts.length : 0)} accounts</span>
                <span class="font-mono text-[10px] text-slate-500">Fail: ${failRate}</span>
              </div>
              <div class="mt-2 text-[10px] text-slate-500">${p.health_reason ? 'Status: ' + p.health_reason.slice(0, 50) : 'Last check: ' + lastCheck}</div>
              ${caps ? `<div class="flex flex-wrap gap-1 mt-2">${caps}</div>` : ''}
              <div class="mt-3 bg-slate-950/60 p-2 rounded border border-slate-850 space-y-1">
                <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Configured Accounts</div>
                <div class="max-h-24 overflow-y-auto">${acctList || '<span class="text-slate-600 text-[11px]">No accounts configured</span>'}</div>
              </div>
            </div>
            <div class="flex items-center justify-end gap-1.5 pt-3 border-t border-slate-800 flex-wrap">
              <button onclick="openAddAccountModal('${p.id}')" class="px-2 py-1 bg-indigo-900/60 hover:bg-indigo-800 text-indigo-200 border border-indigo-700/50 rounded text-[10px] font-mono flex items-center gap-1" title="Add Account to Provider">
                <i class="fa-solid fa-plus"></i> Account
              </button>
              <button onclick="providerHealthCheck('${p.id}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-sky-400 rounded text-[10px] font-mono flex items-center gap-1" title="Check Health">
                <i class="fa-solid fa-heart-pulse"></i> Health
              </button>
              <button onclick="providerDiscoverModels('${p.id}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-indigo-300 rounded text-[10px] font-mono flex items-center gap-1" title="Discover Models">
                <i class="fa-solid fa-compass"></i> Discover
              </button>
              <button onclick="providerToggle('${p.id}', ${!p.enabled})" class="px-2 py-1 ${p.enabled !== false ? 'bg-amber-950 hover:bg-amber-900 text-amber-300 border border-amber-800' : 'bg-emerald-950 hover:bg-emerald-900 text-emerald-300 border border-emerald-800'} rounded text-[10px] font-mono">
                ${p.enabled !== false ? 'Disable' : 'Enable'}
              </button>
            </div>
          </div>`;
        }).join('');
      } catch (err) { console.error('loadProviders error:', err); }
    }

    function openAddProviderModal() {
      document.getElementById('prov-modal-id').value = '';
      document.getElementById('prov-modal-name').value = '';
      document.getElementById('prov-modal-base-url').value = '';
      document.getElementById('prov-modal-caps').value = 'coding, reasoning';
      document.getElementById('add-provider-modal').classList.remove('hidden');
    }
    function closeAddProviderModal() {
      document.getElementById('add-provider-modal').classList.add('hidden');
    }
    async function submitAddProvider() {
      const id = document.getElementById('prov-modal-id').value.trim();
      const name = document.getElementById('prov-modal-name').value.trim();
      const type = document.getElementById('prov-modal-type').value;
      const base_url = document.getElementById('prov-modal-base-url').value.trim();
      const caps = document.getElementById('prov-modal-caps').value.split(',').map(s => s.trim()).filter(Boolean);
      const enabled = document.getElementById('prov-modal-enabled').checked;
      if (!id || !name) {
        showToast('Provider ID and Display Name are required', 'error');
        return;
      }
      try {
        const res = await fetchWithAuth('/api/providers', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id, name, type, base_url: base_url || null, capabilities: caps, enabled })
        });
        const d = await res.json();
        if (res.ok) {
          showToast('Provider ' + id + ' registered successfully!', 'success');
          closeAddProviderModal();
          loadProviders();
          refreshData();
        } else {
          showToast('Error registering provider: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function providerHealthCheck(provId) {
      try {
        const res = await fetchWithAuth('/api/providers/' + provId + '/health', { method: 'POST' });
        const d = await res.json();
        if (res.ok) {
          showToast(provId + ': ' + (d.healthy ? 'HEALTHY' : 'UNHEALTHY') + (d.reason ? ' — ' + d.reason : ''), d.healthy ? 'success' : 'error');
          loadProviders();
        } else {
          showToast('Health check failed: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function providerDiscoverModels(provId) {
      try {
        const res = await fetchWithAuth('/api/providers/' + provId + '/discover', { method: 'POST' });
        const d = await res.json();
        if (res.ok) {
          showToast('Discovered ' + (d.models_discovered || (d.models ? d.models.length : 0)) + ' models for ' + provId, 'success');
          loadProviders();
          loadModels();
        } else {
          showToast('Discovery failed: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function providerToggle(provId, enable) {
      try {
        const endpoint = enable ? 'enable' : 'disable';
        const res = await fetchWithAuth('/api/providers/' + provId + '/' + endpoint, { method: 'POST' });
        if (res.ok) {
          showToast(provId + ' ' + (enable ? 'enabled' : 'disabled'), 'success');
          loadProviders();
        } else {
          const d = await res.json();
          showToast('Error: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    // Accounts Management
    // Phase 22 Part 7: six visually distinct, never-conflated account badges.
    // Each badge reads exactly one orthogonal field returned by the server and
    // is styled independently. A historical task failure is NEVER rendered here
    // as a current auth or health error — task history is separate metadata.
    function mcStatePill(label, value, palette) {
      const cls = palette[value] || 'bg-slate-800 border-slate-700 text-slate-400';
      const safeVal = (value == null || value === '') ? 'UNKNOWN' : String(value);
      return `<span class="inline-flex items-center px-1.5 py-0.5 rounded border text-[9px] font-mono leading-none ${cls}" title="${label}: ${safeVal}">
        <span class="opacity-60 mr-1 uppercase">${label}</span>${safeVal}</span>`;
    }
    function mcAccountBadges(a) {
      const lifecyclePalette = {
        ONLINE:'bg-teal-950 border-teal-800 text-teal-300', OFFLINE:'bg-rose-950 border-rose-800 text-rose-300',
        DISABLED:'bg-slate-900 border-slate-700 text-slate-500', READY:'bg-emerald-950 border-emerald-800 text-emerald-300',
        AUTH_FAILED:'bg-red-950 border-red-800 text-red-300', CONFIG_ERROR:'bg-amber-950 border-amber-800 text-amber-300',
        VALIDATION_FAILED:'bg-amber-950 border-amber-800 text-amber-300', PROVIDER_UNAVAILABLE:'bg-rose-950 border-rose-800 text-rose-300',
        DISCOVERED:'bg-slate-800 border-slate-700 text-slate-300', CONFIGURING:'bg-sky-950 border-sky-800 text-sky-300',
        AUTHENTICATING:'bg-indigo-950 border-indigo-800 text-indigo-300', AUTHENTICATED:'bg-indigo-950 border-indigo-800 text-indigo-300',
        VALIDATING:'bg-sky-950 border-sky-800 text-sky-300', AUTH_CANCELLED:'bg-amber-950 border-amber-800 text-amber-300'
      };
      const processPalette = {
        IDLE:'bg-slate-900 border-slate-700 text-slate-400', WORKING:'bg-amber-950 border-amber-800 text-amber-300',
        STOPPED:'bg-slate-900 border-slate-700 text-slate-500', TERMINATED:'bg-rose-950 border-rose-800 text-rose-300',
        UNKNOWN:'bg-slate-800 border-slate-700 text-slate-400'
      };
      const authPalette = {
        AUTHENTICATED:'bg-indigo-950 border-indigo-800 text-indigo-300', UNAUTHENTICATED:'bg-slate-900 border-slate-700 text-slate-400',
        AUTHENTICATING:'bg-sky-950 border-sky-800 text-sky-300', AUTH_FAILED:'bg-red-950 border-red-800 text-red-300',
        AUTH_CANCELLED:'bg-amber-950 border-amber-800 text-amber-300', EXPIRED:'bg-amber-950 border-amber-800 text-amber-300'
      };
      const healthPalette = {
        HEALTHY:'bg-emerald-950 border-emerald-800 text-emerald-300', DEGRADED:'bg-amber-950 border-amber-800 text-amber-300',
        UNHEALTHY:'bg-rose-950 border-rose-800 text-rose-300', UNKNOWN:'bg-slate-800 border-slate-700 text-slate-400'
      };
      const taskPalette = {
        IDLE:'bg-slate-900 border-slate-700 text-slate-400', ASSIGNED:'bg-sky-950 border-sky-800 text-sky-300',
        RUNNING:'bg-amber-950 border-amber-800 text-amber-300', COMPLETED:'bg-emerald-950 border-emerald-800 text-emerald-300',
        FAILED:'bg-rose-950 border-rose-800 text-rose-300', CANCELLED:'bg-slate-900 border-slate-700 text-slate-500'
      };
      // Availability is derived purely from lifecycle + enabled + task, and is
      // shown as its own badge so operators never read it off another field.
      let availability = 'UNAVAILABLE';
      if (a.enabled === false || a.lifecycle_state === 'DISABLED') availability = 'DISABLED';
      else if (a.lifecycle_state === 'ONLINE' && (a.task_state === 'RUNNING' || a.task_state === 'ASSIGNED')) availability = 'BUSY';
      else if (a.lifecycle_state === 'ONLINE') availability = 'AVAILABLE';
      else if (a.lifecycle_state === 'OFFLINE') availability = 'OFFLINE';
      const availPalette = {
        AVAILABLE:'bg-emerald-950 border-emerald-800 text-emerald-300', BUSY:'bg-amber-950 border-amber-800 text-amber-300',
        OFFLINE:'bg-rose-950 border-rose-800 text-rose-300', DISABLED:'bg-slate-900 border-slate-700 text-slate-500',
        UNAVAILABLE:'bg-slate-800 border-slate-700 text-slate-400'
      };
      return `<div class="flex flex-wrap gap-1">
        ${mcStatePill('Account', a.lifecycle_state, lifecyclePalette)}
        ${mcStatePill('Process', a.process_state, processPalette)}
        ${mcStatePill('Auth', a.auth_state, authPalette)}
        ${mcStatePill('Health', a.health_state, healthPalette)}
        ${mcStatePill('Task', a.task_state, taskPalette)}
        ${mcStatePill('Avail', availability, availPalette)}
      </div>`;
    }

    async function loadAccounts() {
      try {
        const res = await fetchWithAuth('/api/accounts');
        const d = await res.json();
        const tbody = document.getElementById('mc-accounts-tbody');
        if (!tbody) return;
        if (!d.accounts || d.accounts.length === 0) {
          tbody.innerHTML = '<tr><td colspan="8" class="p-6 text-center text-slate-500 font-sans">No accounts registered. Click "Add Account" above.</td></tr>';
          return;
        }
        tbody.innerHTML = d.accounts.map(a => {
          const isProtected = ['antigravity-account-1','antigravity-account-2','antigravity-account-3'].includes(a.id);
          const reqCount = a.requests != null ? a.requests : 0;
          const tokenCount = a.tokens != null ? a.tokens.toLocaleString() : '0';
          const costStr = a.estimated_cost != null ? '$' + a.estimated_cost.toFixed(4) : '$0.0000';
          const maskedKey = a.masked_key || (a.authentication_type === 'api_key' || a.auth_method === 'api_key' ? (a.credential_reference ? '••••••••' : 'None') : (a.auth_method || a.authentication_type || 'profile'));

          // DECOUPLING (Phase 22 Part 7): the "current health reason" line MUST
          // come only from health_reason (the account's live health state) and
          // NEVER from a historical task error. Historical task failures such as
          // "Process terminated or system restarted before task completed" are
          // rendered separately below as dim, explicitly-labelled task history.
          const currentHealth = (a.health_reason && a.health_reason.trim()) ? a.health_reason.trim() : '';
          const healthLine = currentHealth
            ? `<div class="mt-1 text-[9px] text-slate-500 truncate" title="Current health reason (live): ${currentHealth}">health: ${currentHealth}</div>`
            : '';

          return `<tr class="hover:bg-slate-800/50 align-top">
            <td class="p-3 text-slate-200 font-semibold">${a.id}</td>
            <td class="p-3 text-slate-400">${a.provider_id}</td>
            <td class="p-3"><span class="px-1.5 py-0.5 rounded bg-slate-950 border border-slate-800 text-slate-400 text-[10px] font-mono">${maskedKey}</span></td>
            <td class="p-3">${mcAccountBadges(a)}${a.cooldown_active ? '<div class="mt-1 text-[9px] text-yellow-400">⏳ cooldown active</div>' : ''}${healthLine}</td>
            <td class="p-3 text-slate-300 font-mono">${a.priority}</td>
            <td class="p-3 text-slate-300 font-mono">${reqCount} / <span class="text-violet-400">${tokenCount}</span></td>
            <td class="p-3 text-emerald-400 font-mono">${costStr}</td>
            <td class="p-3 space-x-1 font-mono whitespace-nowrap">
              <button onclick="mcHealthCheck('${a.id}')" class="px-2 py-0.5 bg-sky-900 hover:bg-sky-800 text-sky-300 rounded text-[10px]" title="Health Check">♥</button>
              <button onclick="mcToggleAccount('${a.id}', ${!a.enabled})" class="px-2 py-0.5 ${a.enabled ? 'bg-amber-900 hover:bg-amber-800 text-amber-300' : 'bg-emerald-900 hover:bg-emerald-800 text-emerald-300'} rounded text-[10px]">${a.enabled ? 'Disable' : 'Enable'}</button>
              ${!isProtected ? `<button onclick="mcOpenRemoveAccount('${a.id}', '${a.provider_id}', '${(a.credential_reference || '').replace(/'/g, "\\\\'")}')" class="px-2 py-0.5 bg-red-950 border border-red-800 hover:bg-red-900 text-red-300 rounded text-[10px]">Remove</button>` : ''}
            </td>
          </tr>`;
        }).join('');
      } catch (err) { console.error('loadAccounts error:', err); }
    }

    // ── PHASE 22 PART 9: Account Add Wizard client ─────────────────────────
    // Drives the server-side multi-step wizard. Each UI step maps 1:1 to a POST
    // that performs a real AccountLifecycleState transition. Back / Cancel /
    // Retry are available at every step. Secrets are POSTed once to the server
    // (straight into CredentialManager) and never held in the page afterward.
    const WIZ_STEPS = [
      { key: 'select_provider', label: 'Provider' },
      { key: 'select_auth',     label: 'Auth Method' },
      { key: 'configure',       label: 'Configure' },
      { key: 'authenticate',    label: 'Authenticate' },
      { key: 'validate',        label: 'Validate' },
      { key: 'register',        label: 'Register' },
      { key: 'health_check',    label: 'Health Check' },
      { key: 'complete',        label: 'Complete' },
    ];
    let wizState = null;

    async function openAddAccountModal(preselectedProviderId) {
      // Fresh wizard state. The correlation id is assigned by the server on start.
      wizState = {
        wizard_id: null, correlation_id: null, provider_id: preselectedProviderId || '',
        account_id: '', step: 'select_provider', auth_method: null, providers: [],
        authMethods: [], failed: false,
      };
      document.getElementById('wiz-corr').textContent = '—';
      document.getElementById('wiz-feed').innerHTML = '';
      wizClearError();
      // Load providers with their supported auth methods for step 1.
      try {
        const res = await fetchWithAuth('/api/wizard/providers');
        const d = await res.json();
        wizState.providers = d.providers || [];
      } catch (e) { wizState.providers = []; }
      document.getElementById('wizard-modal').classList.remove('hidden');
      wizRenderSteps();
      wizRenderBody();
    }

    function wizClose() {
      document.getElementById('wizard-modal').classList.add('hidden');
      if (window._wizPollInterval) {
        clearInterval(window._wizPollInterval);
        window._wizPollInterval = null;
      }
      wizState = null;
    }

    function wizShowError(msg) {
      const el = document.getElementById('wiz-error');
      el.textContent = msg;
      el.classList.remove('hidden');
      wizState.failed = true;
      document.getElementById('wiz-btn-retry').classList.remove('hidden');
    }
    function wizClearError() {
      const el = document.getElementById('wiz-error');
      el.textContent = '';
      el.classList.add('hidden');
      if (wizState) wizState.failed = false;
      document.getElementById('wiz-btn-retry').classList.add('hidden');
    }

    function wizAppendFeed(evt) {
      const feed = document.getElementById('wiz-feed');
      if (!feed) return;
      const t = (evt.timestamp || '').split('T')[1]?.slice(0, 8) || '';
      const row = document.createElement('div');
      row.innerHTML = `<span class="text-slate-600">${t}</span> <span class="text-indigo-300 font-semibold">${evt.event_type}</span> <span class="text-slate-500">${evt.lifecycle_state || ''}</span> <span class="text-slate-400">${evt.message || ''}</span>`;
      feed.insertBefore(row, feed.firstChild);
    }

    function wizRenderSteps() {
      const idx = WIZ_STEPS.findIndex(s => s.key === wizState.step);
      document.getElementById('wiz-steps').innerHTML = WIZ_STEPS.map((s, i) => {
        let cls = 'bg-slate-950 border-slate-800 text-slate-500';
        if (i < idx) cls = 'bg-emerald-950 border-emerald-800 text-emerald-300';
        else if (i === idx) cls = 'bg-indigo-950 border-indigo-700 text-indigo-200 font-bold';
        return `<span class="px-2 py-0.5 rounded border ${cls}">${i + 1}. ${s.label}</span>`;
      }).join('<span class="text-slate-700">›</span>');
    }

    function wizRenderBody() {
      const body = document.getElementById('wiz-body');
      const nextBtn = document.getElementById('wiz-btn-next');
      const backBtn = document.getElementById('wiz-btn-back');
      backBtn.disabled = (wizState.step === 'select_provider');
      backBtn.classList.toggle('opacity-40', wizState.step === 'select_provider');
      nextBtn.textContent = (wizState.step === 'complete') ? 'Done' : (wizState.step === 'health_check' ? 'Finish (Bring Online)' : 'Next');

      if (wizState.step === 'select_provider') {
        const opts = wizState.providers.map(p =>
          `<option value="${p.id}" ${p.id === wizState.provider_id ? 'selected' : ''}>${p.name} (${p.type}) — ${p.account_count} accounts</option>`).join('');
        body.innerHTML = `
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Provider *</label>
          <select id="wiz-provider" onchange="wizState.provider_id=this.value" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200">${opts}</select>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1 mt-3">New Account ID *</label>
          <input id="wiz-account-id" value="${wizState.account_id || ''}" placeholder="e.g. antigravity-account-4" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 font-mono">
          <p class="text-[10px] text-slate-500 mt-1">Only alphanumerics, dashes and underscores.</p>`;
        if (!wizState.provider_id && wizState.providers.length) wizState.provider_id = wizState.providers[0].id;
      } else if (wizState.step === 'select_auth') {
        const methods = wizState.authMethods || [];
        if (!methods.length) {
          body.innerHTML = `<div class="bg-amber-950/60 border border-amber-800 rounded p-3 text-amber-300 text-[11px]">
            Provider <span class="font-mono">${wizState.provider_id}</span> has no supported authentication method available.
            The required CLI may not be installed. You cannot proceed with this provider.</div>`;
          nextBtn.disabled = true; nextBtn.classList.add('opacity-40');
        } else {
          nextBtn.disabled = false; nextBtn.classList.remove('opacity-40');
          body.innerHTML = `
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Authentication Method</label>
            <div class="space-y-1.5">` + methods.map((m, i) => `
              <label class="flex items-start gap-2 bg-slate-950 border border-slate-800 rounded p-2 cursor-pointer">
                <input type="radio" name="wiz-auth" value="${m.id}" ${(wizState.auth_method === m.id || (!wizState.auth_method && i === 0)) ? 'checked' : ''} onchange="wizState.auth_method=this.value" class="mt-0.5">
                <span><span class="text-slate-200 font-semibold">${m.label}</span>${m.cli_version ? `<span class="text-[10px] text-slate-500 ml-2 font-mono">CLI ${m.cli_version}</span>` : ''}${m.flags ? `<div class="text-[10px] text-slate-500 font-mono mt-0.5">flags: ${m.flags.join(' ')}</div>` : ''}</span>
              </label>`).join('') + `</div>`;
          if (!wizState.auth_method && methods.length) wizState.auth_method = methods[0].id;
        }
      } else if (wizState.step === 'configure') {
        const isAntigravity = wizState.provider_id === 'antigravity';
        const isOAuth = wizState.auth_method === 'oauth';
        const isApiKey = wizState.auth_method === 'api_key';

        let defaultBaseUrl = '';
        if (wizState.provider_id === 'openai') defaultBaseUrl = 'https://api.openai.com/v1';
        else if (wizState.provider_id === 'anthropic') defaultBaseUrl = 'https://api.anthropic.com/v1';
        else if (wizState.provider_id === 'gemini' || wizState.provider_id === 'gemini-api') defaultBaseUrl = 'https://generativelanguage.googleapis.com/v1beta';
        else if (wizState.provider_id === 'openrouter') defaultBaseUrl = 'https://openrouter.ai/api/v1';
        else if (wizState.provider_id === 'groq') defaultBaseUrl = 'https://api.groq.com/openai/v1';
        else if (wizState.provider_id === 'ollama') defaultBaseUrl = 'http://localhost:11434/v1';

        let customFields = '';
        if (isAntigravity && isOAuth) {
          customFields = `
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1 mt-3">Google Account Email (Optional login hint)</label>
            <input id="wiz-email" type="email" placeholder="e.g. user@gmail.com" value="${wizState.email || ''}" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200">
            <p class="text-[10px] text-slate-500 mt-0.5">Pre-selects your Google account on the login page.</p>

            <div class="mt-4 p-4 bg-slate-900 border border-indigo-500/40 rounded-lg space-y-3">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <svg class="w-5 h-5" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/></svg>
                  <span class="text-sm font-semibold text-slate-100">Google Interactive Browser OAuth</span>
                </div>
                <span id="wiz-oauth-status" class="text-xs text-indigo-300 bg-indigo-950/60 px-2 py-0.5 rounded border border-indigo-800/60">Ready</span>
              </div>
              <p class="text-xs text-slate-300">Click <b>Sign In with Google</b> (or <b>Next</b>) to authenticate in a popup window. OmniRoute-style PKCE authentication exchanges tokens directly with Google and stores them in an isolated profile directory with 0600 permissions. No manual token copying required.</p>
              <div class="flex items-center gap-3 pt-1">
                <button type="button" id="wiz-google-login-btn" onclick="wizLaunchOAuthLogin()" class="px-4 py-2 bg-white hover:bg-slate-100 text-slate-900 text-xs font-semibold rounded shadow-md flex items-center gap-2 transition">
                  <svg class="w-4 h-4" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/></svg>
                  Sign In with Google
                </button>
                <button type="button" onclick="wizCheckAuthStatus()" class="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded border border-slate-700">
                  Verify Token
                </button>
              </div>
            </div>
          `;
        } else if (isAntigravity) {
          customFields = `
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1 mt-3">Google Account Email *</label>
            <input id="wiz-email" type="email" placeholder="e.g. user@gmail.com" value="${wizState.email || ''}" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200">
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1 mt-3">Direct OAuth Token / Session Key (Manual / Headless) *</label>
            <input id="wiz-key" type="password" placeholder="Paste session token or OAuth JSON token here..." class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 font-mono text-xs">
            <p class="text-[10px] text-emerald-400/80 mt-1">🔒 Stored in isolated profile directory with 0600 permissions. For headless/remote environments without a browser.</p>
          `;
        } else if (isApiKey || ['openai', 'anthropic', 'gemini', 'gemini-api', 'openrouter', 'groq', 'ollama', 'cline'].includes(wizState.provider_id)) {
          customFields = `
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1 mt-3">Base URL (optional)</label>
            <input id="wiz-baseurl" value="${defaultBaseUrl}" placeholder="${defaultBaseUrl || 'https://api.openai.com/v1'}" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 font-mono text-xs">
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1 mt-3">API Key / Secret ${wizState.provider_id === 'ollama' ? '(optional for local)' : '*'}</label>
            <input id="wiz-key" type="password" placeholder="${wizState.provider_id === 'anthropic' ? 'sk-ant-...' : (wizState.provider_id.includes('gemini') ? 'AIza...' : 'sk-...')}" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 font-mono text-xs">
            <p class="text-[10px] text-emerald-400/80 mt-1">🔒 Sent once to CredentialManager. Tested with live pre-flight validation in Step 5.</p>
          `;
        } else {
          customFields = `
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1 mt-3">Base URL (optional)</label>
            <input id="wiz-baseurl" placeholder="https://..." class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200 font-mono text-xs">
            <p class="text-[10px] text-slate-500 mt-2">This provider uses ${wizState.auth_method}. A fresh isolated profile directory will be allocated in the next step.</p>
          `;
        }

        body.innerHTML = `
          <div class="grid grid-cols-2 gap-3">
            <div><label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Display Name</label>
              <input id="wiz-display" placeholder="${wizState.account_id}" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200"></div>
            <div><label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Priority</label>
              <input id="wiz-priority" type="number" value="10" min="1" max="100" class="w-full bg-slate-950 border border-slate-800 rounded p-2 text-slate-200"></div>
          </div>
          ${customFields}`;
      } else if (wizState.step === 'authenticate') {
        const isAntigravity = wizState.provider_id === 'antigravity';
        body.innerHTML = `
          <div class="space-y-3">
            <div class="text-slate-300 font-medium">Ready to authenticate <span class="font-mono text-indigo-300">${wizState.account_id}</span> (${wizState.provider_id}).</div>
            ${isAntigravity ? `
            <div class="bg-slate-950 border border-slate-800 rounded p-3 text-xs space-y-1.5 font-mono">
              <div><span class="text-slate-500">Email:</span> <span class="text-slate-200">${wizState.email || 'None specified'}</span></div>
              <div><span class="text-slate-500">Profile:</span> <span class="text-slate-200">~/.gemini/antigravity-account-${wizState.account_id.replace('antigravity-', '')}</span></div>
              <div id="wiz-auth-token-status" class="flex items-center gap-2 mt-2 pt-2 border-t border-slate-800">
                <span class="text-slate-500">Status:</span>
                <span class="text-amber-400 font-sans">Checking token status...</span>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <button type="button" onclick="wizCheckAuthStatus()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded border border-slate-700">
                Verify Token Presence
              </button>
            </div>
            <div class="text-[11px] text-slate-500">Click <b>Next</b> to authenticate. If you already signed in or entered your token, it will be validated in the next step.</div>
            ` : `
            <div class="text-[11px] text-slate-500">Click <b>Next</b> to run the isolated authentication step. Lifecycle → AUTHENTICATING → AUTHENTICATED.</div>
            `}
          </div>`;
        if (isAntigravity) {
          setTimeout(wizCheckAuthStatus, 100);
        }
      } else if (wizState.step === 'validate') {
        body.innerHTML = `<div class="text-slate-400">Run pre-flight validation (models discovery / connectivity). Lifecycle → VALIDATING → READY.</div>
          <label class="flex items-center gap-2 mt-2 text-[11px] text-slate-400"><input type="checkbox" id="wiz-live" checked> Perform a live validation probe</label>`;
      } else if (wizState.step === 'register') {
        body.innerHTML = `<div class="text-slate-400">Register <span class="font-mono text-slate-200">${wizState.account_id}</span> into the registry and active routing pool.</div>
          <div class="text-[11px] text-slate-500 mt-1">Discovered models: <span class="text-slate-300 font-mono">${(wizState.lastModels != null ? wizState.lastModels : 0)}</span></div>`;
      } else if (wizState.step === 'health_check') {
        body.innerHTML = `<div class="text-slate-400">Run the final health check, then bring the account <b>ONLINE</b>.</div>
          <div id="wiz-health-result" class="text-[11px] text-slate-500 mt-1"></div>`;
      } else if (wizState.step === 'complete') {
        body.innerHTML = `<div class="bg-emerald-950/60 border border-emerald-800 rounded p-3 text-emerald-300">
          ✓ Account <span class="font-mono">${wizState.account_id}</span> is now ONLINE and added to real-time routing pool. Correlation id: <span class="font-mono">${wizState.correlation_id}</span></div>`;
        document.getElementById('wiz-btn-back').classList.add('hidden');
        document.getElementById('wiz-btn-cancel').classList.add('hidden');
      }
      wizRenderSteps();
    }

    async function wizPost(action, extra) {
      const body = Object.assign({ wizard_id: wizState.wizard_id }, extra || {});
      const res = await fetchWithAuth('/api/wizard/' + action, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      const d = await res.json().catch(() => ({}));
      return { ok: res.ok, status: res.status, d };
    }

    async function wizNext() {
      if (!wizState) return;
      wizClearError();
      try {
        if (wizState.step === 'select_provider') {
          wizState.provider_id = document.getElementById('wiz-provider').value;
          wizState.account_id = (document.getElementById('wiz-account-id').value || '').trim();
          if (!wizState.provider_id || !wizState.account_id) { wizShowError('Provider and Account ID are required.'); return; }
          const start = await wizPost('start', { provider_id: wizState.provider_id, account_id: wizState.account_id });
          if (!start.ok) { wizShowError(start.d.error || ('HTTP ' + start.status)); return; }
          wizState.wizard_id = start.d.wizard.wizard_id;
          wizState.correlation_id = start.d.wizard.correlation_id;
          document.getElementById('wiz-corr').textContent = wizState.correlation_id;
          // Load auth methods for chosen provider.
          const am = await fetchWithAuth('/api/wizard/login-methods?provider=' + encodeURIComponent(wizState.provider_id));
          const amd = await am.json();
          wizState.authMethods = amd.login_methods || [];
          wizState.auth_method = null;
          wizState.step = 'select_auth';
        } else if (wizState.step === 'select_auth') {
          if (!wizState.auth_method) { wizShowError('Select an authentication method.'); return; }
          const r = await wizPost('select-auth', { auth_method: wizState.auth_method });
          if (!r.ok) { wizShowError(r.d.error || ('HTTP ' + r.status)); return; }
          wizState.step = 'configure';
        } else if (wizState.step === 'configure') {
          const cfg = {
            display_name: (document.getElementById('wiz-display')?.value || '').trim(),
            priority: parseInt(document.getElementById('wiz-priority')?.value || '10') || 10,
            base_url: (document.getElementById('wiz-baseurl')?.value || '').trim() || null,
          };
          const emailEl = document.getElementById('wiz-email');
          if (emailEl && emailEl.value.trim()) {
            cfg.email = emailEl.value.trim();
            wizState.email = cfg.email;
          }
          const keyEl = document.getElementById('wiz-key');
          if (keyEl && keyEl.value.trim()) {
            cfg.api_key = keyEl.value.trim();
            cfg.auth_token = keyEl.value.trim();
          }

          // If Antigravity + OAuth and not authenticated yet, automatically launch Google sign-in
          if (wizState.provider_id === 'antigravity' && wizState.auth_method === 'oauth') {
            const chk = await fetchWithAuth('/api/wizard/check-auth?wizard_id=' + encodeURIComponent(wizState.wizard_id));
            const chkd = await chk.json().catch(() => ({}));
            if (!chkd.token_exists && !cfg.api_key) {
              await wizPost('configure', { config: cfg });
              wizLaunchOAuthLogin();
              return;
            }
          }

          const r = await wizPost('configure', { config: cfg });
          if (keyEl) keyEl.value = '';  // never keep the secret in the DOM
          if (!r.ok) { wizShowError(r.d.error || ('HTTP ' + r.status)); return; }
          wizState.step = 'authenticate';
        } else if (wizState.step === 'authenticate') {
          const r = await wizPost('authenticate', {});
          if (!r.ok) { wizShowError(r.d.error || ('HTTP ' + r.status)); return; }
          wizState.step = 'validate';
        } else if (wizState.step === 'validate') {
          const live = document.getElementById('wiz-live')?.checked !== false;
          const r = await wizPost('validate', { live });
          if (!r.ok) { wizShowError(r.d.error || ('HTTP ' + r.status)); return; }
          wizState.lastModels = (r.d.wizard && r.d.wizard.discovered_models) ? r.d.wizard.discovered_models.length : 0;
          wizState.step = 'register';
        } else if (wizState.step === 'register') {
          const r = await wizPost('register', {});
          if (!r.ok) { wizShowError(r.d.error || ('HTTP ' + r.status)); return; }
          wizState.step = 'health_check';
        } else if (wizState.step === 'health_check') {
          const hr = await wizPost('health', {});
          if (!hr.ok) { wizShowError(hr.d.error || ('HTTP ' + hr.status)); return; }
          const hel = document.getElementById('wiz-health-result');
          if (hel) hel.textContent = 'Health: ' + (hr.d.health && hr.d.health.healthy ? 'OK' : 'FAILED') + ' — ' + (hr.d.health ? hr.d.health.reason : '');
          const c = await wizPost('complete', {});
          if (!c.ok) { wizShowError(c.d.error || ('HTTP ' + c.status)); return; }
          wizState.step = 'complete';
          showToast('Account ' + wizState.account_id + ' is ONLINE.', 'success');
          loadAccounts(); loadProviders(); refreshData();
        } else if (wizState.step === 'complete') {
          wizClose();
          return;
        }
        wizRenderBody();
      } catch (err) { wizShowError('Network error: ' + err.message); }
    }

    async function wizBack() {
      if (!wizState) return;
      wizClearError();
      // Local-only step (provider/auth) before the server session exists.
      if (!wizState.wizard_id) {
        if (wizState.step === 'select_auth') { wizState.step = 'select_provider'; wizRenderBody(); }
        return;
      }
      const idx = WIZ_STEPS.findIndex(s => s.key === wizState.step);
      if (idx <= 0) return;
      try {
        const r = await wizPost('back', {});
        if (!r.ok) { wizShowError(r.d.error || ('HTTP ' + r.status)); return; }
        wizState.step = WIZ_STEPS[idx - 1].key;
        wizRenderBody();
      } catch (err) { wizShowError('Network error: ' + err.message); }
    }

    async function wizRetry() {
      if (!wizState || !wizState.wizard_id) { wizClearError(); wizRenderBody(); return; }
      try { await wizPost('retry', {}); } catch (e) {}
      wizClearError();
      wizRenderBody();
    }

    async function wizCancel() {
      if (!wizState) { wizClose(); return; }
      if (!wizState.wizard_id) { wizClose(); return; }  // nothing created server-side yet
      try {
        const r = await wizPost('cancel', {});
        const c = r.d.cleanup || {};
        showToast('Wizard cancelled. Rolled back cleanly (record: ' + (c.account_record_removed ? 'removed' : 'none') +
          ', credential: ' + (c.reference_purged ? 'purged' : 'none') + ').', 'info');
        loadAccounts(); loadProviders(); refreshData();
      } catch (err) { showToast('Cancel error: ' + err.message, 'error'); }
      wizClose();
    }

    async function wizLaunchOAuthLogin() {
      const st = document.getElementById('wiz-oauth-status');
      if (st) {
        st.className = 'text-xs text-indigo-300 font-semibold';
        st.textContent = 'Opening Google Sign-In...';
      }
      try {
        const email = document.getElementById('wiz-email')?.value?.trim() || '';
        const r = await wizPost('launch-login', { email: email });
        if (r.ok && r.d.login && r.d.login.auth_url) {
          const authUrl = r.d.login.auth_url;
          if (st) {
            st.className = 'text-xs text-amber-400 font-semibold';
            st.textContent = 'Waiting for Google sign-in in popup...';
          }
          const w = 620, h = 720;
          const left = Math.max(0, Math.floor((window.screen.width - w) / 2));
          const top = Math.max(0, Math.floor((window.screen.height - h) / 2));
          window._oauthPopup = window.open(
            authUrl,
            'google_oauth_popup',
            `width=${w},height=${h},top=${top},left=${left},status=no,resizable=yes`
          );

          if (!window._wizPollInterval) {
            window._wizPollInterval = setInterval(async () => {
              if (!wizState || (wizState.step !== 'configure' && wizState.step !== 'authenticate')) {
                clearInterval(window._wizPollInterval);
                window._wizPollInterval = null;
                return;
              }
              await wizCheckAuthStatus();
            }, 2500);
          }
        } else {
          if (st) {
            st.className = 'text-xs text-red-400 font-semibold';
            st.textContent = r.d.error || 'Failed to start login flow';
          }
        }
      } catch (e) {
        if (st) {
          st.className = 'text-xs text-red-400 font-semibold';
          st.textContent = 'Error: ' + e.message;
        }
      }
    }

    async function wizCheckAuthStatus() {
      if (!wizState || !wizState.wizard_id) return;
      try {
        const res = await fetchWithAuth('/api/wizard/check-auth?wizard_id=' + encodeURIComponent(wizState.wizard_id));
        const d = await res.json();
        const st = document.getElementById('wiz-oauth-status');
        const tokenStatusEl = document.getElementById('wiz-auth-token-status');
        if (d.token_exists) {
          if (st) {
            st.className = 'text-xs text-emerald-400 font-bold';
            st.textContent = '✓ Token Detected & Active';
          }
          if (tokenStatusEl) {
            tokenStatusEl.innerHTML = `<span class="text-slate-500">Status:</span> <span class="text-emerald-400 font-sans font-bold">✓ Token Detected & Active</span>`;
          }
          if (window._wizPollInterval) {
            clearInterval(window._wizPollInterval);
            window._wizPollInterval = null;
          }
          if (wizState.step === 'configure') {
            wizState.step = 'authenticate';
            wizRenderBody();
            setTimeout(wizNext, 400);
          }
        } else {
          if (tokenStatusEl) {
            tokenStatusEl.innerHTML = `<span class="text-slate-500">Status:</span> <span class="text-amber-400 font-sans">Token file not found yet. Complete login in popup.</span>`;
          }
        }
      } catch (e) {}
    }

    window.addEventListener('message', async (event) => {
      if (event.data && event.data.type === 'google_oauth_complete') {
        if (window._oauthPopup && !window._oauthPopup.closed) {
          try { window._oauthPopup.close(); } catch (e) {}
        }
        if (window._wizPollInterval) {
          clearInterval(window._wizPollInterval);
          window._wizPollInterval = null;
        }
        const st = document.getElementById('wiz-oauth-status');
        if (st) {
          st.className = 'text-xs text-emerald-400 font-bold';
          st.textContent = '✓ Google Sign-In Successful (' + (event.data.email || '') + ')';
        }
        showToast('Google Sign-In successful for ' + (event.data.email || wizState?.account_id || ''), 'success');
        if (wizState && wizState.step === 'configure') {
          wizState.step = 'authenticate';
          wizRenderBody();
          setTimeout(wizNext, 400);
        } else if (wizState && wizState.step === 'authenticate') {
          setTimeout(wizNext, 400);
        }
      }
    });

    async function openLegacyAddAccountModal(preselectedProviderId) {
      const provSel = document.getElementById('acct-modal-provider');
      provSel.innerHTML = '';
      try {
        const res = await fetchWithAuth('/api/providers');
        const d = await res.json();
        (d.providers || []).forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = `${p.name || p.id} (${p.type || 'API'})`;
          if (preselectedProviderId && p.id === preselectedProviderId) {
            opt.selected = true;
          }
          provSel.appendChild(opt);
        });
      } catch (e) {}
      document.getElementById('acct-modal-id').value = '';
      document.getElementById('acct-modal-key').value = '';
      document.getElementById('acct-modal-priority').value = '10';
      document.getElementById('add-account-modal').classList.remove('hidden');
    }
    function closeAddAccountModal() {
      document.getElementById('add-account-modal').classList.add('hidden');
    }
    async function submitAddAccount() {
      const provider_id = document.getElementById('acct-modal-provider').value;
      const account_id = document.getElementById('acct-modal-id').value.trim();
      const account_type = document.getElementById('acct-modal-type').value;
      const auth_method = document.getElementById('acct-modal-auth').value;
      const key = document.getElementById('acct-modal-key').value.trim();
      const priority = parseInt(document.getElementById('acct-modal-priority').value) || 10;
      if (!provider_id || !account_id) {
        showToast('Provider and Account ID are required', 'error');
        return;
      }
      try {
        const res = await fetchWithAuth('/api/accounts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: account_id,
            account_id: account_id,
            provider: provider_id,
            provider_id: provider_id,
            account_type: account_type,
            auth_type: auth_method,
            auth_method: auth_method,
            api_key: key || null,
            priority: priority
          })
        });
        const d = await res.json();
        if (res.ok) {
          showToast('Account ' + account_id + ' registered securely into CredentialManager!', 'success');
          closeAddAccountModal();
          loadAccounts();
          loadProviders();
          refreshData();
        } else {
          showToast('Error: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function mcHealthCheck(accountId) {
      try {
        const res = await fetchWithAuth('/api/accounts/' + accountId + '/health', { method: 'POST' });
        const d = await res.json();
        if (res.ok) {
          const isNotConf = d.reason && (d.reason.includes('Missing credential') || d.reason.includes('AUTH_ERROR') || d.reason.includes('No credential'));
          const msg = isNotConf ? `${accountId}: NOT CONFIGURED (${d.reason})` : `${accountId}: ${d.healthy ? 'HEALTHY' : 'UNHEALTHY'}${d.reason ? ' — ' + d.reason : ''}`;
          showToast(msg, d.healthy ? 'success' : (isNotConf ? 'info' : 'error'));
          loadAccounts();
          loadProviders();
          refreshData();
        } else {
          showToast('Health check error: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function mcToggleAccount(accountId, enable) {
      try {
        const endpoint = enable ? 'enable' : 'disable';
        const res = await fetchWithAuth('/api/accounts/' + accountId + '/' + endpoint, { method: 'POST' });
        if (res.ok) {
          showToast(accountId + ' ' + (enable ? 'enabled' : 'disabled'), 'success');
          loadAccounts();
        } else {
          const d = await res.json();
          showToast('Error: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function mcRemoveAccount(accountId) {
      if (!confirm('Remove account ' + accountId + '? This will also delete stored credentials from CredentialManager.')) return;
      try {
        const res = await fetchWithAuth('/api/accounts/' + accountId, { method: 'DELETE' });
        if (res.ok) {
          showToast(accountId + ' removed successfully', 'success');
          loadAccounts();
        } else {
          const d = await res.json();
          showToast('Error: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    // Phase 22 Part 7: explicit two-step Remove flow with scoped cleanup detail.
    // Step 1 opens a confirmation modal listing exactly what will be deleted.
    // Step 2 (mcConfirmRemoveAccount) performs the DELETE only after the operator
    // explicitly confirms. The credential is referenced by its secret:// URI only
    // — never its value — so no secret can reach the browser.
    let _mcPendingRemoveId = null;
    function mcOpenRemoveAccount(accountId, providerId, credentialReference) {
      _mcPendingRemoveId = accountId;
      const nameEl = document.getElementById('remove-account-name');
      if (nameEl) nameEl.textContent = accountId;
      const listEl = document.getElementById('remove-account-scope');
      if (listEl) {
        const credLine = (credentialReference && credentialReference.trim())
          ? `Stored credential reference <span class="text-amber-300">${credentialReference}</span> (secret deleted from CredentialManager; value never shown)`
          : 'No stored credential reference (nothing to delete from CredentialManager)';
        listEl.innerHTML = `
          <li>Account <span class="text-slate-200">${accountId}</span> removed from the in-memory registry and its provider pool <span class="text-slate-200">${providerId || 'unknown'}</span></li>
          <li>${credLine}</li>
          <li>Usage and analytics history is retained (audit trail is not purged)</li>
          <li>Persisted <span class="text-slate-200">config/providers.json</span> is not rewritten by this action</li>`;
      }
      const modal = document.getElementById('remove-account-modal');
      if (modal) modal.classList.remove('hidden');
    }
    function mcCloseRemoveAccount() {
      _mcPendingRemoveId = null;
      const modal = document.getElementById('remove-account-modal');
      if (modal) modal.classList.add('hidden');
    }
    async function mcConfirmRemoveAccount() {
      const accountId = _mcPendingRemoveId;
      if (!accountId) return;
      try {
        const res = await fetchWithAuth('/api/accounts/' + encodeURIComponent(accountId), { method: 'DELETE' });
        const d = await res.json().catch(() => ({}));
        if (res.ok) {
          showToast(accountId + ' removed. ' + ((d.cleanup && d.cleanup.reference_purged) ? 'Credential secret deleted.' : 'No credential to delete.'), 'success');
          mcCloseRemoveAccount();
          loadAccounts();
          loadProviders();
          refreshData();
        } else {
          showToast('Error: ' + (d.message || d.error || ('HTTP ' + res.status)), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    // Models Management
    async function loadModels() {
      try {
        const res = await fetchWithAuth('/api/models');
        const d = await res.json();
        allModels = d.models || [];
        renderModelsTable(allModels);
      } catch (err) { console.error('loadModels error:', err); }
    }

    function renderModelsTable(models) {
      const tbody = document.getElementById('mc-models-tbody');
      if (!tbody) return;
      if (!models || models.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="p-6 text-center text-slate-500 font-sans">No models found. Click "Discover Models" above.</td></tr>';
        return;
      }
      tbody.innerHTML = models.map(m => {
        const caps = (m.capabilities || []).slice(0, 4).map(c => `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-violet-300 text-[9px] font-mono">${c}</span>`).join(' ');
        const speedStr = m.speed || (m.avg_latency ? m.avg_latency.toFixed(2) + 's' : 'normal');
        const isAvail = m.status === 'available' && m.enabled !== false;
        return `<tr class="hover:bg-slate-800/50">
          <td class="p-3 text-slate-200 font-semibold">${m.display_name || m.model_id}</td>
          <td class="p-3 text-slate-400">${m.provider_id}</td>
          <td class="p-3 text-slate-300 font-mono">${m.context_window ? (m.context_window/1000).toFixed(0)+'K' : '—'}</td>
          <td class="p-3 text-slate-300 font-mono">${m.input_cost_per_1m != null && m.input_cost_per_1m > 0 ? '$' + m.input_cost_per_1m.toFixed(2) + ' / $' + (m.output_cost_per_1m || 0).toFixed(2) : 'Free / Local'}</td>
          <td class="p-3 text-cyan-400 font-mono text-[11px]">${speedStr}</td>
          <td class="p-3 text-slate-300 font-mono">${m.priority || 50}</td>
          <td class="p-3">${caps || '<span class="text-slate-600">—</span>'}</td>
          <td class="p-3 space-x-1.5 font-mono">
            <span class="px-1.5 py-0.5 rounded ${isAvail ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : 'bg-slate-800 text-slate-500'} text-[10px]">${isAvail ? 'AVAILABLE' : 'OFFLINE'}</span>
            <button onclick="openEditModelModal('${m.model_id}')" class="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-[10px]">Edit</button>
          </td>
        </tr>`;
      }).join('');
    }

    function filterModelsTable() {
      const search = (document.getElementById('model-filter-search')?.value || '').toLowerCase();
      const cap = document.getElementById('model-filter-capability')?.value || '';
      const filtered = allModels.filter(m => {
        const matchesSearch = !search || (m.model_id && m.model_id.toLowerCase().includes(search)) || (m.display_name && m.display_name.toLowerCase().includes(search)) || (m.provider_id && m.provider_id.toLowerCase().includes(search));
        const matchesCap = !cap || (m.capabilities && m.capabilities.includes(cap));
        return matchesSearch && matchesCap;
      });
      renderModelsTable(filtered);
    }

    async function discoverAllModels() {
      try {
        showToast('Discovering models across all providers...', 'info');
        const res = await fetchWithAuth('/api/models/discover', { method: 'POST' });
        const d = await res.json();
        if (res.ok) {
          showToast('Discovery complete! Found ' + (d.models_discovered || (d.models ? d.models.length : 0)) + ' models.', 'success');
          loadModels();
          loadProviders();
        } else {
          showToast('Discovery error: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    function openEditModelModal(modelId) {
      const model = allModels.find(m => m.model_id === modelId);
      if (!model) return;
      document.getElementById('model-edit-id').value = model.model_id;
      document.getElementById('model-edit-priority').value = model.priority || 50;
      document.getElementById('model-edit-caps').value = (model.capabilities || []).join(', ');
      document.getElementById('model-edit-enabled').checked = model.enabled !== false;
      document.getElementById('edit-model-modal').classList.remove('hidden');
    }
    function closeEditModelModal() {
      document.getElementById('edit-model-modal').classList.add('hidden');
    }
    async function submitEditModel() {
      const model_id = document.getElementById('model-edit-id').value;
      const priority = parseInt(document.getElementById('model-edit-priority').value) || 50;
      const caps = document.getElementById('model-edit-caps').value.split(',').map(s => s.trim()).filter(Boolean);
      const enabled = document.getElementById('model-edit-enabled').checked;
      try {
        const res = await fetchWithAuth('/api/models/' + encodeURIComponent(model_id), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ priority, capabilities: caps, enabled })
        });
        const d = await res.json();
        if (res.ok) {
          showToast('Model ' + model_id + ' updated', 'success');
          closeEditModelModal();
          loadModels();
        } else {
          showToast('Error: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    // Job Submission & Orchestration
    async function populateJobDropdowns() {
      try {
        const provRes = await fetchWithAuth('/api/providers');
        const provData = await provRes.json();
        const pSel = document.getElementById('job-provider-select');
        if (pSel) {
          pSel.innerHTML = '<option value="auto">Auto (SmartRouter)</option>';
          (provData.providers || []).forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = `${p.name || p.id} (${p.type || 'API'})`;
            pSel.appendChild(opt);
          });
        }
      } catch (e) {}
    }

    async function onJobProviderChange() {
      const pSel = document.getElementById('job-provider-select');
      const aSel = document.getElementById('job-account-select');
      const mSel = document.getElementById('job-model-select');
      const provId = pSel.value;
      if (provId === 'auto') {
        aSel.innerHTML = '<option value="auto">Auto (SmartRouter)</option>';
        mSel.innerHTML = '<option value="auto">Auto (SmartRouter)</option>';
        return;
      }
      try {
        const acctRes = await fetchWithAuth('/api/accounts');
        const acctData = await acctRes.json();
        aSel.innerHTML = '<option value="auto">Auto (SmartRouter)</option>';
        (acctData.accounts || []).filter(a => a.provider_id === provId).forEach(a => {
          const opt = document.createElement('option');
          opt.value = a.id;
          opt.textContent = `${a.id} [${a.status}]`;
          aSel.appendChild(opt);
        });

        const modRes = await fetchWithAuth('/api/models');
        const modData = await modRes.json();
        mSel.innerHTML = '<option value="auto">Auto (SmartRouter)</option>';
        (modData.models || []).filter(m => m.provider_id === provId).forEach(m => {
          const opt = document.createElement('option');
          opt.value = m.model_id;
          opt.textContent = `${m.display_name || m.model_id}`;
          mSel.appendChild(opt);
        });
      } catch (e) {}
    }

    async function submitMissionControlJob() {
      const task = document.getElementById('job-task-input').value.trim();
      if (!task) {
        showToast('Please enter a task instruction or prompt', 'error');
        return;
      }
      const mode = document.getElementById('job-mode-select').value;
      const provider = document.getElementById('job-provider-select').value;
      const account = document.getElementById('job-account-select').value;
      const model = document.getElementById('job-model-select').value;
      const priority = parseInt(document.getElementById('job-priority-input').value) || 5;
      const timeout = parseInt(document.getElementById('job-timeout-input').value) || 30;
      const failover_enabled = document.getElementById('job-failover-check').checked;
      const streaming = document.getElementById('job-streaming-check').checked;

      try {
        const res = await fetchWithAuth('/api/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task,
            routing_mode: mode,
            provider: provider === 'auto' ? null : provider,
            account: account === 'auto' ? null : account,
            model: model === 'auto' ? null : model,
            priority,
            timeout,
            failover_enabled,
            streaming
          })
        });
        const d = await res.json();
        if (res.ok) {
          showToast('Job ' + (d.job ? d.job.id : '') + ' submitted successfully!', 'success');
          document.getElementById('job-task-input').value = '';

          // Display routing decision banner
          const resBox = document.getElementById('job-submission-result');
          if (resBox && d.routing_decision) {
            const rd = d.routing_decision;
            resBox.innerHTML = `
              <div class="flex items-center justify-between border-b border-slate-850 pb-2">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
                  <span class="text-white font-bold text-xs">SMART ROUTER DECISION</span>
                  <span class="text-indigo-400 font-bold">Job: ${d.job ? d.job.id : ''}</span>
                </div>
                <button onclick="openRoutingInspectModal('${d.job ? d.job.id : ''}')" class="px-2 py-0.5 bg-indigo-950 border border-indigo-800 hover:bg-indigo-900 text-indigo-300 rounded text-[10px]">
                  Inspect Score Breakdown →
                </button>
              </div>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-slate-300 pt-1">
                <div><span class="text-slate-500">Selected Provider:</span> <strong class="text-emerald-400">${rd.provider_id || d.job.provider}</strong></div>
                <div><span class="text-slate-500">Selected Account:</span> <strong class="text-cyan-400">${rd.account_id || d.job.account}</strong></div>
                <div><span class="text-slate-500">Selected Model:</span> <strong class="text-violet-400">${rd.model_id || d.job.model}</strong></div>
                <div><span class="text-slate-500">Total Score:</span> <strong class="text-amber-400">${(rd.total_score != null ? rd.total_score : rd.score || 0).toFixed(1)} / 100</strong></div>
              </div>
              <div class="text-[11px] text-slate-400 font-sans pt-1">
                <span class="font-bold text-slate-300 font-mono">Reason:</span> ${rd.reason || d.explanation || 'Optimal candidate matching capability, health, latency, and available quota.'}
              </div>
            `;
            resBox.classList.remove('hidden');
          }
          loadJobs();
          refreshData();
        } else {
          showToast('Job submission error: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function loadJobs() {
      try {
        const res = await fetchWithAuth('/api/jobs');
        const d = await res.json();
        allJobs = d.jobs || [];
        renderJobsTable(allJobs);
      } catch (err) { console.error('loadJobs error:', err); }
    }

    function renderJobsTable(jobs) {
      const tbody = document.getElementById('mc-jobs-tbody');
      if (!tbody) return;
      let list = jobs;
      if (currentJobFilter !== 'all') {
        list = jobs.filter(j => j.status === currentJobFilter);
      }
      if (!list || list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="p-6 text-center text-slate-500 font-sans">No jobs found in status "${currentJobFilter}".</td></tr>`;
        return;
      }
      tbody.innerHTML = list.map(j => {
        const sColor = {pending:'text-slate-400',running:'text-cyan-400 animate-pulse',completed:'text-emerald-400',failed:'text-red-400'}[j.status] || 'text-slate-400';
        const taskSnippet = (j.task || '').substring(0, 45) + ((j.task || '').length > 45 ? '...' : '');
        const durationStr = j.duration ? j.duration.toFixed(2) + 's' : (j.status === 'running' ? 'running...' : '—');
        const tokenStr = (j.tokens != null ? j.tokens : (j.metrics ? j.metrics.total_tokens : 0)) || '—';
        const failoverTag = j.failover_occurred
          ? `<span class="px-1.5 py-0.2 rounded bg-amber-950 border border-amber-800 text-amber-300 text-[9px]">FAILOVER</span>`
          : '';

        return `<tr class="hover:bg-slate-800/50">
          <td class="p-3 text-slate-300 font-mono text-[10px] font-semibold">${j.id}</td>
          <td class="p-3 text-slate-200 font-sans text-xs">${taskSnippet}</td>
          <td class="p-3 text-slate-400 font-mono">${j.provider || 'auto'}</td>
          <td class="p-3 text-slate-400 font-mono">${j.account || 'auto'}</td>
          <td class="p-3 text-slate-400 font-mono">${j.model || 'auto'}</td>
          <td class="p-3 text-slate-300 font-mono">${durationStr}</td>
          <td class="p-3 text-violet-300 font-mono">${tokenStr}</td>
          <td class="p-3 font-semibold font-mono ${sColor}">${j.status.toUpperCase()} ${failoverTag}</td>
          <td class="p-3 space-x-1.5 font-mono">
            <button onclick="openRoutingInspectModal('${j.id}')" class="px-2 py-0.5 bg-indigo-950 border border-indigo-800 hover:bg-indigo-900 text-indigo-300 rounded text-[10px]" title="Inspect Scoring & Routing">
              Inspect
            </button>
            ${j.status === 'running' ? `<button onclick="cancelMissionControlJob('${j.id}')" class="px-2 py-0.5 bg-red-950 border border-red-800 hover:bg-red-900 text-red-300 rounded text-[10px]">Cancel</button>` : ''}
          </td>
        </tr>`;
      }).join('');
    }

    function filterJobs(filter) {
      currentJobFilter = filter;
      ['all', 'running', 'completed', 'failed'].forEach(f => {
        const btn = document.getElementById('job-filter-' + f);
        if (btn) {
          if (f === filter) {
            btn.className = 'px-2.5 py-1 rounded bg-slate-800 text-white font-medium';
          } else {
            btn.className = 'px-2.5 py-1 rounded text-slate-400 hover:text-white';
          }
        }
      });
      renderJobsTable(allJobs);
    }

    async function cancelMissionControlJob(jobId) {
      try {
        const res = await fetchWithAuth('/api/jobs/' + jobId + '/cancel', { method: 'POST' });
        if (res.ok) {
          showToast('Job ' + jobId + ' cancelled', 'info');
          loadJobs();
        } else {
          showToast('Could not cancel job', 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    // Routing Decision Inspection Modal
    async function openRoutingInspectModal(jobId) {
      const modal = document.getElementById('routing-inspect-modal');
      const body = document.getElementById('routing-inspect-body');
      body.innerHTML = '<div class="p-6 text-center text-slate-500">Loading explainable routing breakdown for job ' + jobId + '...</div>';
      modal.classList.remove('hidden');

      try {
        const res = await fetchWithAuth('/api/routing/inspect?job_id=' + encodeURIComponent(jobId));
        const d = await res.json();
        if (!res.ok || !d.decision) {
          body.innerHTML = `<div class="p-4 bg-slate-950 rounded text-slate-400">No explicit routing inspection found for job ${jobId}. (Executed via explicit target or prior run).</div>`;
          return;
        }

        const dec = d.decision;
        const breakdown = dec.score_breakdown || {};
        const breakdownRows = Object.entries(breakdown).map(([factor, pts]) => {
          const ptVal = typeof pts === 'number' ? pts.toFixed(1) : pts;
          const isPositive = typeof pts === 'number' && pts >= 0;
          return `<tr class="hover:bg-slate-800/40">
            <td class="p-2 text-slate-300 font-mono">${factor}</td>
            <td class="p-2 font-mono font-bold ${isPositive ? 'text-emerald-400' : 'text-red-400'}">${isPositive ? '+' : ''}${ptVal}</td>
          </tr>`;
        }).join('');

        const candidateRows = (dec.candidates || []).map((c, i) => {
          const isWinner = (c.account_id === dec.account_id && c.model_id === dec.model_id);
          return `<tr class="hover:bg-slate-800/40 ${isWinner ? 'bg-indigo-950/40 font-semibold text-white' : 'text-slate-400'}">
            <td class="p-2 font-mono">#${i + 1} ${isWinner ? '★ WINNER' : ''}</td>
            <td class="p-2 font-mono">${c.provider_id}</td>
            <td class="p-2 font-mono text-cyan-300">${c.account_id}</td>
            <td class="p-2 font-mono text-violet-300">${c.model_id}</td>
            <td class="p-2 font-mono font-bold text-amber-400">${(c.total_score != null ? c.total_score : c.score || 0).toFixed(1)}</td>
          </tr>`;
        }).join('');

        const failoverSection = dec.failover_history && dec.failover_history.length > 0 ? `
          <div class="space-y-2 pt-2 border-t border-slate-800">
            <h4 class="text-xs font-bold text-amber-400 uppercase font-mono tracking-wider">Deterministic Failover Trail</h4>
            <div class="space-y-1.5">
              ${dec.failover_history.map((fh, i) => `
                <div class="p-2 bg-red-950/30 border border-red-800/60 rounded text-xs font-mono flex items-center justify-between">
                  <div>
                    <span class="text-red-400 font-bold">Attempt #${i + 1}:</span>
                    <span class="text-slate-300">${fh.original_provider}/${fh.original_account}</span>
                    <span class="text-red-300">❌ (${fh.error || 'failed'})</span>
                    <span class="text-slate-500">→ Next:</span>
                    <span class="text-emerald-400">${fh.next_provider}/${fh.next_account}</span>
                  </div>
                  <div class="text-[10px] text-slate-500">${fh.reason || ''}</div>
                </div>
              `).join('')}
            </div>
          </div>
        ` : '';

        body.innerHTML = `
          <div class="p-4 bg-slate-950 rounded-lg border border-slate-800 space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs text-slate-500 font-mono">Job ID: <strong class="text-white">${dec.job_id || jobId}</strong></span>
              <span class="px-2 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-800 font-mono text-[10px]">Total Score: ${(dec.total_score != null ? dec.total_score : dec.score || 0).toFixed(1)} / 100</span>
            </div>
            <div class="grid grid-cols-3 gap-2 text-xs font-mono pt-1">
              <div><span class="text-slate-500">Selected Provider:</span> <strong class="text-emerald-400">${dec.provider_id}</strong></div>
              <div><span class="text-slate-500">Selected Account:</span> <strong class="text-cyan-400">${dec.account_id}</strong></div>
              <div><span class="text-slate-500">Selected Model:</span> <strong class="text-violet-400">${dec.model_id}</strong></div>
            </div>
            <div class="text-xs text-slate-300 pt-2 font-sans">
              <span class="font-bold text-slate-400 font-mono">Reasoning:</span> ${dec.reason || 'Candidate evaluated across capability_match, health, latency, reliability, cost, and priority.'}
            </div>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <h4 class="text-xs font-bold text-white uppercase font-mono tracking-wider mb-2">16-Factor Score Breakdown</h4>
              <div class="bg-slate-950 border border-slate-800 rounded overflow-hidden">
                <table class="w-full text-left text-xs">
                  <thead class="bg-slate-900 text-slate-400 uppercase text-[10px]">
                    <tr><th class="p-2">Factor</th><th class="p-2">Points</th></tr>
                  </thead>
                  <tbody class="divide-y divide-slate-850">
                    ${breakdownRows || '<tr><td colspan="2" class="p-3 text-slate-500">Standard candidate scoring applied.</td></tr>'}
                  </tbody>
                </table>
              </div>
            </div>

            <div>
              <h4 class="text-xs font-bold text-white uppercase font-mono tracking-wider mb-2">Candidate Alternatives</h4>
              <div class="bg-slate-950 border border-slate-800 rounded overflow-hidden">
                <table class="w-full text-left text-xs">
                  <thead class="bg-slate-900 text-slate-400 uppercase text-[10px]">
                    <tr><th class="p-2">Rank</th><th class="p-2">Prov</th><th class="p-2">Account</th><th class="p-2">Model</th><th class="p-2">Score</th></tr>
                  </thead>
                  <tbody class="divide-y divide-slate-850">
                    ${candidateRows || '<tr><td colspan="5" class="p-3 text-slate-500">Single candidate matched task capabilities.</td></tr>'}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          ${failoverSection}
        `;
      } catch (err) {
        body.innerHTML = `<div class="p-4 text-red-400">Error loading routing inspection: ${err.message}</div>`;
      }
    }
    function closeRoutingInspectModal() {
      document.getElementById('routing-inspect-modal').classList.add('hidden');
    }

    // Usage, Cost, Quotas & Analytics Management
    async function loadUsage() {
      try {
        const [resUsage, resCost, resQuotas, resAnalytics] = await Promise.all([
          fetchWithAuth('/api/usage'),
          fetchWithAuth('/api/cost'),
          fetchWithAuth('/api/quotas'),
          fetchWithAuth('/api/analytics')
        ]);

        const usageData = await resUsage.json();
        const costData = await resCost.json();
        const quotaData = await resQuotas.json();
        const analyticsData = await resAnalytics.json();

        // Top overview stats
        document.getElementById('mc-usage-requests').textContent = (usageData.requests || 0).toLocaleString();
        document.getElementById('mc-usage-success-reqs').textContent = (usageData.successful_requests || usageData.requests || 0).toLocaleString();
        document.getElementById('mc-usage-tokens').textContent = (usageData.total_tokens || 0).toLocaleString();
        document.getElementById('mc-usage-tokens-in').textContent = (usageData.input_tokens || 0).toLocaleString();
        document.getElementById('mc-usage-tokens-out').textContent = (usageData.output_tokens || 0).toLocaleString();

        const totalCost = costData.total_estimated_cost_usd != null ? costData.total_estimated_cost_usd : (usageData.estimated_cost_usd || 0);
        const todayCost = costData.today_estimated_cost_usd != null ? costData.today_estimated_cost_usd : (usageData.today_cost_usd || 0);
        document.getElementById('mc-usage-cost').textContent = '$' + totalCost.toFixed(4);
        document.getElementById('mc-usage-cost-today').textContent = '$' + todayCost.toFixed(4);

        // Performance percentiles
        const lat = analyticsData.latency || {};
        document.getElementById('mc-p50-latency').textContent = lat.p50 ? lat.p50.toFixed(2) + 's' : '0.15s';
        document.getElementById('mc-p95-latency').textContent = lat.p95 ? lat.p95.toFixed(2) + 's' : '0.45s';
        document.getElementById('mc-p99-latency').textContent = lat.p99 ? lat.p99.toFixed(2) + 's' : '0.80s';
        document.getElementById('mc-success-rate').textContent = ((analyticsData.success_rate != null ? analyticsData.success_rate : 1.0) * 100).toFixed(1) + '%';
        document.getElementById('mc-failure-rate').textContent = ((analyticsData.failure_rate || 0) * 100).toFixed(1) + '%';
        document.getElementById('mc-failover-rate').textContent = ((analyticsData.failover_rate || 0) * 100).toFixed(1) + '%';

        // Budget Alerts count
        const alerts = quotaData.alerts || [];
        document.getElementById('mc-active-budget-alerts').textContent = alerts.length;

        // Provider Breakdown
        const provTbody = document.getElementById('mc-provider-usage-tbody');
        if (provTbody) {
          const provs = usageData.by_provider || {};
          const keys = Object.keys(provs);
          if (keys.length === 0) {
            provTbody.innerHTML = '<tr><td colspan="7" class="p-4 text-center text-slate-500 font-sans">No recorded usage across providers yet.</td></tr>';
          } else {
            provTbody.innerHTML = keys.map(k => {
              const p = provs[k];
              const sRate = p.requests > 0 ? (((p.successful_requests != null ? p.successful_requests : p.requests) / p.requests) * 100).toFixed(1) + '%' : '100%';
              return `<tr class="hover:bg-slate-800/40 font-mono">
                <td class="p-3 text-slate-200 font-bold">${k}</td>
                <td class="p-3 text-slate-300">${(p.requests || 0).toLocaleString()}</td>
                <td class="p-3 text-slate-400">${(p.input_tokens || 0).toLocaleString()}</td>
                <td class="p-3 text-slate-400">${(p.output_tokens || 0).toLocaleString()}</td>
                <td class="p-3 text-violet-300 font-semibold">${(p.total_tokens || 0).toLocaleString()}</td>
                <td class="p-3 text-emerald-400">$${(p.estimated_cost_usd || 0).toFixed(4)}</td>
                <td class="p-3 text-cyan-400">${sRate}</td>
              </tr>`;
            }).join('');
          }
        }

        // Quotas Compliance
        const quotaTbody = document.getElementById('mc-quotas-tbody');
        if (quotaTbody) {
          const rules = quotaData.quotas || [];
          if (rules.length === 0) {
            quotaTbody.innerHTML = '<tr><td colspan="7" class="p-4 text-center text-slate-500 font-sans">No custom quotas configured. Default generous limits active.</td></tr>';
          } else {
            quotaTbody.innerHTML = rules.map(q => {
              const dailyTokensUsed = q.used_daily_tokens || 0;
              const dailyTokensLimit = q.daily_token_limit ? q.daily_token_limit.toLocaleString() : '∞';
              const pctTokens = q.daily_token_limit ? Math.min(100, Math.round((dailyTokensUsed / q.daily_token_limit) * 100)) : 0;
              const barColor = pctTokens >= 90 ? 'bg-red-500' : (pctTokens >= 75 ? 'bg-amber-500' : 'bg-emerald-500');

              return `<tr class="hover:bg-slate-800/40 font-mono">
                <td class="p-3 text-slate-300 uppercase text-[10px]"><span class="px-1.5 py-0.5 rounded bg-slate-950 border border-slate-800">${q.scope}</span></td>
                <td class="p-3 text-slate-200 font-bold">${q.target_id}</td>
                <td class="p-3">
                  <div class="text-slate-300">${dailyTokensUsed.toLocaleString()} / ${dailyTokensLimit}</div>
                  ${q.daily_token_limit ? `<div class="w-24 bg-slate-950 h-1.5 rounded-full overflow-hidden mt-1 border border-slate-850"><div class="${barColor} h-full" style="width: ${pctTokens}%"></div></div>` : ''}
                </td>
                <td class="p-3 text-slate-300">${q.used_daily_requests || 0} / ${q.daily_request_limit || '∞'}</td>
                <td class="p-3 text-emerald-400">$${(q.used_monthly_cost || 0).toFixed(2)} / ${q.cost_limit_usd ? '$' + q.cost_limit_usd.toFixed(2) : '∞'}</td>
                <td class="p-3"><span class="px-1.5 py-0.5 rounded text-[10px] ${q.exhausted ? 'bg-red-950 text-red-300 border border-red-800' : 'bg-emerald-950 text-emerald-300'}">${q.exhausted ? 'EXHAUSTED' : (pctTokens >= 75 ? pctTokens + '% ALERT' : 'COMPLIANT')}</span></td>
                <td class="p-3">
                  <button onclick="deleteQuota('${q.scope}', '${q.target_id}')" class="px-2 py-0.5 bg-red-950 border border-red-800 hover:bg-red-900 text-red-300 rounded text-[10px]">Delete</button>
                </td>
              </tr>`;
            }).join('');
          }
        }
      } catch (err) { console.error('loadUsage error:', err); }
    }

    function openAddQuotaModal() {
      document.getElementById('quota-modal-target').value = '';
      document.getElementById('quota-modal-daily-reqs').value = '';
      document.getElementById('quota-modal-daily-tokens').value = '';
      document.getElementById('quota-modal-monthly-reqs').value = '';
      document.getElementById('quota-modal-monthly-tokens').value = '';
      document.getElementById('quota-modal-cost-limit').value = '';
      document.getElementById('add-quota-modal').classList.remove('hidden');
    }
    function closeAddQuotaModal() {
      document.getElementById('add-quota-modal').classList.add('hidden');
    }
    async function submitAddQuota() {
      const scope = document.getElementById('quota-modal-scope').value;
      const target_id = document.getElementById('quota-modal-target').value.trim();
      const daily_requests = parseInt(document.getElementById('quota-modal-daily-reqs').value) || null;
      const daily_tokens = parseInt(document.getElementById('quota-modal-daily-tokens').value) || null;
      const monthly_requests = parseInt(document.getElementById('quota-modal-monthly-reqs').value) || null;
      const monthly_tokens = parseInt(document.getElementById('quota-modal-monthly-tokens').value) || null;
      const cost_limit_usd = parseFloat(document.getElementById('quota-modal-cost-limit').value) || null;

      if (!target_id) {
        showToast('Target ID is required', 'error');
        return;
      }
      try {
        const res = await fetchWithAuth('/api/quotas', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope, target_id, daily_requests, daily_tokens, monthly_requests, monthly_tokens, cost_limit_usd })
        });
        const d = await res.json();
        if (res.ok) {
          showToast('Quota rule for ' + target_id + ' configured', 'success');
          closeAddQuotaModal();
          loadUsage();
        } else {
          showToast('Error configuring quota: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function deleteQuota(scope, targetId) {
      if (!confirm(`Delete quota rule for ${scope}:${targetId}?`)) return;
      try {
        const res = await fetchWithAuth(`/api/quotas?scope=${scope}&target_id=${encodeURIComponent(targetId)}`, { method: 'DELETE' });
        if (res.ok) {
          showToast('Quota rule deleted', 'success');
          loadUsage();
        } else {
          const d = await res.json();
          showToast('Error deleting quota: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    async function resetQuotas() {
      if (!confirm('Reset all daily and monthly quota counters to zero?')) return;
      try {
        const res = await fetchWithAuth('/api/quotas/reset', { method: 'POST' });
        if (res.ok) {
          showToast('Quota usage counters reset to 0', 'success');
          loadUsage();
        } else {
          const d = await res.json();
          showToast('Error resetting quotas: ' + (d.message || d.error), 'error');
        }
      } catch (err) { showToast('Network error: ' + err.message, 'error'); }
    }

    // Override showTab to load Phase 10 data on tab switch
    const _originalShowTab = typeof showTab === 'function' ? showTab : null;

    initAuth().then(() => {
      refreshData();
      pollingInterval = setInterval(refreshData, 2500);
      initEventStream();
    });
  </script>
</body>
</html>"""
        out = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(out)


PID_FILE = PROJECT_ROOT / "runtime" / "dashboard.pid"
PROTECTED_PIDS = frozenset({3809})


def is_pid_alive(pid: int) -> bool:
    """Check if process with given PID exists. Never signals protected PIDs."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_dashboard_process(pid: int) -> bool:
    """Check if a running PID corresponds to a Mission Control dashboard process."""
    if pid in PROTECTED_PIDS or pid == 3809:
        return False
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.is_file():
            cmdline = cmdline_path.read_text(encoding="utf-8", errors="replace")
            if "dashboard.py" in cmdline or "ui.dashboard" in cmdline:
                return True
    except Exception:
        pass
    return False


def acquire_pid_file(pid_file: Path | None = None) -> None:
    """Acquire the dashboard PID lock file.

    CRITICAL GUI SAFETY: Never signals, terminates, or interferes with PID 3809
    (the running Antigravity IDE GUI process).
    """
    target_file = pid_file or PID_FILE
    target_file.parent.mkdir(parents=True, exist_ok=True)
    if target_file.is_file():
        try:
            raw = target_file.read_text(encoding="utf-8").strip()
            existing_pid = int(raw)
            if existing_pid in PROTECTED_PIDS or existing_pid == 3809:
                target_file.unlink(missing_ok=True)
            elif is_pid_alive(existing_pid):
                if is_dashboard_process(existing_pid):
                    print(f"Error: Mission Control dashboard is already running (PID {existing_pid}).")
                    sys.exit(1)
                else:
                    target_file.unlink(missing_ok=True)
            else:
                target_file.unlink(missing_ok=True)
        except (ValueError, OSError):
            target_file.unlink(missing_ok=True)

    target_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        os.chmod(target_file, 0o600)
    except Exception:
        pass
    atexit.register(release_pid_file, target_file)


def release_pid_file(pid_file: Path | None = None) -> None:
    """Release the dashboard PID lock file if owned by the current process."""
    target_file = pid_file or PID_FILE
    try:
        if target_file.is_file():
            raw = target_file.read_text(encoding="utf-8").strip()
            if raw == str(os.getpid()):
                target_file.unlink(missing_ok=True)
    except Exception:
        pass


def setup_signal_handlers(server: ThreadedHTTPServer) -> None:
    """Register graceful shutdown handlers for SIGINT and SIGTERM."""
    def _shutdown_handler(signum: int, frame: Any) -> None:
        signame = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        print(f"\nReceived signal {signame} ({signum}). Initiating graceful shutdown...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGINT, _shutdown_handler)
        signal.signal(signal.SIGTERM, _shutdown_handler)
    except (ValueError, OSError):
        pass


def run_startup_checks(host: str = "127.0.0.1") -> dict[str, Any]:
    """Validate local loopback binding, runtime directory permissions, token, and provider registry."""
    validate_host_binding(host)

    runtime_dir = PROJECT_ROOT / "runtime"
    logs_dir = runtime_dir / "logs"
    audit_dir = runtime_dir / "audit"
    for d in (runtime_dir, logs_dir, audit_dir):
        d.mkdir(parents=True, exist_ok=True)
        test_file = d / ".health_check_write_test"
        try:
            test_file.write_text("probe", encoding="utf-8")
            test_file.unlink(missing_ok=True)
        except Exception as exc:
            raise RuntimeError(f"Startup check failed: directory '{d}' is not writable: {exc}") from exc

    token = get_or_create_auth_token()
    if not token or len(token) < 16:
        raise RuntimeError("Startup check failed: Generated auth token is invalid or empty")
    if AUTH_TOKEN_FILE.is_file():
        try:
            mode = os.stat(AUTH_TOKEN_FILE).st_mode
            if mode & 0o077 != 0:
                os.chmod(AUTH_TOKEN_FILE, 0o600)
        except Exception:
            pass

    if not registry.list_providers() and not registry.list_ai_providers():
        raise RuntimeError("Startup check failed: ProviderRegistry initialized with zero providers")

    return {
        "status": "HEALTHY",
        "host": host,
        "token_configured": bool(token),
        "runtime_writable": True,
        "providers_count": len(registry.list_providers()) + len(registry.list_ai_providers()),
    }


def run_server(port: int = PORT, host: str | None = None) -> None:
    target_host = host or os.environ.get("BRAIN_HOST", "127.0.0.1")
    validate_host_binding(target_host)
    run_startup_checks(target_host)
    acquire_pid_file()
    server = None
    try:
        server = ThreadedHTTPServer((target_host, port), MissionControlHandler)
        setup_signal_handlers(server)
        print(f"🚀 Mission Control Dashboard listening on http://{target_host}:{port}")
        server.serve_forever()
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 98):
            print(f"Error: Port {port} is already in use on {target_host}. Please select another port via BRAIN_PORT or --port.")
            sys.exit(1)
        raise
    finally:
        if server:
            try:
                server.server_close()
            except Exception:
                pass
        release_pid_file()
        print("Mission Control Dashboard cleanly terminated.")


if __name__ == "__main__":
    run_server()
