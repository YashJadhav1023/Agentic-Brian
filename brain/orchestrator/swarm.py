"""Multi-Agent Swarm Worker and Execution Pool.

Coordinates concurrent headless task execution across the four active agents:

- Antigravity Account 1 (profile `antigravity-cli`)
- Antigravity Account 2 (profile `antigravity-ide`) — headless, no IDE required
- Kiro CLI
- Cline CLI

Execution path for every task:

    brain -> router -> account adapter -> CLI -> JSON -> normalized result
          -> task record -> session mapping -> shared memory -> handoff -> events

Enforces bounded concurrency (MAX_CONCURRENT_AGENTS, default 2 for a 2-core /
16 GB host), file locking, least-privilege permissions, honest model reporting,
task-to-conversation session mapping, and per-account telemetry.
"""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from agents.base.adapter import UNKNOWN_MODEL, AgentAdapter, TaskExecutionResult
from events.bus import EventBus, EventType
from handoffs.handoff_manager import HandoffManager, HandoffRecord
from locks.file_locker import FileLocker
from memory.retrieval.retriever import MemoryRetriever
from memory.store.memory_store import MemoryScope, MemoryStore
from models.policies.model_policy import verify_actual_model
from providers.registry.bootstrap import create_default_registry
from providers.registry.provider_registry import ProviderRegistry
from sessions.session_manager import SessionManager
from tasks.manager import Task, TaskManager, TaskStatus

MAX_CONCURRENT_AGENTS = int(os.environ.get("MAX_CONCURRENT_AGENTS", "2"))

#: Per-account telemetry event names. Account 2 has dedicated events as required
#: by the observability contract; the mapping keeps the swarm free of
#: agent-specific branching.
ACCOUNT_EVENTS: dict[str, dict[str, EventType]] = {
    "antigravity-account-2": {
        "started": EventType.ANTIGRAVITY_ACCOUNT2_STARTED,
        "completed": EventType.ANTIGRAVITY_ACCOUNT2_COMPLETED,
        "failed": EventType.ANTIGRAVITY_ACCOUNT2_FAILED,
        "health": EventType.ANTIGRAVITY_ACCOUNT2_HEALTH,
        "model_selected": EventType.ANTIGRAVITY_ACCOUNT2_MODEL_SELECTED,
        "handoff": EventType.ANTIGRAVITY_ACCOUNT2_HANDOFF,
    },
}

#: Which agent should verify another agent's output, so a completed task hands
#: off to a genuinely different agent rather than looping on itself.
VERIFICATION_AGENT = "kiro-cli"


