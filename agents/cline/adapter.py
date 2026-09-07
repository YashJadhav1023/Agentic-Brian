"""Cline CLI Agent Adapter."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agents.base.adapter import AgentAdapter, Capability, ExecutionMode, TaskExecutionResult


class ClineAdapter(AgentAdapter):
    """Adapter for the headless Cline CLI."""

    def __init__(self, executable: str = "cline") -> None:
        self._executable = executable

    @property
    def agent_id(self) -> str:
        return "cline"

    @property
    def provider(self) -> str:
        return "cline"

    @property
    def account_id(self) -> str:
        return "cli"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.HEADLESS

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({
            Capability.EDITOR_REFACTORING,
            Capability.FRONTEND_STYLING,
            Capability.COMPONENT_REFACTORING,
            Capability.CODE_REVIEW,
        })

    def available_models(self) -> tuple[str, ...]:
        return (
            "auto",
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "gpt-4o",
            "gemini-2.0-flash",
        )

    def health(self) -> tuple[bool, str]:
        path = shutil.which(self._executable)
        if path:
            return True, f"Found {self._executable} at {path}"
        return False, f"Executable {self._executable} not found on PATH"

    def execute(
        self,
        task_id: str,
        prompt: str,
        model: str | None = None,
        work_dir: Path | None = None,
        timeout_seconds: int = 300,
        options: dict[str, Any] | None = None,
    ) -> TaskExecutionResult:
        exe = shutil.which(self._executable) or self._executable
        cmd = [exe, "-p", prompt]
        if model and model != "auto":
            cmd.extend(["--model", model])

        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(work_dir or Path.cwd()),
                env=os.environ.copy(),
            )
            dur = time.time() - start_time
            return TaskExecutionResult(
                task_id=task_id,
                agent_id=self.agent_id,
                account_id=self.account_id,
                provider=self.provider,
                success=(proc.returncode == 0),
                exit_code=proc.returncode,
                output=proc.stdout.strip(),
                error=proc.stderr.strip(),
                requested_model=model,
                actual_model=model or "auto",
                duration_seconds=dur,
            )
        except subprocess.TimeoutExpired:
            return TaskExecutionResult(
                task_id=task_id,
                agent_id=self.agent_id,
                account_id=self.account_id,
                provider=self.provider,
                success=False,
                exit_code=124,
                output="",
                error=f"Task timed out after {timeout_seconds}s",
                requested_model=model,
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            return TaskExecutionResult(
                task_id=task_id,
                agent_id=self.agent_id,
                account_id=self.account_id,
                provider=self.provider,
                success=False,
                exit_code=1,
                output="",
                error=str(e),
                requested_model=model,
                duration_seconds=time.time() - start_time,
            )

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
        return self.execute(task_id, prompt, model, work_dir, timeout_seconds, options)

    def cancel(self, task_id: str) -> bool:
        return True
