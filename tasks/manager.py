"""Persistent Multi-Agent Task Lifecycle and Queue Manager.

Persists task records in structured directories so that state survives restarts,
crashes, or shell closes.
"""
from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    BACKLOG = "BACKLOG"
    READY = "READY"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    # Phase 4A Terminal States
    VERIFICATION_COMPLETE = "VERIFICATION_COMPLETE"
    DEPTH_LIMIT_REACHED = "DEPTH_LIMIT_REACHED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    REJECTED = "REJECTED"


class TaskPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class Task:
    task_id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.READY
    priority: TaskPriority = TaskPriority.NORMAL
    complexity: str = "standard"
    assigned_agent: str | None = None
    assigned_account: str | None = None
    assigned_model: str | None = None
    actual_model: str | None = None
    parent_task: str | None = None
    dependencies: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float = 0.0
    files: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    handoffs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    #: Per-task execution overrides handed to the adapter (e.g. explicit
    #: {"dangerously_skip_permissions": true}). Empty means "use the account's
    #: configured least-privilege defaults".
    execution_options: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    conversation_id: str | None = None
    requested_model: str | None = None
    # Phase 4A Continuation & Verification Contract
    task_type: str = "general"               # "general", "verification", "remediation"
    continuation_depth: int = 0
    max_continuation_depth: int = 5
    verification_depth: int = 0
    max_verification_depth: int = 1
    continuation_budget: int = 5
    verification_for: str | None = None      # ID of task this task verifies
    is_terminal: bool = False
    terminal_reason: str | None = None

    @property
    def is_verification(self) -> bool:
        return self.task_type == "verification" or bool(self.verification_for)

    @property
    def is_terminal_state(self) -> bool:
        return self.is_terminal or self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.VERIFICATION_COMPLETE,
            TaskStatus.DEPTH_LIMIT_REACHED,
            TaskStatus.BUDGET_EXHAUSTED,
            TaskStatus.REJECTED,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["priority"] = self.priority.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Task:
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in known}
        if "status" in data:
            data["status"] = TaskStatus(data["status"])
        if "priority" in data:
            data["priority"] = TaskPriority(data["priority"])
        return cls(**data)


