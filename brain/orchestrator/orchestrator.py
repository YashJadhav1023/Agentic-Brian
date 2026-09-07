"""Master Orchestrator.

High-level interface for decomposing user requests, routing to agents, and
dispatching tasks to the swarm worker pool.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.router.smart_router import SmartRouter
from brain.orchestrator.swarm import SwarmWorkerPool
from events.bus import EventBus, EventType
from providers.registry.bootstrap import create_default_registry
from providers.registry.provider_registry import ProviderRegistry
from tasks.manager import Task, TaskManager, TaskPriority


class Orchestrator:
    """End-to-end orchestration coordinator."""

    def __init__(
        self,
        task_manager: TaskManager | None = None,
        registry: ProviderRegistry | None = None,
        event_bus: EventBus | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self._registry = registry or create_default_registry()
        self._task_manager = task_manager or TaskManager()
        self._event_bus = event_bus or EventBus()
        self._workspace = workspace_dir or Path.cwd()
        self._router = SmartRouter(self._registry)
        self._swarm = SwarmWorkerPool(
            task_manager=self._task_manager,
            registry=self._registry,
            event_bus=self._event_bus,
            workspace_dir=self._workspace,
        )

    def plan_and_dispatch(
        self,
        instruction: str,
        files: list[str] | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        preferred_agent: str | None = None,
        preferred_model: str | None = None,
    ) -> Task:
        """Analyze an instruction, route it, create a persistent task, and queue it."""
        decision = self._router.route(
            task_text=instruction,
            preferred_agent=preferred_agent,
            preferred_model=preferred_model,
        )

        task = self._task_manager.create_task(
            title=instruction[:80],
            description=instruction,
            priority=priority,
            complexity=decision.complexity.value,
            assigned_agent=decision.agent_id,
            assigned_account=decision.account_id,
            assigned_model=decision.model,
        )
        if files:
            task.files = files
            self._task_manager.save_task(task)

        self._event_bus.publish(
            EventType.TASK_CREATED,
            agent_id=decision.agent_id,
            task_id=task.task_id,
            metadata={
                "assigned_model": decision.model,
                "complexity": decision.complexity.value,
                "routing_reason": decision.reason,
            },
        )

        return task

    def execute_next(self) -> Any:
        """Trigger swarm execution on the next queued batch."""
        return self._swarm.run_queue()
