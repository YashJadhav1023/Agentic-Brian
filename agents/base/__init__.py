"""Base Agent Adapter Interfaces and Declarations."""
from .adapter import AgentAdapter, TaskExecutionResult, AgentStatus, Capability, ExecutionMode

__all__ = [
    "AgentAdapter",
    "TaskExecutionResult",
    "AgentStatus",
    "Capability",
    "ExecutionMode",
]
