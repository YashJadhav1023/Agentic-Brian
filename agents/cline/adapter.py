"""Cline CLI Agent Adapter.

Flags verified against `cline --help`:
  the prompt is positional, `-p/--plan` is PLAN MODE (not print),
  `--json` emits machine-readable messages, `-m/--model <id>` selects the model,
  `--id <session-id>` resumes a session, and `--auto-approve <boolean>`
  defaults to **true**.

Because auto-approval is on by default in this CLI, the adapter explicitly sends
`--auto-approve false` unless a caller opts in. Least privilege has to be stated,
not assumed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agents.base.adapter import (
    UNKNOWN_MODEL,
    AgentAdapter,
    AgentStatus,
    Capability,
    ExecutionMode,
    TaskExecutionResult,
)


class ClineAdapter(AgentAdapter):
    """Adapter for the headless Cline CLI."""

    DEFAULT_CAPABILITIES = frozenset({
        Capability.EDITOR_REFACTORING,
        Capability.FRONTEND_STYLING,
        Capability.COMPONENT_REFACTORING,
        Capability.CODE_REVIEW,
    })
    DEFAULT_MODELS = ("auto",)

    def __init__(
        self,
        executable: str = "cline",
        capabilities: frozenset[Capability] | None = None,
        models: tuple[str, ...] | None = None,
        default_model: str = "auto",
    ) -> None:
        self._executable = executable
        self._capabilities = capabilities or self.DEFAULT_CAPABILITIES
        self._models = models or self.DEFAULT_MODELS
        self._default_model = default_model
        self._current_status = AgentStatus.IDLE

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
        return self._capabilities

    def available_models(self) -> tuple[str, ...]:
        return self._models

    def status(self, task_id: str | None = None) -> AgentStatus:
        return self._current_status

    def _resolve_executable(self) -> str | None:
        found = shutil.which(self._executable)
        if found:
            return found
        candidate = Path(self._executable)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return None

    def health(self, deep: bool = False) -> tuple[bool, str]:
        path = self._resolve_executable()
        if path:
            return True, f"Found {self._executable} at {path}"
        return False, f"Executable {self._executable} not found on PATH"

    @staticmethod
    def _extract_response(stdout: str) -> tuple[str, dict[str, Any], bool]:
        """Parse --json NDJSON output; fall back to raw text."""
        text = stdout.strip()
        if not text:
            return "", {}, False
        messages: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                messages.append(obj)
        if not messages:
            return text, {}, False

        last = messages[-1]
        for key in ("text", "content", "message", "response", "result"):
            value = last.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), last, True
        return text, last, True

    def execute(
        self,
        task_id: str,
        prompt: str,
        model: str | None = None,
        work_dir: Path | None = None,
        timeout_seconds: int = 300,
        options: dict[str, Any] | None = None,
    ) -> TaskExecutionResult:
        opts = options or {}
        exe = self._resolve_executable()
        if not exe:
            return self._failure(task_id, f"{self._executable} not found on PATH", model)

        auto_approve = bool(
            opts.get("auto_approve", opts.get("dangerously_skip_permissions", False))
        )
        cmd = [exe, "--json", "--auto-approve", "true" if auto_approve else "false"]
        target_model = model or self._default_model
        if target_model and target_model != "auto":
            cmd.extend(["-m", target_model])
        if opts.get("session_id_native"):
            cmd.extend(["--id", str(opts["session_id_native"])])
        if opts.get("mode") == "plan":
            cmd.append("--plan")
        if work_dir:
            cmd.extend(["--cwd", str(work_dir)])
        cmd.append(prompt)

        self._current_status = AgentStatus.WORKING
        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(work_dir or Path.cwd()),
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            self._current_status = AgentStatus.FAILED
            return self._failure(
                task_id, f"Task timed out after {timeout_seconds}s", model, 124, time.time() - start
            )
        except Exception as exc:
            self._current_status = AgentStatus.FAILED
            return self._failure(task_id, str(exc), model, 1, time.time() - start)

        response, payload, json_valid = self._extract_response(proc.stdout)
        stderr = proc.stderr.strip()
        success = proc.returncode == 0 and bool(response.strip())
        error = stderr if not success else ""
        if not success and not error:
            error = f"Cline produced no output (exit {proc.returncode})"

        reported = payload.get("model") if isinstance(payload.get("model"), str) else None

        self._current_status = AgentStatus.IDLE if success else AgentStatus.FAILED
        return TaskExecutionResult(
            task_id=task_id,
            agent_id=self.agent_id,
            account_id=self.account_id,
            provider=self.provider,
            success=success,
            exit_code=proc.returncode,
            output=response,
            error=error,
            requested_model=target_model,
            actual_model=reported or UNKNOWN_MODEL,
            duration_seconds=time.time() - start,
            json_valid=json_valid,
            raw_response=payload,
            raw_stdout=proc.stdout,
            raw_stderr=proc.stderr,
            command=cmd[:-1] + ["<prompt redacted>"],
        )

    def _failure(
        self,
        task_id: str,
        error: str,
        model: str | None,
        exit_code: int = 1,
        duration: float = 0.0,
    ) -> TaskExecutionResult:
        return TaskExecutionResult(
            task_id=task_id,
            agent_id=self.agent_id,
            account_id=self.account_id,
            provider=self.provider,
            success=False,
            exit_code=exit_code,
            output="",
            error=error,
            requested_model=model,
            actual_model=UNKNOWN_MODEL,
            duration_seconds=duration,
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
        opts = dict(options or {})
        opts.setdefault("session_id_native", session_id)
        return self.execute(task_id, prompt, model, work_dir, timeout_seconds, opts)

    def cancel(self, task_id: str) -> bool:
        self._current_status = AgentStatus.IDLE
        return True
