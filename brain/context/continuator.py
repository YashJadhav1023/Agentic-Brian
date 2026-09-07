"""Universal Continue Engine.

Synthesizes context across previous tasks, handoffs, git diffs, and memory to resume
execution without re-prompting or losing context.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from handoffs.handoff_manager import HandoffManager
from memory.retrieval.retriever import MemoryRetriever
from tasks.manager import Task, TaskManager, TaskStatus


@dataclass
class ContinueContext:
    active_task: Task | None
    latest_handoff: str | None
    git_diff_stat: str
    recent_errors: list[str]
    next_recommended_action: str
    target_agent: str
    target_model: str
    prompt: str


class UniversalContinuator:
    """Inspects machine state, task history, and handoffs to build a continue prompt."""

    def __init__(
        self,
        task_manager: TaskManager,
        handoff_manager: HandoffManager,
        retriever: MemoryRetriever | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._handoff_manager = handoff_manager
        self._retriever = retriever
        self._workspace = workspace_dir or Path.cwd()

    def _get_git_state(self) -> str:
        try:
            res = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(self._workspace),
            )
            return res.stdout.strip() or "clean"
        except Exception:
            return "unknown"

    def build_continue_context(self) -> ContinueContext:
        # 1. Find latest active or ready task
        active_tasks = self._task_manager.list_tasks(status=TaskStatus.RUNNING)
        if not active_tasks:
            active_tasks = self._task_manager.list_tasks(status=TaskStatus.READY)

        task = active_tasks[0] if active_tasks else None

        # 2. Get latest handoff
        handoff_md = self._handoff_manager.get_current_handoff()

        # 3. Get git state
        git_state = self._get_git_state()

        # 4. Resolve next action and target agent
        errors: list[str] = []
        if task and task.errors:
            errors.extend(task.errors)

        target_agent = "antigravity-account-1"
        target_model = "auto"
        next_action = "Review system status and proceed with planned items."

        if task:
            target_agent = task.assigned_agent or "antigravity-account-1"
            target_model = task.assigned_model or "auto"
            next_action = f"Continue work on {task.title}: {task.description}"

        prompt_parts = [
            "### Universal Continue Directives:",
            f"Active Task: {task.title if task else 'General Orchestration'}",
            f"Next Action: {next_action}",
            f"Git Working Tree State: {git_state}",
        ]
        if errors:
            prompt_parts.append(f"Previous Errors to Resolve: {errors[-2:]}")

        if handoff_md:
            prompt_parts.append(f"Latest Handoff Summary:\n{handoff_md[:1000]}")

        prompt = "\n\n".join(prompt_parts)

        return ContinueContext(
            active_task=task,
            latest_handoff=handoff_md,
            git_diff_stat=git_state,
            recent_errors=errors,
            next_recommended_action=next_action,
            target_agent=target_agent,
            target_model=target_model,
            prompt=prompt,
        )