class SwarmWorkerPool:
    """Manages concurrent headless execution of tasks using registered adapters."""

    def __init__(
        self,
        task_manager: TaskManager,
        registry: ProviderRegistry | None = None,
        event_bus: EventBus | None = None,
        file_locker: FileLocker | None = None,
        handoff_manager: HandoffManager | None = None,
        memory_store: MemoryStore | None = None,
        session_manager: SessionManager | None = None,
        workspace_dir: Path | None = None,
        max_workers: int = MAX_CONCURRENT_AGENTS,
    ) -> None:
        self._task_manager = task_manager
        self._registry = registry or create_default_registry()
        self._event_bus = event_bus or EventBus()
        self._file_locker = file_locker or FileLocker()
        self._handoff_manager = handoff_manager or HandoffManager()
        self._memory_store = memory_store or MemoryStore()
        self._retriever = MemoryRetriever(self._memory_store)
        self._session_manager = session_manager or SessionManager()
        self._workspace = workspace_dir or Path.cwd()
        self._max_workers = max(1, max_workers)

    # ------------------------------------------------------------------
    # Telemetry helpers
    # ------------------------------------------------------------------
    def _emit(
        self,
        kind: str,
        generic: EventType,
        adapter: AgentAdapter | None,
        agent_id: str,
        task_id: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Publish the generic event plus any account-specific twin."""
        meta = dict(metadata or {})
        meta.setdefault("account_id", adapter.account_id if adapter else "unknown")
        meta.setdefault("provider", adapter.provider if adapter else "unknown")

        self._event_bus.publish(
            generic,
            agent_id=agent_id,
            task_id=task_id,
            session_id=session_id,
            metadata=meta,
        )
        specific = ACCOUNT_EVENTS.get(agent_id, {}).get(kind)
        if specific:
            self._event_bus.publish(
                specific,
                agent_id=agent_id,
                task_id=task_id,
                session_id=session_id,
                metadata=meta,
            )

    def _git_state(self) -> str:
        """Compact, single-line git summary. Never a full status dump."""
        try:
            status = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=10, cwd=str(self._workspace),
            )
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10, cwd=str(self._workspace),
            )
        except Exception:
            return "unknown"
        if status.returncode != 0:
            return "unknown"
        changed = [line for line in status.stdout.splitlines() if line.strip()]
        branch_name = branch.stdout.strip() or "unknown-branch"
        if not changed:
            return f"clean on {branch_name}"
        return f"{len(changed)} uncommitted change(s) on {branch_name}"

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------
    def _build_prompt(self, task: Task) -> tuple[str, list[str]]:
        """Compose the agent prompt with only *relevant* shared memory.

        The whole memory database is never injected; the retriever caps both the
        number of entries and the byte budget.
        """
        parts = [f"Task: {task.title}", "", "Description:", task.description]

        memories = self._retriever.retrieve_context(
            query=f"{task.title} {task.description}",
            task_id=task.task_id,
            max_items=5,
            max_bytes=2048,
        )
        memory_refs = [m.memory_id for m in memories]
        context_block = self._retriever.format_context_for_prompt(memories)
        if context_block:
            parts.extend(["", context_block])

        return "\n".join(parts), memory_refs

    def _resolve_execution_options(self, task: Task, session_id: str) -> dict[str, Any]:
        """Least-privilege options.

        Nothing here silently enables `--dangerously-skip-permissions`. It is
        only set when a task explicitly carries it, or when the account is
        configured for it in config/providers.json.
        """
        options: dict[str, Any] = {"session_id": session_id}
        options.update(task.execution_options or {})
        if task.conversation_id:
            options.setdefault("conversation_id", task.conversation_id)
        return options

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def execute_task(self, task: Task) -> TaskExecutionResult:
        """Execute a single task synchronously on its assigned agent."""
        agent_id = task.assigned_agent or "antigravity-account-1"
        adapter = self._registry.get_adapter(agent_id)

        if not adapter:
            active = self._registry.list_active_adapters()
            if not active:
                error = "No active agent adapters available"
                self._task_manager.update_status(task.task_id, TaskStatus.FAILED, error=error)
                return TaskExecutionResult(
                    task_id=task.task_id,
                    agent_id=agent_id,
                    account_id="unknown",
                    provider="unknown",
                    success=False,
                    exit_code=1,
                    output="",
                    error=error,
                    actual_model=UNKNOWN_MODEL,
                )
            adapter = active[0]
            agent_id = adapter.agent_id

        session_id = task.session_id or f"sess-{uuid.uuid4().hex[:8]}"

        # Pre-flight health so a dead account fails fast instead of burning a run.
        healthy, health_reason = adapter.health()
        self._emit(
            "health",
            EventType.AGENT_HEALTH,
            adapter,
            agent_id,
            task.task_id,
            session_id,
            {"healthy": healthy, "reason": health_reason},
        )
        if not healthy:
            self._task_manager.update_status(
                task.task_id, TaskStatus.BLOCKED, error=f"Agent unhealthy: {health_reason}"
            )
            self._emit(
                "failed",
                EventType.TASK_FAILED,
                adapter,
                agent_id,
                task.task_id,
                session_id,
                {"error": health_reason},
            )
            return TaskExecutionResult(
                task_id=task.task_id,
                agent_id=agent_id,
                account_id=adapter.account_id,
                provider=adapter.provider,
                success=False,
                exit_code=1,
                output="",
                error=f"Agent unhealthy: {health_reason}",
                actual_model=UNKNOWN_MODEL,
                session_id=session_id,
            )

        self._task_manager.update_status(
            task.task_id,
            TaskStatus.RUNNING,
            session_id=session_id,
            requested_model=task.assigned_model,
        )

        # Record the session mapping *before* execution so an interrupted run
        # still leaves a resumable trace.
        self._session_manager.record_session(
            task_id=task.task_id,
            agent_id=agent_id,
            session_id=session_id,
            model=task.assigned_model,
            metadata={"account_id": adapter.account_id, "provider": adapter.provider},
        )

        self._emit(
            "started",
            EventType.TASK_STARTED,
            adapter,
            agent_id,
            task.task_id,
            session_id,
            {"requested_model": task.assigned_model},
        )
        self._emit(
            "model_selected",
            EventType.MODEL_SELECTED,
            adapter,
            agent_id,
            task.task_id,
            session_id,
            {"requested_model": task.assigned_model},
        )

        # Acquire file locks
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
                error = f"Failed to acquire lock for file: {file_path}"
                self._task_manager.update_status(task.task_id, TaskStatus.BLOCKED, error=error)
                self._emit(
                    "failed",
                    EventType.TASK_FAILED,
                    adapter,
                    agent_id,
                    task.task_id,
                    session_id,
                    {"error": error},
                )
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
                    error=error,
                    actual_model=UNKNOWN_MODEL,
                    session_id=session_id,
                )

        try:
            prompt, memory_refs = self._build_prompt(task)
            options = self._resolve_execution_options(task, session_id)

            result = adapter.execute(
                task_id=task.task_id,
                prompt=prompt,
                model=task.assigned_model,
                work_dir=self._workspace,
                timeout_seconds=300,
                options=options,
            )

            # Honest model reporting: only what the provider actually named.
            result.actual_model = verify_actual_model(result.raw_response, task.assigned_model)
            result.session_id = session_id

            self._session_manager.record_session(
                task_id=task.task_id,
                agent_id=agent_id,
                session_id=session_id,
                conversation_id=result.conversation_id,
                model=task.assigned_model,
                metadata={
                    "account_id": adapter.account_id,
                    "provider": adapter.provider,
                    "actual_model": result.actual_model,
                    "exit_code": result.exit_code,
                },
            )

            if result.success:
                self._on_success(task, adapter, agent_id, session_id, result, memory_refs)
            else:
                self._on_failure(task, adapter, agent_id, session_id, result)

            return result
        finally:
            for lf in locked_files:
                self._file_locker.release(lf, agent_id=agent_id)
                self._event_bus.publish(
                    EventType.FILE_UNLOCKED,
                    agent_id=agent_id,
                    task_id=task.task_id,
                    metadata={"file": lf},
                )

    def _on_success(
        self,
        task: Task,
        adapter: AgentAdapter,
        agent_id: str,
        session_id: str,
        result: TaskExecutionResult,
        memory_refs: list[str],
    ) -> None:
        # Shared memory: one compact, useful entry. Never the raw payload.
        stored_refs = list(memory_refs)
        if result.output:
            snippet = " ".join(result.output.split())[:500]
            entry = self._memory_store.add(
                content=(
                    f"Task '{task.title}' completed by {agent_id} "
                    f"(account {adapter.account_id}): {snippet}"
                ),
                scope=MemoryScope.PROJECT,
                source_agent=agent_id,
                task_id=task.task_id,
                session_id=session_id,
                importance=3,
                tags=["task_result", agent_id, adapter.account_id],
            )
            stored_refs.append(entry.memory_id)
            self._event_bus.publish(
                EventType.MEMORY_CREATED,
                agent_id=agent_id,
                task_id=task.task_id,
                session_id=session_id,
                metadata={"memory_id": entry.memory_id, "scope": MemoryScope.PROJECT.value},
            )

        git_state = self._git_state()
        recommended_agent = (
            VERIFICATION_AGENT if agent_id != VERIFICATION_AGENT else "antigravity-account-1"
        )

        next_action = (
            f"Verify the output of task {task.task_id} ('{task.title}') and integrate it."
        )
        if result.conversation_id:
            next_action += (
                f" Resume the originating conversation with "
                f"--conversation={result.conversation_id} if deeper context is required."
            )

        record = HandoffRecord(
            task=task.title,
            objective=task.description,
            completed=[
                f"Executed headlessly by {agent_id} (account {adapter.account_id}, "
                f"provider {adapter.provider})",
                f"Requested model {task.assigned_model or 'default'}; "
                f"provider-reported model {result.actual_model}",
                f"Response: {' '.join(result.output.split())[:300]}" if result.output else "No response text",
            ],
            files_modified=task.files,
            tests=["python3 -m unittest discover -s tests"],
            errors=task.errors or [],
            decisions=[
                "Least-privilege execution: --dangerously-skip-permissions not used "
                "unless explicitly configured",
                f"Session {session_id} maps to conversation {result.conversation_id}",
            ],
            relevant_memory=stored_refs,
            git_state=git_state,
            remaining_work=["Verify the produced result and integrate it"],
            next_action=next_action,
            recommended_agent=recommended_agent,
            recommended_model="auto",
            task_id=task.task_id,
            agent_id=agent_id,
            account_id=adapter.account_id,
            session_id=session_id,
            conversation_id=result.conversation_id,
        )
        handoff_path = self._handoff_manager.write_handoff(record)

        self._task_manager.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            result=result.normalized(),
            actual_model=result.actual_model,
            requested_model=task.assigned_model,
            session_id=session_id,
            conversation_id=result.conversation_id,
            duration_seconds=result.duration_seconds,
            handoff=str(handoff_path),
            memory_refs=stored_refs,
        )

        self._emit(
            "completed",
            EventType.TASK_COMPLETED,
            adapter,
            agent_id,
            task.task_id,
            session_id,
            {
                "requested_model": task.assigned_model,
                "actual_model": result.actual_model,
                "duration_seconds": result.duration_seconds,
                "conversation_id": result.conversation_id,
                "total_tokens": result.total_tokens,
            },
        )
        self._emit(
            "handoff",
            EventType.TASK_HANDOFF,
            adapter,
            agent_id,
            task.task_id,
            session_id,
            {"handoff": str(handoff_path), "recommended_agent": recommended_agent},
        )

    def _on_failure(
        self,
        task: Task,
        adapter: AgentAdapter,
        agent_id: str,
        session_id: str,
        result: TaskExecutionResult,
    ) -> None:
        self._task_manager.update_status(
            task.task_id,
            TaskStatus.FAILED,
            result=result.normalized(),
            error=result.error or f"exit {result.exit_code}",
            actual_model=result.actual_model,
            requested_model=task.assigned_model,
            session_id=session_id,
            conversation_id=result.conversation_id,
            duration_seconds=result.duration_seconds,
        )
        self._emit(
            "failed",
            EventType.TASK_FAILED,
            adapter,
            agent_id,
            task.task_id,
            session_id,
            {
                "error": (result.error or "")[:500],
                "exit_code": result.exit_code,
                "provider_status": result.provider_status,
            },
        )

    def run_queue(self) -> list[TaskExecutionResult]:
        """Execute the next batch of READY tasks with bounded concurrency."""
        ready_tasks = self._task_manager.list_tasks(status=TaskStatus.READY)
        if not ready_tasks:
            return []

        results: list[TaskExecutionResult] = []
        batch = ready_tasks[: self._max_workers]

        with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_task = {executor.submit(self.execute_task, t): t for t in batch}
            for future in concurrent.futures.as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    self._task_manager.update_status(
                        task.task_id, TaskStatus.FAILED, error=str(exc)
                    )
        return results
