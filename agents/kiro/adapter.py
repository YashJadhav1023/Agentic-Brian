"""Kiro CLI Agent Adapter."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agents.base.adapter import AgentAdapter, Capability, ExecutionMode, TaskExecutionResult


class KiroAdapter(AgentAdapter):
    """Adapter for the local headless Kiro CLI."""

    def __init__(self, executable: str = "kiro-cli") -> None:
        self._executable = executable

    @property
    def agent_id(self) -> str:
        return "kiro-cli"

    @property
    def provider(self) -> str:
        return "kiro"

    @property
    def account_id(self) -> str:
        return "cli"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.HEADLESS

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({
            Capability.TERMINAL_OPERATIONS,
            Capability.LOCAL_VALIDATION,
            Capability.BUILD_AND_TEST,
            Capability.CLOUD_READ_ONLY,
            Capability.KUBERNETES_READ_ONLY,
        })

    def available_models(self) -> tuple[str, ...]:
        return (
            "auto",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-sonnet-4.6",
            "claude-sonnet-4.5",
            "claude-haiku-4.5",
            "deepseek-3.2",
            "minimax-m2.5",
            "qwen3-coder-next",
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
        cmd = [exe, "chat", "--no-interactive"]
        if model and model != "auto":
            cmd.extend(["--model", model])
        cmd.extend(["--prompt", prompt])

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
        # Continue using conversation prompt
        return self.execute(task_id, f"Continuing previous session {session_id}:\n{prompt}", model, work_dir, timeout_seconds, options)

    def cancel(self, task_id: str) -> bool:
        return True