class TaskManager:
    """Manages file-backed task persistence across queue, active, completed, failed."""

    def __init__(self, root_tasks_dir: Path | None = None) -> None:
        self._root = root_tasks_dir or Path(__file__).resolve().parent
        self._dir_queue = self._root / "queue"
        self._dir_active = self._root / "active"
        self._dir_completed = self._root / "completed"
        self._dir_failed = self._root / "failed"

        for d in (self._dir_queue, self._dir_active, self._dir_completed, self._dir_failed):
            d.mkdir(parents=True, exist_ok=True)

    def _dir_for_status(self, status: TaskStatus) -> Path:
        if status in (TaskStatus.READY, TaskStatus.BACKLOG):
            return self._dir_queue
        elif status in (TaskStatus.RUNNING, TaskStatus.PLANNING, TaskStatus.REVIEW):
            return self._dir_active
        elif status in (TaskStatus.COMPLETED, TaskStatus.VERIFICATION_COMPLETE):
            return self._dir_completed
        else:
            return self._dir_failed

    def create_task(
        self,
        title: str,
        description: str,
        priority: TaskPriority = TaskPriority.NORMAL,
        complexity: str = "standard",
        assigned_agent: str | None = None,
        assigned_account: str | None = None,
        assigned_model: str | None = None,
        dependencies: list[str] | None = None,
        parent_task: str | None = None,
        task_id: str | None = None,
        task_type: str = "general",
        continuation_depth: int = 0,
        max_continuation_depth: int = 5,
        verification_depth: int = 0,
        max_verification_depth: int = 1,
        continuation_budget: int = 5,
        verification_for: str | None = None,
        is_terminal: bool = False,
        terminal_reason: str | None = None,
    ) -> Task:
        t_id = task_id or f"task-{uuid.uuid4().hex[:8]}"
        task = Task(
            task_id=t_id,
            title=title,
            description=description,
            status=TaskStatus.READY,
            priority=priority,
            complexity=complexity,
            assigned_agent=assigned_agent,
            assigned_account=assigned_account,
            assigned_model=assigned_model,
            dependencies=dependencies or [],
            parent_task=parent_task,
            task_type=task_type,
            continuation_depth=continuation_depth,
            max_continuation_depth=max_continuation_depth,
            verification_depth=verification_depth,
            max_verification_depth=max_verification_depth,
            continuation_budget=continuation_budget,
            verification_for=verification_for,
            is_terminal=is_terminal,
            terminal_reason=terminal_reason,
        )
        self.save_task(task)
        return task

    def save_task(self, task: Task) -> Path:
        target_dir = self._dir_for_status(task.status)
        target_file = target_dir / f"{task.task_id}.json"

        # Remove from other directories if moving
        for d in (self._dir_queue, self._dir_active, self._dir_completed, self._dir_failed):
            if d != target_dir:
                old_file = d / f"{task.task_id}.json"
                if old_file.exists():
                    old_file.unlink(missing_ok=True)

        target_file.write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")
        return target_file

    def get_task(self, task_id: str) -> Task | None:
        filename = f"{task_id}.json"
        for d in (self._dir_queue, self._dir_active, self._dir_completed, self._dir_failed):
            file_path = d / filename
            if file_path.exists():
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
                    return Task.from_dict(data)
                except Exception:
                    return None
        return None

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        actual_model: str | None = None,
        requested_model: str | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        duration_seconds: float | None = None,
        handoff: str | None = None,
        memory_refs: list[str] | None = None,
        is_terminal: bool | None = None,
        terminal_reason: str | None = None,
        task_type: str | None = None,
        continuation_depth: int | None = None,
        verification_depth: int | None = None,
        continuation_budget: int | None = None,
        verification_for: str | None = None,
    ) -> Task | None:
        task = self.get_task(task_id)
        if not task:
            return None

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        task.status = status

        if status == TaskStatus.RUNNING and not task.started_at:
            task.started_at = now
        elif status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.VERIFICATION_COMPLETE,
            TaskStatus.DEPTH_LIMIT_REACHED,
            TaskStatus.BUDGET_EXHAUSTED,
            TaskStatus.REJECTED,
        ):
            task.completed_at = now

        if is_terminal is not None:
            task.is_terminal = is_terminal
        elif status in (
            TaskStatus.CANCELLED,
            TaskStatus.VERIFICATION_COMPLETE,
            TaskStatus.DEPTH_LIMIT_REACHED,
            TaskStatus.BUDGET_EXHAUSTED,
            TaskStatus.REJECTED,
        ):
            task.is_terminal = True

        if terminal_reason is not None:
            task.terminal_reason = terminal_reason
        elif status in (
            TaskStatus.VERIFICATION_COMPLETE,
            TaskStatus.DEPTH_LIMIT_REACHED,
            TaskStatus.BUDGET_EXHAUSTED,
            TaskStatus.REJECTED,
        ):
            task.terminal_reason = status.value

        if task_type is not None:
            task.task_type = task_type
        if continuation_depth is not None:
            task.continuation_depth = continuation_depth
        if verification_depth is not None:
            task.verification_depth = verification_depth
        if continuation_budget is not None:
            task.continuation_budget = continuation_budget
        if verification_for is not None:
            task.verification_for = verification_for

        if actual_model:
            task.actual_model = actual_model
        if requested_model:
            task.requested_model = requested_model
        if session_id:
            task.session_id = session_id
        if conversation_id:
            task.conversation_id = conversation_id
        if duration_seconds is not None:
            task.duration_seconds = duration_seconds
        if handoff:
            task.handoffs.append(handoff)
        if memory_refs:
            task.memory_refs.extend(memory_refs)
        if result:
            task.result.update(result)
        if error:
            task.errors.append(error)

        self.save_task(task)
        return task

    def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        """List tasks, newest first.

        Several statuses share a directory (BLOCKED, FAILED and CANCELLED all
        live in `failed/`), so the status filter is applied to the task record
        itself rather than inferred from its location.
        """
        dirs = (
            [self._dir_for_status(status)]
            if status
            else [self._dir_queue, self._dir_active, self._dir_completed, self._dir_failed]
        )
        tasks = []
        for d in dirs:
            for p in d.glob("*.json"):
                try:
                    task = Task.from_dict(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    continue
                if status and task.status != status:
                    continue
                tasks.append(task)
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def recover_orphaned_tasks(self) -> int:
        """Move uncompleted tasks from active back to queue upon system restart."""
        count = 0
        for p in self._dir_active.glob("*.json"):
            try:
                task = Task.from_dict(json.loads(p.read_text(encoding="utf-8")))
                task.status = TaskStatus.READY
                task.errors.append("Task was interrupted by system shutdown/restart and recovered.")
                self.save_task(task)
                count += 1
            except Exception:
                pass
        return count
