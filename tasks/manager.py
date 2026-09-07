"""Persistent Multi-Agent Task Lifecycle and Queue Manager.

Persists task records in structured directories so that state survives restarts,
crashes, or shell closes.
"""
from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field
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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["priority"] = self.priority.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Task:
        data = dict(d)
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
        elif status == TaskStatus.COMPLETED:
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
    ) -> Task | None:
        task = self.get_task(task_id)
        if not task:
            return None

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        task.status = status

        if status == TaskStatus.RUNNING and not task.started_at:
            task.started_at = now
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.completed_at = now

        if actual_model:
            task.actual_model = actual_model
        if result:
            task.result.update(result)
        if error:
            task.errors.append(error)

        self.save_task(task)
        return task

    def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        dirs = [self._dir_for_status(status)] if status else [self._dir_queue, self._dir_active, self._dir_completed, self._dir_failed]
        tasks = []
        for d in dirs:
            for p in d.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    tasks.append(Task.from_dict(data))
                except Exception:
                    pass
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
