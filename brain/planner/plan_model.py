"""Plan and ExecutionGraph Models for Mission Control Phase 18.

Defines multi-step plans, dependency DAGs, approval requirements,
and rollback metadata for autonomous task execution.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ROLLED_BACK = "ROLLED_BACK"


class PlanStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED_ON_APPROVAL = "BLOCKED_ON_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class PlanStep:
    """A single executable or verifiable action in a plan."""
    step_id: str
    title: str
    description: str = ""
    dependencies: List[str] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    agent: str = "antigravity"
    model: str = "auto"
    provider: str = "auto"
    account: str = "auto"
    tool: Optional[str] = None
    mcp_server: Optional[str] = None
    command: Optional[str] = None
    risk_level: str = "READ_ONLY"  # READ_ONLY, LOW_RISK_WRITE, HIGH_RISK_WRITE, DESTRUCTIVE
    requires_approval: bool = False
    approved: bool = False
    status: StepStatus = StepStatus.PENDING
    output: Optional[str] = None
    error: Optional[str] = None
    verification_rules: List[str] = field(default_factory=list)
    rollback_action: Optional[str] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "description": self.description,
            "dependencies": list(self.dependencies),
            "required_capabilities": list(self.required_capabilities),
            "agent": self.agent,
            "model": self.model,
            "provider": self.provider,
            "account": self.account,
            "tool": self.tool,
            "mcp_server": self.mcp_server,
            "command": self.command,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "approved": self.approved,
            "status": self.status.value if isinstance(self.status, StepStatus) else str(self.status),
            "output": self.output,
            "error": self.error,
            "verification_rules": list(self.verification_rules),
            "rollback_action": self.rollback_action,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class Plan:
    """An inspectable, deterministic plan consisting of dependency-linked steps."""
    plan_id: str
    task: str
    domain: str
    steps: List[PlanStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_at: float = field(default_factory=time.time)
    is_dry_run: bool = False
    requires_approval: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task": self.task,
            "domain": self.domain,
            "status": self.status.value if isinstance(self.status, PlanStatus) else str(self.status),
            "created_at": self.created_at,
            "is_dry_run": self.is_dry_run,
            "requires_approval": self.requires_approval,
            "steps_count": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
            "metadata": self.metadata,
        }


class ExecutionGraph:
    """DAG engine managing step dependencies, cycle detection, and topological sorting."""

    def __init__(self, steps: List[PlanStep]) -> None:
        self.steps = {s.step_id: s for s in steps}
        self._validate_no_cycles()

    def _validate_no_cycles(self) -> None:
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node_id: str) -> None:
            visited.add(node_id)
            rec_stack.add(node_id)

            step = self.steps.get(node_id)
            if step:
                for dep in step.dependencies:
                    if dep not in visited:
                        dfs(dep)
                    elif dep in rec_stack:
                        raise ValueError(f"Cyclic dependency detected in execution graph: {node_id} -> {dep}")

            rec_stack.remove(node_id)

        for s_id in self.steps:
            if s_id not in visited:
                dfs(s_id)

    def get_topological_order(self) -> List[PlanStep]:
        """Return steps in valid execution order."""
        in_degree = {s_id: 0 for s_id in self.steps}
        adj: Dict[str, List[str]] = {s_id: [] for s_id in self.steps}

        for s_id, step in self.steps.items():
            for dep in step.dependencies:
                if dep in self.steps:
                    adj[dep].append(s_id)
                    in_degree[s_id] += 1

        queue = [s_id for s_id, deg in in_degree.items() if deg == 0]
        order = []

        while queue:
            curr = queue.pop(0)
            order.append(self.steps[curr])
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return order

    def get_ready_steps(self) -> List[PlanStep]:
        """Return steps that are pending and all their dependencies have completed."""
        ready = []
        for step in self.steps.values():
            if step.status == StepStatus.PENDING:
                deps_met = all(
                    dep in self.steps and self.steps[dep].status == StepStatus.COMPLETED
                    for dep in step.dependencies
                )
                if deps_met:
                    ready.append(step)
        return ready
