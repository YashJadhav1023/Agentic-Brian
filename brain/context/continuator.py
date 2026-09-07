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

import json
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
    # --- Phase 4A Continuation Contract ---
    is_terminal: bool = False
    terminal_reason: str | None = None
    task_type: str = "general"
    continuation_depth: int = 0
    max_continuation_depth: int = 5
    verification_depth: int = 0
    max_verification_depth: int = 1
    continuation_budget: int = 5

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
            "is_terminal": self.is_terminal,
            "terminal_reason": self.terminal_reason,
            "task_type": self.task_type,
            "continuation_depth": self.continuation_depth,
            "max_continuation_depth": self.max_continuation_depth,
            "verification_depth": self.verification_depth,
            "max_verification_depth": self.max_verification_depth,
            "continuation_budget": self.continuation_budget,
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

        CANCELLED tasks are deliberately excluded when active work exists — a
        cancelled task is a decision, not unfinished work.
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

        for status, label in (
            (TaskStatus.CANCELLED, "cancelled task"),
            (TaskStatus.VERIFICATION_COMPLETE, "verification complete task"),
            (TaskStatus.DEPTH_LIMIT_REACHED, "depth limit reached task"),
            (TaskStatus.BUDGET_EXHAUSTED, "budget exhausted task"),
            (TaskStatus.REJECTED, "rejected task"),
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

        # Check for malformed handoff
        current_json_file = getattr(self._handoff_manager, "_current_json", None)
        if current_json_file and current_json_file.exists():
            try:
                raw_json = current_json_file.read_text(encoding="utf-8")
                parsed = json.loads(raw_json)
                if not isinstance(parsed, dict) or "task" not in parsed:
                    return ContinueContext(
                        active_task=task,
                        latest_handoff=handoff_md,
                        handoff_record=record,
                        git_diff_stat=git_state,
                        recent_errors=["Malformed handoff JSON structure"],
                        next_recommended_action="Continuation terminated: Malformed handoff record detected.",
                        target_agent="none",
                        target_model="none",
                        prompt="Malformed handoff detected. Continuation safely terminated.",
                        is_terminal=True,
                        terminal_reason="MALFORMED_HANDOFF",
                        source="malformed_handoff",
                    )
            except Exception as e:
                return ContinueContext(
                    active_task=task,
                    latest_handoff=handoff_md,
                    handoff_record=record,
                    git_diff_stat=git_state,
                    recent_errors=[f"Corrupted handoff JSON: {e}"],
                    next_recommended_action="Continuation terminated: Malformed handoff record detected.",
                    target_agent="none",
                    target_model="none",
                    prompt="Corrupted handoff JSON. Continuation safely terminated.",
                    is_terminal=True,
                    terminal_reason="MALFORMED_HANDOFF",
                    source="malformed_handoff",
                )

        # Check if cancelled task
        if task and task.status == TaskStatus.CANCELLED:
            return ContinueContext(
                active_task=task,
                latest_handoff=handoff_md,
                handoff_record=record,
                git_diff_stat=git_state,
                recent_errors=list(task.errors) if task.errors else [],
                next_recommended_action=f"Continuation terminated: Task {task.task_id} was cancelled.",
                target_agent="none",
                target_model="none",
                prompt=f"Task {task.task_id} was cancelled. No continuation available.",
                is_terminal=True,
                terminal_reason="CANCELLED",
                source=source,
            )

        # Determine terminal conditions from task and handoff
        is_terminal = False
        terminal_reason: str | None = None

        if task and task.status in (
            TaskStatus.VERIFICATION_COMPLETE,
            TaskStatus.DEPTH_LIMIT_REACHED,
            TaskStatus.BUDGET_EXHAUSTED,
            TaskStatus.REJECTED,
        ):
            is_terminal = True
            terminal_reason = task.terminal_reason or task.status.value
        elif task and task.is_terminal:
            is_terminal = True
            terminal_reason = task.terminal_reason or "TASK_TERMINAL"
        elif record and record.is_terminal:
            is_terminal = True
            terminal_reason = record.terminal_reason or "HANDOFF_TERMINAL"
        elif task and task.continuation_budget <= 0:
            is_terminal = True
            terminal_reason = "BUDGET_EXHAUSTED"
        elif task and task.continuation_depth >= task.max_continuation_depth:
            is_terminal = True
            terminal_reason = "DEPTH_LIMIT_REACHED"
        elif task and task.is_verification and (
            task.status in (TaskStatus.COMPLETED, TaskStatus.VERIFICATION_COMPLETE)
            or task.verification_depth >= task.max_verification_depth
        ):
            is_terminal = True
            terminal_reason = "VERIFICATION_COMPLETE"
        elif task and task.status == TaskStatus.COMPLETED:
            # Check if verification has already been created/completed for this task
            existing_verif = [
                t for t in self._task_manager.list_tasks()
                if t.verification_for == task.task_id
                or (t.task_type == "verification" and task.task_id in t.description)
            ]
            if existing_verif:
                if any(v.is_terminal_state for v in existing_verif):
                    is_terminal = True
                    terminal_reason = "VERIFICATION_COMPLETE"
            elif task.verification_depth >= task.max_verification_depth:
                is_terminal = True
                terminal_reason = "COMPLETED"

        # Determine continuation metadata
        task_type = "general"
        continuation_depth = 0
        max_continuation_depth = 5
        verification_depth = 0
        max_verification_depth = 1
        continuation_budget = 5

        if task:
            task_type = task.task_type
            continuation_depth = task.continuation_depth
            max_continuation_depth = task.max_continuation_depth
            verification_depth = task.verification_depth
            max_verification_depth = task.max_verification_depth
            continuation_budget = task.continuation_budget
        elif record:
            task_type = record.task_type
            continuation_depth = record.continuation_depth
            max_continuation_depth = record.max_continuation_depth
            verification_depth = record.verification_depth
            max_verification_depth = record.max_verification_depth
            continuation_budget = record.continuation_budget

        errors: list[str] = list(task.errors) if task and task.errors else []
        if record and record.errors:
            errors.extend(e for e in record.errors if e and e != "None")

        remaining = list(record.remaining_work) if record and record.remaining_work else []

        target_agent = "antigravity-account-1"
        target_model = "auto"
        handoff_matches_task = bool(
            record and task and record.task_id and record.task_id == task.task_id
        )
        task_needs_recovery = bool(
            task and task.status in (TaskStatus.FAILED, TaskStatus.BLOCKED) and not is_terminal
        )

        if is_terminal:
            next_action = f"Continuation terminated: {terminal_reason}"
            target_agent = "none"
            target_model = "none"
            prompt = f"Continuation terminated: {terminal_reason}. Workflow is complete."
            return ContinueContext(
                active_task=task,
                latest_handoff=handoff_md,
                handoff_record=record,
                git_diff_stat=git_state,
                recent_errors=errors,
                next_recommended_action=next_action,
                target_agent=target_agent,
                target_model=target_model,
                prompt=prompt,
                resume_conversation_id=None,
                resume_session_id=None,
                remaining_work=[],
                relevant_memory=[],
                source=source,
                is_terminal=True,
                terminal_reason=terminal_reason,
                task_type=task_type,
                continuation_depth=continuation_depth,
                max_continuation_depth=max_continuation_depth,
                verification_depth=verification_depth,
                max_verification_depth=max_verification_depth,
                continuation_budget=continuation_budget,
            )

        # Decide the next action and next agent with standard precedence
        if handoff_matches_task:
            next_action = record.next_action or f"Continue work on {task.title}"
            target_agent = record.recommended_agent or target_agent
            target_model = record.recommended_model or "auto"
            source = f"{source} + handoff"
            continuation_depth += 1
            continuation_budget = max(0, continuation_budget - 1)
        elif task_needs_recovery:
            last_error = (task.errors[-1] if task.errors else "unknown error").splitlines()[0]
            next_action = (
                f"Resolve the failure of task {task.task_id} ('{task.title}'): {last_error}"
            )
            target_agent = task.assigned_agent or target_agent
            target_model = task.assigned_model or "auto"
            source = f"{source} (recovery)"
            continuation_depth += 1
            continuation_budget = max(0, continuation_budget - 1)
        elif record and record.next_action:
            next_action = record.next_action
            target_agent = record.recommended_agent or target_agent
            target_model = record.recommended_model or "auto"
            source = f"{source} + handoff"
            continuation_depth += 1
            continuation_budget = max(0, continuation_budget - 1)
        elif task:
            next_action = f"Continue work on {task.title}: {task.description}"
            target_agent = task.assigned_agent or target_agent
            target_model = task.assigned_model or "auto"
            continuation_depth += 1
            continuation_budget = max(0, continuation_budget - 1)
        else:
            next_action = "Review system status and proceed with planned items."

        # Determine task type and verification depth
        next_task_type = "general"
        if "Verify" in next_action or "Review diff" in next_action:
            next_task_type = "verification"
            verification_depth += 1
        elif "Remediate" in next_action or (task and task.is_verification and task_needs_recovery):
            next_task_type = "remediation"

        # Resolve a resumable conversation
        resume_conversation_id = None
        resume_session_id = None
        session = None
        if task:
            session = self._session_manager.get_by_task(task.task_id)
        if session is None:
            session = self._session_manager.get_latest_for_agent(target_agent)
        if session:
            resume_session_id = session.session_id
            if session.agent_id == target_agent:
                resume_conversation_id = session.conversation_id
        if record and record.conversation_id and record.agent_id == target_agent:
            resume_conversation_id = resume_conversation_id or record.conversation_id

        # Relevant memory only
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
            is_terminal=False,
            terminal_reason=None,
            task_type=next_task_type,
            continuation_depth=continuation_depth,
            max_continuation_depth=max_continuation_depth,
            verification_depth=verification_depth,
            max_verification_depth=max_verification_depth,
            continuation_budget=continuation_budget,
        )
