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
from typing import Callable, Any


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


@dataclass
class TaskExecutionResult:
    """Normalized output produced by an agent execution."""
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
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    conversation_id: str | None = None
    files_touched: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)


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
