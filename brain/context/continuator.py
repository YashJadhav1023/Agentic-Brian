"""Universal Continue Engine.

Answers a bare "continue" without the user restating anything. It reconstructs
state from every durable source the brain owns:

    latest task      tasks/{queue,active,completed,failed}
    latest session   sessions/session_registry.json  (task -> session -> conversation)
    latest handoff   handoffs/current.json  (structured) + current.md (readable)
    relevant memory  memory/store/shared_memory.db  (relevance-filtered, bounded)
    git state        git status --short in the workspace
    unfinished work  remaining_work + task errors

It then names the next agent and, when the previous agent's conversation is
known, the conversation id to resume so context is not rebuilt from scratch.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from handoffs.handoff_manager import HandoffManager, HandoffRecord
from memory.retrieval.retriever import MemoryRetriever
from memory.store.memory_store import MemoryStore
from sessions.session_manager import SessionManager
from tasks.manager import Task, TaskManager, TaskStatus


@dataclass
class ContinueContext:
    active_task: Task | None
    latest_handoff: str | None
    handoff_record: HandoffRecord | None
    git_diff_stat: str
    recent_errors: list[str]
    next_recommended_action: str
    target_agent: str
    target_model: str
    prompt: str
    #: Antigravity conversation id to resume, when the target agent has one.
    resume_conversation_id: str | None = None
    resume_session_id: str | None = None
    remaining_work: list[str] = field(default_factory=list)
    relevant_memory: list[str] = field(default_factory=list)
    source: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.active_task.task_id if self.active_task else None,
            "target_agent": self.target_agent,
            "target_model": self.target_model,
            "resume_conversation_id": self.resume_conversation_id,
            "resume_session_id": self.resume_session_id,
            "next_action": self.next_recommended_action,
            "remaining_work": self.remaining_work,
            "errors": self.recent_errors,
            "git_state": self.git_diff_stat,
            "source": self.source,
        }


class UniversalContinuator:
    """Inspects durable state to build a continue decision and prompt."""

    def __init__(
        self,
        task_manager: TaskManager,
        handoff_manager: HandoffManager,
        retriever: MemoryRetriever | None = None,
        workspace_dir: Path | None = None,
        session_manager: SessionManager | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._handoff_manager = handoff_manager
        self._session_manager = session_manager or SessionManager()
        store = memory_store or MemoryStore()
        self._retriever = retriever or MemoryRetriever(store)
        self._workspace = workspace_dir or Path.cwd()

    def _get_git_state(self) -> str:
        """Compact, single-line git summary. A prompt never carries a raw dump."""
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

    def _latest_task(self) -> tuple[Task | None, str]:
        """Most relevant task: in-flight first, then queued, then last touched.

        CANCELLED tasks are deliberately excluded — a cancelled task is a
        decision, not unfinished work.
        """
        for status, label in (
            (TaskStatus.RUNNING, "running task"),
            (TaskStatus.READY, "queued task"),
            (TaskStatus.BLOCKED, "blocked task"),
            (TaskStatus.FAILED, "failed task"),
            (TaskStatus.COMPLETED, "completed task"),
        ):
            tasks = self._task_manager.list_tasks(status=status)
            if tasks:
                return tasks[0], label
        return None, "none"

    def build_continue_context(self) -> ContinueContext:
        task, source = self._latest_task()
        handoff_md = self._handoff_manager.get_current_handoff()
        record = self._handoff_manager.get_current_record()
        git_state = self._get_git_state()

        errors: list[str] = list(task.errors) if task and task.errors else []
        if record and record.errors:
            errors.extend(e for e in record.errors if e and e != "None")

        remaining = list(record.remaining_work) if record and record.remaining_work else []

        # --- Decide the next action and the next agent ------------------
        # Precedence matters. A handoff that describes *this* task is the
        # strongest signal; a failed task must be recovered before an unrelated
        # handoff is picked up; only then does the latest handoff apply.
        target_agent = "antigravity-account-1"
        target_model = "auto"
        handoff_matches_task = bool(
            record and task and record.task_id and record.task_id == task.task_id
        )
        task_needs_recovery = bool(
            task and task.status in (TaskStatus.FAILED, TaskStatus.BLOCKED)
        )

        if handoff_matches_task:
            next_action = record.next_action or f"Continue work on {task.title}"
            target_agent = record.recommended_agent or target_agent
            target_model = record.recommended_model or "auto"
            source = f"{source} + handoff"
        elif task_needs_recovery:
            last_error = (task.errors[-1] if task.errors else "unknown error").splitlines()[0]
            next_action = (
                f"Resolve the failure of task {task.task_id} ('{task.title}'): {last_error}"
            )
            target_agent = task.assigned_agent or target_agent
            target_model = task.assigned_model or "auto"
            source = f"{source} (recovery)"
        elif record and record.next_action:
            next_action = record.next_action
            target_agent = record.recommended_agent or target_agent
            target_model = record.recommended_model or "auto"
            source = f"{source} + handoff"
        elif task:
            next_action = f"Continue work on {task.title}: {task.description}"
            target_agent = task.assigned_agent or target_agent
            target_model = task.assigned_model or "auto"
        else:
            next_action = "Review system status and proceed with planned items."

        # --- Resolve a resumable conversation ---------------------------
        resume_conversation_id = None
        resume_session_id = None
        session = None
        if task:
            session = self._session_manager.get_by_task(task.task_id)
        if session is None:
            session = self._session_manager.get_latest_for_agent(target_agent)
        if session:
            resume_session_id = session.session_id
            # Only resume a conversation that belongs to the target agent; a
            # conversation id is account-scoped and must never cross accounts.
            if session.agent_id == target_agent:
                resume_conversation_id = session.conversation_id
        if record and record.conversation_id and record.agent_id == target_agent:
            resume_conversation_id = resume_conversation_id or record.conversation_id

        # --- Relevant memory only, never the whole store ----------------
        query = next_action if not task else f"{task.title} {next_action}"
        memories = self._retriever.retrieve_context(query=query, max_items=5, max_bytes=2048)
        memory_block = self._retriever.format_context_for_prompt(memories)

        prompt_parts = [
            "### Universal Continue",
            f"Resumed from: {source}",
            f"Active Task: {task.title if task else 'General Orchestration'}"
            + (f" ({task.task_id}, status {task.status.value})" if task else ""),
            f"Next Action: {next_action}",
            f"Git Working Tree: {git_state}",
        ]
        if remaining:
            prompt_parts.append("Remaining Work:\n" + "\n".join(f"- {r}" for r in remaining))
        if errors:
            prompt_parts.append("Unresolved Errors:\n" + "\n".join(f"- {e}" for e in errors[-3:]))
        if resume_conversation_id:
            prompt_parts.append(
                f"Prior conversation available for {target_agent}: {resume_conversation_id}"
            )
        if memory_block:
            prompt_parts.append(memory_block)
        if handoff_md:
            prompt_parts.append(f"Latest Handoff:\n{handoff_md[:1200]}")

        return ContinueContext(
            active_task=task,
            latest_handoff=handoff_md,
            handoff_record=record,
            git_diff_stat=git_state,
            recent_errors=errors,
            next_recommended_action=next_action,
            target_agent=target_agent,
            target_model=target_model,
            prompt="\n\n".join(prompt_parts),
            resume_conversation_id=resume_conversation_id,
            resume_session_id=resume_session_id,
            remaining_work=remaining,
            relevant_memory=[m.memory_id for m in memories],
            source=source,
        )
