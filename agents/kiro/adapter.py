"""Kiro CLI Agent Adapter.

Flags verified against `kiro-cli chat --help`:
  prompt is a positional INPUT, `--no-interactive` for headless,
  `--model <id>` selects the model, `--resume-id <id>` resumes a session,
  `--trust-all-tools` auto-approves tools (off unless explicitly requested).
"""
from __future__ import annotations

import os
import re
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

#: This CLI styles its output for a terminal. Escape sequences are stripped so
#: stored memory, handoffs and task records hold readable text.
_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class KiroAdapter(AgentAdapter):
    """Adapter for the local headless Kiro CLI.

    Capabilities, models and the executable come from config/providers.json when
    an entry exists; the literals below are only a last-resort fallback so the
    adapter still works if the config file is unavailable.
    """

    DEFAULT_CAPABILITIES = frozenset({
        Capability.TERMINAL_OPERATIONS,
        Capability.LOCAL_VALIDATION,
        Capability.BUILD_AND_TEST,
        Capability.CLOUD_READ_ONLY,
        Capability.KUBERNETES_READ_ONLY,
    })
    DEFAULT_MODELS = (
        "auto",
        "claude-opus-5",
        "claude-sonnet-5",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "claude-sonnet-4.6",
        "claude-haiku-4.5",
    )

    #: Single-account by design: isolated profile root not safely verified.
    multi_account: bool = False
    multi_account_reason: str = "isolated profile root not safely verified"

    def __init__(
        self,
        executable: str = "kiro-cli",
        capabilities: frozenset[Capability] | None = None,
        models: tuple[str, ...] | None = None,
        default_model: str = "auto",
        agent_id: str = "kiro-cli",
        account_id: str = "cli",
        aws_profile: str | None = None,
    ) -> None:
        self._executable = executable
        self._capabilities = capabilities or self.DEFAULT_CAPABILITIES
        self._models = models or self.DEFAULT_MODELS
        self._default_model = default_model
        self._agent_id = agent_id
        self._account_id = account_id
        self._aws_profile = aws_profile
        self._current_status = AgentStatus.IDLE

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def provider(self) -> str:
        return "kiro"

    @property
    def account_id(self) -> str:
        return self._account_id

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
        candidate = Path(self._executable).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return None

    def health(self, deep: bool = False) -> tuple[bool, str]:
        path = self._resolve_executable()
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
        opts = options or {}
        exe = self._resolve_executable()
        if not exe:
            return self._failure(task_id, f"{self._executable} not found on PATH", model)

        cmd = [exe, "chat", "--no-interactive"]
        # `auto` is a valid model id for this CLI, so it is passed explicitly
        # rather than omitted; omitting it falls back to the machine's stored
        # default, which may be stale or invalid.
        target_model = model or self._default_model
        if target_model:
            cmd.extend(["--model", target_model])
        if opts.get("resume_id"):
            cmd.extend(["--resume-id", str(opts["resume_id"])])
        # Least privilege: tools are not auto-trusted unless asked for.
        if opts.get("trust_all_tools") or opts.get("dangerously_skip_permissions"):
            cmd.append("--trust-all-tools")
        cmd.append(prompt)

        self._current_status = AgentStatus.WORKING
        start = time.time()
        import datetime
        started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            sub_env = os.environ.copy()
            if self._aws_profile:
                sub_env["AWS_PROFILE"] = self._aws_profile
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(work_dir or Path.cwd()),
                env=sub_env,
            )
        except subprocess.TimeoutExpired:
            self._current_status = AgentStatus.FAILED
            completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return self._failure(
                task_id, f"Task timed out after {timeout_seconds}s", model, 124, time.time() - start, started_at, completed_at
            )
        except Exception as exc:
            self._current_status = AgentStatus.FAILED
            completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return self._failure(task_id, str(exc), model, 1, time.time() - start, started_at, completed_at)

        completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        stdout = _ANSI_ESCAPE.sub("", proc.stdout).strip()
        stderr = _ANSI_ESCAPE.sub("", proc.stderr).strip()
        # Exit 0 with no answer is not a success; this CLI reports some errors
        # on stderr while still exiting 0.
        success = proc.returncode == 0 and bool(stdout)
        error = stderr if not success else ""
        if not success and not error:
            error = f"Kiro produced no output (exit {proc.returncode})"

        self._current_status = AgentStatus.IDLE if success else AgentStatus.FAILED
        return TaskExecutionResult(
            task_id=task_id,
            agent_id=self.agent_id,
            account_id=self.account_id,
            provider=self.provider,
            success=success,
            exit_code=proc.returncode,
            output=stdout,
            error=error,
            requested_model=target_model,
            # The CLI does not report integer token counts or model id in text mode
            actual_model=UNKNOWN_MODEL,
            duration_seconds=time.time() - start,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            usage_source="unknown",
            started_at=started_at,
            completed_at=completed_at,
            raw_stdout=proc.stdout,
            raw_stderr=proc.stderr,
            command=[c for c in cmd[:-1]] + ["<prompt redacted>"],
        )

    def _failure(
        self,
        task_id: str,
        error: str,
        model: str | None,
        exit_code: int = 1,
        duration: float = 0.0,
        started_at: str | None = None,
        completed_at: str | None = None,
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
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            usage_source="unknown",
            started_at=started_at,
            completed_at=completed_at,
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
        """Resume a Kiro conversation with --resume-id where one is known."""
        opts = dict(options or {})
        opts.setdefault("resume_id", session_id)
        return self.execute(task_id, prompt, model, work_dir, timeout_seconds, opts)

    def cancel(self, task_id: str) -> bool:
        self._current_status = AgentStatus.IDLE
        return True
