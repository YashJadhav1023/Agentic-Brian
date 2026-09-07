"""Multi-Agent Swarm Worker and Execution Pool.

Coordinates concurrent headless task execution across:
- Antigravity Account 1 (CLI session)
- Antigravity Account 2 (IDE session)
- Kiro CLI
- Cline CLI

Enforces bounded concurrency (MAX_CONCURRENT_AGENTS=2 for 2-core / 16GB host),
file locking, model verification, event emissions, and automatic handoff generation.
"""
from __future__ import annotations

import concurrent.futures
import os
import time
from pathlib import Path
from typing import Any

from agents.base.adapter import TaskExecutionResult
from events.bus import EventBus, EventType
from handoffs.handoff_manager import HandoffManager, HandoffRecord
from locks.file_locker import FileLocker
from models.policies.model_policy import verify_actual_model
from providers.registry.bootstrap import create_default_registry
from providers.registry.provider_registry import ProviderRegistry
from tasks.manager import Task, TaskManager, TaskStatus

MAX_CONCURRENT_AGENTS = int(os.environ.get("MAX_CONCURRENT_AGENTS", "2"))


class SwarmWorkerPool:
    """Manages concurrent headless execution of tasks using registered adapters."""

    def __init__(
        self,
        task_manager: TaskManager,
        registry: ProviderRegistry | None = None,
        event_bus: EventBus | None = None,
        file_locker: FileLocker | None = None,
        handoff_manager: HandoffManager | None = None,
        workspace_dir: Path | None = None,
        max_workers: int = MAX_CONCURRENT_AGENTS,
    ) -> None:
        self._task_manager = task_manager
        self._registry = registry or create_default_registry()
        self._event_bus = event_bus or EventBus()
        self._file_locker = file_locker or FileLocker()
        self._handoff_manager = handoff_manager or HandoffManager()
        self._workspace = workspace_dir or Path.cwd()
        self._max_workers = max_workers

    def execute_task(self, task: Task) -> TaskExecutionResult:
        """Execute a single task synchronously on its assigned agent."""
        agent_id = task.assigned_agent or "antigravity-account-1"
        adapter = self._registry.get_adapter(agent_id)

        if not adapter:
            # Fallback to any active adapter
            active = self._registry.list_active_adapters()
            if not active:
                err_res = TaskExecutionResult(
                    task_id=task.task_id,
                    agent_id=agent_id,
                    account_id="unknown",
                    provider="unknown",
                    success=False,
                    exit_code=1,
                    output="",
                    error="No active agent adapters available",
                )
                self._task_manager.update_status(task.task_id, TaskStatus.FAILED, error=err_res.error)
                return err_res
            adapter = active[0]
            agent_id = adapter.agent_id

        # Mark RUNNING
        self._task_manager.update_status(task.task_id, TaskStatus.RUNNING)
        self._event_bus.publish(
            EventType.TASK_STARTED,
            agent_id=agent_id,
            task_id=task.task_id,
            metadata={"requested_model": task.assigned_model},
        )

        # Acquire file locks if task declares target files
        locked_files: list[str] = []
        for file_path in task.files:
            if self._file_locker.acquire(file_path, agent_id=agent_id, task_id=task.task_id):
                locked_files.append(file_path)
                self._event_bus.publish(
                    EventType.FILE_LOCKED,
                    agent_id=agent_id,
                    task_id=task.task_id,
                    metadata={"file": file_path},
                )
            else:
                err_msg = f"Failed to acquire lock for file: {file_path}"
                self._task_manager.update_status(task.task_id, TaskStatus.BLOCKED, error=err_msg)
                self._event_bus.publish(
                    EventType.TASK_FAILED,
                    agent_id=agent_id,
                    task_id=task.task_id,
                    metadata={"error": err_msg},
                )
                # Release previously acquired locks
                for lf in locked_files:
                    self._file_locker.release(lf, agent_id=agent_id)
                return TaskExecutionResult(
                    task_id=task.task_id,
                    agent_id=agent_id,
                    account_id=adapter.account_id,
                    provider=adapter.provider,
                    success=False,
                    exit_code=1,
                    output="",
                    error=err_msg,
                )

        try:
            # Execute through adapter with explicitly passed model
            prompt = f"Task: {task.title}\n\nDescription:\n{task.description}"
            res = adapter.execute(
                task_id=task.task_id,
                prompt=prompt,
                model=task.assigned_model,
                work_dir=self._workspace,
                timeout_seconds=300,
            )

            # Verify actual model used
            actual_model = verify_actual_model(res.raw_response, task.assigned_model)
            res.actual_model = actual_model

            if res.success:
                self._task_manager.update_status(
                    task.task_id,
                    TaskStatus.COMPLETED,
                    result={"output": res.output[:2000], "duration_seconds": res.duration_seconds},
                    actual_model=actual_model,
                )
                self._event_bus.publish(
                    EventType.TASK_COMPLETED,
                    agent_id=agent_id,
                    task_id=task.task_id,
                    metadata={
                        "actual_model": actual_model,
                        "duration_seconds": res.duration_seconds,
                    },
                )

                # Generate structured handoff
                rec = HandoffRecord(
                    task=task.title,
                    objective=task.description,
                    completed=[f"Completed by {agent_id} on model {actual_model}"],
                    files_modified=task.files,
                    next_action="Task completed successfully.",
                    recommended_agent=agent_id,
                    recommended_model=actual_model,
                )
                self._handoff_manager.write_handoff(rec)
                self._event_bus.publish(
                    EventType.TASK_HANDOFF,
                    agent_id=agent_id,
                    task_id=task.task_id,
                )
            else:
                self._task_manager.update_status(
                    task.task_id,
                    TaskStatus.FAILED,
                    error=res.error,
                    actual_model=actual_model,
                )
                self._event_bus.publish(
                    EventType.TASK_FAILED,
                    agent_id=agent_id,
                    task_id=task.task_id,
                    metadata={"error": res.error},
                )

            return res
        finally:
            # Always release locks
            for lf in locked_files:
                self._file_locker.release(lf, agent_id=agent_id)
                self._event_bus.publish(
                    EventType.FILE_UNLOCKED,
                    agent_id=agent_id,
                    task_id=task.task_id,
                    metadata={"file": lf},
                )

    def run_queue(self) -> list[TaskExecutionResult]:
        """Process ready tasks up to MAX_CONCURRENT_AGENTS concurrently."""
        ready_tasks = self._task_manager.list_tasks(status=TaskStatus.READY)
        if not ready_tasks:
            return []

        results: list[TaskExecutionResult] = []
        batch = ready_tasks[:self._max_workers]

        with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_task = {executor.submit(self.execute_task, t): t for t in batch}
            for future in concurrent.futures.as_completed(future_to_task):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    task = future_to_task[future]
                    self._task_manager.update_status(task.task_id, TaskStatus.FAILED, error=str(e))
        return results
