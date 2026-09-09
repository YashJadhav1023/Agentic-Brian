"""Abstract base class for all agent execution adapters.

Every execution resource (CLI, headless subshell, or future API bridge) implements
this standard interface. The core brain orchestrator and router interact only
with this interface and make zero assumptions about provider-specific CLI flags.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Any, TypedDict


class Capability(str, Enum):
    """Explicit, provider-neutral capabilities used for planning and routing."""
    TERMINAL_OPERATIONS = "terminal-operations"
    LOCAL_VALIDATION = "local-validation"
    BUILD_AND_TEST = "build-and-test"
    CLOUD_READ_ONLY = "cloud-read-only"
    KUBERNETES_READ_ONLY = "kubernetes-read-only"
    ARCHITECTURE = "architecture"
    PROTOCOL_DESIGN = "protocol-design"
    GOVERNANCE = "governance"
    DEEP_REASONING = "deep-reasoning"
    EDITOR_REFACTORING = "editor-refactoring"
    FRONTEND_STYLING = "frontend-styling"
    COMPONENT_REFACTORING = "component-refactoring"
    CODE_REVIEW = "code-review"
    CODE_COMPLETION = "code-completion"
    DOCUMENTATION = "documentation"
    TEST_SCAFFOLDING = "test-scaffolding"


class ExecutionMode(str, Enum):
    """How the agent can actually be executed."""
    HEADLESS = "headless"
    DELIVERY = "delivery"
    API = "api"


class AgentStatus(str, Enum):
    """Standardized lifecycle and health states."""
    ONLINE = "ONLINE"
    IDLE = "IDLE"
    WORKING = "WORKING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    OFFLINE = "OFFLINE"
    INTERACTIVE_ONLY = "INTERACTIVE_ONLY"


#: Sentinel used whenever a provider does not report which model actually served a
#: request. It is never replaced by the requested model — see docs/MODEL_ROUTING.md.
UNKNOWN_MODEL = "unknown"


class ExecutionResultUsage(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    usage_source: str


class ExecutionResultError(TypedDict, total=False):
    type: str
    message: str


class ExecutionResult(TypedDict):
    task_id: str
    provider: str
    agent_id: str
    model: str
    status: str
    exit_code: int
    started_at: str | None
    completed_at: str | None
    duration_seconds: float
    stdout: str
    stderr: str
    usage: ExecutionResultUsage
    error: ExecutionResultError | None


@dataclass
class TaskExecutionResult:
    """Normalized output produced by an agent execution.

    Raw execution metadata (`raw_stdout`, `raw_stderr`, `command`, `raw_response`)
    is deliberately preserved so no provider detail is lost by normalization.
    """
    task_id: str
    agent_id: str
    account_id: str
    provider: str
    success: bool
    exit_code: int
    output: str
    error: str
    requested_model: str | None = None
    actual_model: str | None = None
    duration_seconds: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usage_source: str = "unknown"
    started_at: str | None = None
    completed_at: str | None = None
    conversation_id: str | None = None
    files_touched: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)

    # --- Extended, provider-reported execution metadata -------------------
    session_id: str | None = None
    #: Provider-reported terminal status string (e.g. Antigravity "SUCCESS").
    provider_status: str | None = None
    #: Provider-reported duration, which may differ from wall-clock duration.
    provider_duration_seconds: float | None = None
    num_turns: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    #: Whether the payload was valid structured JSON rather than plain text.
    json_valid: bool = False
    raw_stdout: str = ""
    raw_stderr: str = ""
    #: Argv actually executed, with the prompt redacted for log safety.
    command: list[str] = field(default_factory=list)

    def to_execution_result(self) -> dict[str, Any]:
        """Return the standard ExecutionResult contract for all providers."""
        model_str = (
            self.actual_model
            if self.actual_model and self.actual_model != UNKNOWN_MODEL
            else (self.requested_model or UNKNOWN_MODEL)
        )
        return {
            "task_id": self.task_id,
            "provider": self.provider,
            "agent_id": self.agent_id,
            "model": model_str,
            "status": "completed" if self.success else "failed",
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "output": self.output,
            "stdout": self.raw_stdout or self.output,
            "stderr": self.raw_stderr or self.error,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "usage_source": self.usage_source,
            },
            "error": {
                "type": "ExecutionError" if self.error else "ExecutionFailed",
                "message": self.error or "Execution failed",
            } if (not self.success or self.error) else None,
        }

    def normalized(self) -> dict[str, Any]:
        """Return the canonical cross-provider task result schema."""
        return {
            "agent_id": self.agent_id,
            "account_id": self.account_id,
            "provider": self.provider,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "response": self.output,
            "usage": {
                "input_tokens": int(self.input_tokens or 0),
                "output_tokens": int(self.output_tokens or 0),
                "thinking_tokens": self.thinking_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "total_tokens": int(self.total_tokens or 0),
                "usage_source": self.usage_source,
            },
            "num_turns": self.num_turns,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "status": "completed" if self.success else "failed",
            "provider_status": self.provider_status,
            "json_valid": self.json_valid,
            "execution_result": self.to_execution_result(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return dict representation of execution result."""
        return self.to_execution_result()


