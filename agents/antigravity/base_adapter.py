"""Shared base logic for Antigravity Account Adapters."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agents.base.adapter import AgentAdapter, Capability, ExecutionMode, TaskExecutionResult

# Candidate CLI binaries
ANTIGRAVITY_BIN_CANDIDATES = [
    Path("/home/setoo/.gemini/bin/agy"),
    Path("/home/setoo/.local/bin/antigravity"),
    Path("/usr/local/bin/antigravity"),
]


def resolve_antigravity_bin() -> Path | None:
    for candidate in ANTIGRAVITY_BIN_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    which_path = shutil.which("antigravity") or shutil.which("agy")
    if which_path:
        return Path(which_path)
    return None


class AntigravityBaseAdapter(AgentAdapter):
    """Base class for Antigravity CLI invocations isolated by data directory."""

    def __init__(self, data_dir_name: str) -> None:
        self._data_dir_name = data_dir_name
        self._bin = resolve_antigravity_bin()

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.HEADLESS

    def available_models(self) -> tuple[str, ...]:
        return (
            "gemini-3.8-flash-high",
            "gemini-3.8-flash-medium",
            "gemini-3.8-flash-low",
            "gemini-3.7-flash-high",
        )

    def health(self) -> tuple[bool, str]:
        if not self._bin:
            return False, "Antigravity CLI binary ('agy' or 'antigravity') not found"
        data_dir = Path.home() / ".gemini" / self._data_dir_name
        if not data_dir.is_dir():
            return False, f"Data directory {data_dir} does not exist"
        return True, f"Ready: CLI {self._bin} with isolated data dir {self._data_dir_name}"

    def execute(
        self,
        task_id: str,
        prompt: str,
        model: str | None = None,
        work_dir: Path | None = None,
        timeout_seconds: int = 300,
        options: dict[str, Any] | None = None,
    ) -> TaskExecutionResult:
        if not self._bin:
            return TaskExecutionResult(
                task_id=task_id,
                agent_id=self.agent_id,
                account_id=self.account_id,
                provider=self.provider,
                success=False,
                exit_code=1,
                output="",
                error="Antigravity binary not found",
            )

        cmd = [
            str(self._bin),
            "--dangerously-skip-permissions",
            f"--app_data_dir={self._data_dir_name}",
            "--output-format", "json",
        ]
        if model:
            cmd.append(f"--model={model}")
        cmd.extend(["-p", prompt])

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
            out_raw = proc.stdout.strip()
            err_raw = proc.stderr.strip()

            # Attempt JSON parsing of output
            parsed = {}
            response_text = out_raw
            conv_id = None
            in_tok, out_tok, tot_tok = 0, 0, 0

            if out_raw.startswith("{") and out_raw.endswith("}"):
                try:
                    parsed = json.loads(out_raw)
                    response_text = parsed.get("response", out_raw)
                    conv_id = parsed.get("conversation_id")
                    usage = parsed.get("usage", {})
                    in_tok = usage.get("input_tokens", 0)
                    out_tok = usage.get("output_tokens", 0)
                    tot_tok = usage.get("total_tokens", 0)
                except Exception:
                    pass

            return TaskExecutionResult(
                task_id=task_id,
                agent_id=self.agent_id,
                account_id=self.account_id,
                provider=self.provider,
                success=(proc.returncode == 0),
                exit_code=proc.returncode,
                output=response_text,
                error=err_raw,
                requested_model=model,
                actual_model=model or "default",
                duration_seconds=dur,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=tot_tok,
                conversation_id=conv_id,
                raw_response=parsed,
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
        if not self._bin:
            return TaskExecutionResult(
                task_id=task_id,
                agent_id=self.agent_id,
                account_id=self.account_id,
                provider=self.provider,
                success=False,
                exit_code=1,
                output="",
                error="Antigravity binary not found",
            )

        cmd = [
            str(self._bin),
            "--dangerously-skip-permissions",
            f"--app_data_dir={self._data_dir_name}",
            "--output-format", "json",
            f"--conversation={session_id}",
        ]
        if model:
            cmd.append(f"--model={model}")
        cmd.extend(["-p", prompt])

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
            out_raw = proc.stdout.strip()
            parsed = {}
            response_text = out_raw
            if out_raw.startswith("{") and out_raw.endswith("}"):
                try:
                    parsed = json.loads(out_raw)
                    response_text = parsed.get("response", out_raw)
                except Exception:
                    pass

            return TaskExecutionResult(
                task_id=task_id,
                agent_id=self.agent_id,
                account_id=self.account_id,
                provider=self.provider,
                success=(proc.returncode == 0),
                exit_code=proc.returncode,
                output=response_text,
                error=proc.stderr.strip(),
                requested_model=model,
                actual_model=model or "default",
                duration_seconds=dur,
                conversation_id=session_id,
                raw_response=parsed,
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

    def cancel(self, task_id: str) -> bool:
        return True