class AgentAdapter(abc.ABC):
    """Abstract interface for an agent execution adapter."""

    @property
    @abc.abstractmethod
    def agent_id(self) -> str:
        """Unique identifier for this agent instance (e.g. 'antigravity-account-1')."""
        pass

    @property
    @abc.abstractmethod
    def provider(self) -> str:
        """Provider name (e.g. 'antigravity', 'kiro', 'cline')."""
        pass

    @property
    @abc.abstractmethod
    def account_id(self) -> str:
        """Account identifier (e.g. 'account-1', 'account-2', 'cli')."""
        pass

    @property
    @abc.abstractmethod
    def execution_mode(self) -> ExecutionMode:
        """Execution mode."""
        pass

    @abc.abstractmethod
    def capabilities(self) -> frozenset[Capability]:
        """Declared capabilities."""
        pass

    @abc.abstractmethod
    def available_models(self) -> tuple[str, ...]:
        """Tuple of model identifiers recognized by this adapter."""
        pass

    @abc.abstractmethod
    def health(self) -> tuple[bool, str]:
        """Check if this agent is ready to execute tasks."""
        pass

    @abc.abstractmethod
    def execute(
        self,
        task_id: str,
        prompt: str,
        model: str | None = None,
        work_dir: Path | None = None,
        timeout_seconds: int = 300,
        options: dict[str, Any] | None = None,
    ) -> TaskExecutionResult:
        """Execute a task prompt and return a standardized result."""
        pass

    @abc.abstractmethod
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
        """Continue an existing conversation or task session."""
        pass

    @abc.abstractmethod
    def cancel(self, task_id: str) -> bool:
        """Attempt to cancel an in-flight execution."""
        pass

    # --- Optional interface with safe defaults ----------------------------
    # These are intentionally concrete so that adding capability to the
    # contract never breaks an existing provider adapter.

    def status(self, task_id: str | None = None) -> AgentStatus:
        """Current lifecycle state of this agent.

        Adapters that track in-flight work override this. The default is a
        conservative IDLE for adapters with no internal state machine.
        """
        return AgentStatus.IDLE

    def stream(
        self,
        task_id: str,
        prompt: str,
        model: str | None = None,
        work_dir: Path | None = None,
        timeout_seconds: int = 300,
        options: dict[str, Any] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> TaskExecutionResult:
        """Execute while emitting incremental events.

        The default implementation degrades to a single terminal event so that
        callers can always use `stream()` regardless of provider support.
        """
        result = self.execute(
            task_id=task_id,
            prompt=prompt,
            model=model,
            work_dir=work_dir,
            timeout_seconds=timeout_seconds,
            options=options,
        )
        if on_event:
            on_event({"type": "result", "payload": result.normalized()})
        return result

    @property
    def profile_dir(self) -> Path | None:
        """Filesystem profile that isolates this account, when one exists."""
        return None

    def describe(self) -> dict[str, Any]:
        """Registry-facing description of this execution resource."""
        healthy, reason = self.health()
        return {
            "agent_id": self.agent_id,
            "provider": self.provider,
            "account_id": self.account_id,
            "profile": str(self.profile_dir) if self.profile_dir else None,
            "execution_mode": self.execution_mode.value,
            "status": self.status().value,
            "capabilities": sorted(c.value for c in self.capabilities()),
            "models": list(self.available_models()),
            "health": {"healthy": healthy, "reason": reason},
        }
