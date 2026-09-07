"""Phase 4A Unit Tests: Continuation Contract & Verification Loop Prevention.

Tests all 15 required scenarios:
1. Normal task completes without verification recursion.
2. Verification executes once.
3. Verification cannot verify itself indefinitely.
4. Max verification depth terminates.
5. Max continuation depth terminates.
6. Continuation budget exhaustion terminates.
7. Verification failure can create bounded remediation.
8. Remediation cannot create infinite verification.
9. Continue resumes an incomplete task.
10. Continue on terminal task does not create new work.
11. Duplicate verification is rejected/prevented.
12. Malformed handoff terminates safely.
13. Cancelled task cannot continue.
14. Handoff record schema compatibility with continuation metadata.
15. Sandbox worktree compatibility under continuation contract.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents.base.adapter import (
    AgentAdapter,
    Capability,
    ExecutionMode,
    TaskExecutionResult,
)
from brain.context.continuator import UniversalContinuator
from brain.orchestrator.orchestrator import Orchestrator
from brain.orchestrator.swarm import SwarmWorkerPool
from brain.worktree.worktree_manager import WorktreeManager
from events.bus import EventBus, EventType
from handoffs.handoff_manager import HandoffManager, HandoffRecord
from memory.store.memory_store import MemoryStore
from providers.registry.provider_registry import ProviderRegistry
from sessions.session_manager import SessionManager
from tasks.manager import Task, TaskManager, TaskPriority, TaskStatus


class MockAgentAdapter(AgentAdapter):
    """Configurable test adapter that records invocations and returns simulated results."""

    def __init__(
        self,
        agent_id: str = "antigravity-account-1",
        provider: str = "antigravity",
        account_id: str = "account-1",
        success: bool = True,
        output: str = "Task executed successfully",
        actual_model: str = "gemini-3.7-flash-high",
    ) -> None:
        self._agent_id = agent_id
        self._provider = provider
        self._account_id = account_id
        self.success = success
        self.output = output
        self.actual_model = actual_model
        self.invocations: list[dict[str, Any]] = []

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.HEADLESS

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({
            Capability.TERMINAL_OPERATIONS,
            Capability.BUILD_AND_TEST,
            Capability.ARCHITECTURE,
            Capability.DEEP_REASONING,
            Capability.LOCAL_VALIDATION,
            Capability.CODE_REVIEW,
            Capability.DOCUMENTATION,
        })

    def available_models(self) -> tuple[str, ...]:
        return ("gemini-3.7-flash-high", "gemini-3.8-flash-low")

    def health(self) -> tuple[bool, str]:
        return True, "healthy"

    def execute(
        self,
        task_id: str,
        prompt: str,
        model: str | None = None,
        work_dir: Path | None = None,
        timeout_seconds: int = 300,
        options: dict[str, Any] | None = None,
    ) -> TaskExecutionResult:
        self.invocations.append({
            "task_id": task_id,
            "prompt": prompt,
            "model": model,
            "work_dir": work_dir,
            "options": options,
        })
        return TaskExecutionResult(
            task_id=task_id,
            agent_id=self._agent_id,
            account_id=self._account_id,
            provider=self._provider,
            success=self.success,
            exit_code=0 if self.success else 1,
            output=self.output if self.success else "",
            error="" if self.success else "Simulated adapter failure",
            actual_model=self.actual_model,
            raw_response={"status": "SUCCESS" if self.success else "FAILED"},
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


def create_mock_registry(success: bool = True) -> ProviderRegistry:
    reg = ProviderRegistry()
    for agent_id, provider, account in (
        ("antigravity-account-1", "antigravity", "account-1"),
        ("antigravity-account-2", "antigravity", "account-2"),
        ("kiro-cli", "kiro", "cli"),
        ("cline", "cline", "cli"),
    ):
        adapter = MockAgentAdapter(agent_id=agent_id, provider=provider, account_id=account, success=success)
        reg.register_adapter(provider, adapter)
    return reg


class TestContinuationContract(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tm = TaskManager(root_tasks_dir=self.root / "tasks")
        self.hm = HandoffManager(root_dir=self.root / "handoffs")
        self.sm = SessionManager(registry_file=self.root / "sessions.json")
        self.store = MemoryStore(db_path=self.root / "memory.db")
        self.eb = EventBus()
        self.reg = create_mock_registry(success=True)
        self.wm = WorktreeManager(canonical_repo=self.root)
        self.orch = Orchestrator(
            task_manager=self.tm,
            registry=self.reg,
            event_bus=self.eb,
            workspace_dir=self.root,
            handoff_manager=self.hm,
            session_manager=self.sm,
            memory_store=self.store,
            worktree_manager=self.wm,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # 1. Normal task completes without verification recursion
    # ------------------------------------------------------------------
    def test_01_normal_task_completes_without_verification_recursion(self) -> None:
        task = self.orch.plan_and_dispatch("Implement feature authentication")
        self.assertEqual(task.task_type, "general")
        self.assertEqual(task.verification_depth, 0)
        self.assertEqual(task.continuation_depth, 0)

        # Execute normal task
        result = self.orch.execute_task_now(task)
        self.assertTrue(result.success)
        fresh = self.tm.get_task(task.task_id)
        self.assertEqual(fresh.status, TaskStatus.COMPLETED)
        self.assertFalse(fresh.is_terminal)

        # Now continue -> should dispatch verification
        ctx, v_result = self.orch.continue_work()
        self.assertFalse(ctx.is_terminal)
        self.assertEqual(ctx.task_type, "verification")
        self.assertTrue(v_result.success)

        # Verification task MUST be VERIFICATION_COMPLETE and terminal
        v_tasks = [t for t in self.tm.list_tasks() if t.is_verification]
        self.assertEqual(len(v_tasks), 1)
        v_task = v_tasks[0]
        self.assertEqual(v_task.status, TaskStatus.VERIFICATION_COMPLETE)
        self.assertTrue(v_task.is_terminal)

        # Continuing again MUST terminate immediately without creating tasks
        tasks_before = len(self.tm.list_tasks())
        next_ctx, next_result = self.orch.continue_work()
        self.assertTrue(next_ctx.is_terminal)
        self.assertIsNone(next_result)
        self.assertEqual(len(self.tm.list_tasks()), tasks_before)

    # ------------------------------------------------------------------
    # 2. Verification executes once
    # ------------------------------------------------------------------
    def test_02_verification_executes_once(self) -> None:
        v_task = self.tm.create_task(
            title="Verify output of task-1234",
            description="Verify output of task-1234",
            task_type="verification",
            verification_for="task-1234",
            verification_depth=1,
            max_verification_depth=1,
        )
        events_emitted: list[str] = []
        self.eb.subscribe(lambda e: events_emitted.append(e.event_type.value))

        result = self.orch.execute_task_now(v_task)
        self.assertTrue(result.success)

        fresh = self.tm.get_task(v_task.task_id)
        self.assertEqual(fresh.status, TaskStatus.VERIFICATION_COMPLETE)
        self.assertTrue(fresh.is_terminal)
        self.assertIn("VERIFICATION_COMPLETED", events_emitted)
        self.assertIn("CONTINUATION_TERMINATED", events_emitted)

        ctx = self.orch.build_continue_context()
        self.assertTrue(ctx.is_terminal)
        self.assertEqual(ctx.terminal_reason, "VERIFICATION_COMPLETE")

    # ------------------------------------------------------------------
    # 3. Verification cannot verify itself indefinitely
    # ------------------------------------------------------------------
    def test_03_verification_cannot_verify_itself_indefinitely(self) -> None:
        v1 = self.tm.create_task(
            title="Verify output of task-initial",
            description="Verify output of task-initial",
            task_type="verification",
            verification_for="task-initial",
        )
        self.tm.update_status(v1.task_id, TaskStatus.VERIFICATION_COMPLETE)

        events: list[str] = []
        self.eb.subscribe(lambda e: events.append(e.event_type.value))

        # Attempt to create verification OF verification
        v2 = self.orch.plan_and_dispatch(f"Verify output of task {v1.task_id}")
        self.assertEqual(v2.status, TaskStatus.REJECTED)
        self.assertTrue(v2.is_terminal)
        self.assertEqual(v2.terminal_reason, "RECURSION_PREVENTED")
        self.assertIn("RECURSION_PREVENTED", events)
        self.assertIn("VERIFICATION_REJECTED", events)

    # ------------------------------------------------------------------
    # 4. Max verification depth terminates
    # ------------------------------------------------------------------
    def test_04_max_verification_depth_terminates(self) -> None:
        events: list[str] = []
        self.eb.subscribe(lambda e: events.append(e.event_type.value))

        task = self.orch.plan_and_dispatch(
            "Verify deep task",
            task_type="verification",
            verification_for="task-target",
            verification_depth=2,
            max_verification_depth=1,
        )
        self.assertEqual(task.status, TaskStatus.DEPTH_LIMIT_REACHED)
        self.assertTrue(task.is_terminal)
        self.assertIn("DEPTH_LIMIT_REACHED", events)

    # ------------------------------------------------------------------
    # 5. Max continuation depth terminates
    # ------------------------------------------------------------------
    def test_05_max_continuation_depth_terminates(self) -> None:
        events: list[str] = []
        self.eb.subscribe(lambda e: events.append(e.event_type.value))

        task = self.orch.plan_and_dispatch(
            "Deep recursive chained continuation",
            continuation_depth=6,
            max_continuation_depth=5,
        )
        self.assertEqual(task.status, TaskStatus.DEPTH_LIMIT_REACHED)
        self.assertTrue(task.is_terminal)
        self.assertIn("DEPTH_LIMIT_REACHED", events)

    # ------------------------------------------------------------------
    # 6. Continuation budget exhaustion terminates
    # ------------------------------------------------------------------
    def test_06_continuation_budget_exhaustion_terminates(self) -> None:
        events: list[str] = []
        self.eb.subscribe(lambda e: events.append(e.event_type.value))

        task = self.orch.plan_and_dispatch(
            "Budget exhausted action",
            continuation_budget=0,
        )
        self.assertEqual(task.status, TaskStatus.BUDGET_EXHAUSTED)
        self.assertTrue(task.is_terminal)
        self.assertIn("BUDGET_EXHAUSTED", events)

    # ------------------------------------------------------------------
    # 7. Verification failure can create bounded remediation
    # ------------------------------------------------------------------
    def test_07_verification_failure_can_create_bounded_remediation(self) -> None:
        # Create failing registry
        fail_reg = create_mock_registry(success=False)
        orch = Orchestrator(
            task_manager=self.tm,
            registry=fail_reg,
            event_bus=self.eb,
            workspace_dir=self.root,
            handoff_manager=self.hm,
            session_manager=self.sm,
            memory_store=self.store,
            worktree_manager=self.wm,
        )

        v_task = self.tm.create_task(
            title="Verify output of task-100",
            description="Verify output of task-100",
            task_type="verification",
            verification_for="task-100",
            continuation_depth=1,
            continuation_budget=3,
            max_continuation_depth=5,
        )
        result = orch.execute_task_now(v_task)
        self.assertFalse(result.success)

        # A failed verification with remaining budget creates a remediation handoff
        rec = self.hm.get_current_record()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.task_type, "remediation")
        self.assertFalse(rec.is_terminal)
        self.assertEqual(rec.continuation_budget, 2)
        self.assertEqual(rec.continuation_depth, 2)

    # ------------------------------------------------------------------
    # 8. Remediation cannot create infinite verification
    # ------------------------------------------------------------------
    def test_08_remediation_cannot_create_infinite_verification(self) -> None:
        remed_task = self.tm.create_task(
            title="Remediate failure in task-100",
            description="Remediate failure",
            task_type="remediation",
            verification_depth=1,
            max_verification_depth=1,
            continuation_depth=2,
            continuation_budget=2,
            max_continuation_depth=5,
        )
        result = self.orch.execute_task_now(remed_task)
        self.assertTrue(result.success)

        # Because verification_depth (1) >= max_verification_depth (1), it must NOT recommend another verification!
        rec = self.hm.get_current_record()
        self.assertIsNotNone(rec)
        self.assertTrue(rec.is_terminal)
        self.assertEqual(rec.recommended_agent, "none")
        self.assertEqual(rec.remaining_work, [])

        # Continuator recognizes completion
        ctx = self.orch.build_continue_context()
        self.assertTrue(ctx.is_terminal)

    # ------------------------------------------------------------------
    # 9. Continue resumes an incomplete task
    # ------------------------------------------------------------------
    def test_09_continue_resumes_an_incomplete_task(self) -> None:
        task = self.tm.create_task(
            title="Incomplete pending job",
            description="Job waiting in queue",
        )
        ctx = self.orch.build_continue_context()
        self.assertFalse(ctx.is_terminal)
        self.assertEqual(ctx.active_task.task_id, task.task_id)

    # ------------------------------------------------------------------
    # 10. Continue on terminal task does not create new work
    # ------------------------------------------------------------------
    def test_10_continue_on_terminal_task_does_not_create_new_work(self) -> None:
        task = self.tm.create_task(
            title="Task terminal",
            description="Terminal completed task",
            is_terminal=True,
            terminal_reason="VERIFICATION_COMPLETE",
        )
        self.tm.update_status(task.task_id, TaskStatus.VERIFICATION_COMPLETE)

        ctx = self.orch.build_continue_context()
        self.assertTrue(ctx.is_terminal)

        count_before = len(self.tm.list_tasks())
        res_ctx, res_output = self.orch.continue_work()
        self.assertTrue(res_ctx.is_terminal)
        self.assertIsNone(res_output)
        self.assertEqual(len(self.tm.list_tasks()), count_before)

    # ------------------------------------------------------------------
    # 11. Duplicate verification is rejected/prevented
    # ------------------------------------------------------------------
    def test_11_duplicate_verification_is_rejected_or_prevented(self) -> None:
        t_base = self.tm.create_task(title="Base task", description="Base task")
        v1 = self.orch.plan_and_dispatch(
            f"Verify output of task {t_base.task_id}",
            verification_for=t_base.task_id,
        )
        self.assertEqual(v1.task_type, "verification")
        self.assertFalse(v1.is_terminal)

        # Attempt to dispatch duplicate verification for same base task
        v2 = self.orch.plan_and_dispatch(
            f"Verify output of task {t_base.task_id}",
            verification_for=t_base.task_id,
        )
        self.assertEqual(v2.status, TaskStatus.REJECTED)
        self.assertTrue(v2.is_terminal)
        self.assertEqual(v2.terminal_reason, "DUPLICATE_VERIFICATION")

    # ------------------------------------------------------------------
    # 12. Malformed handoff terminates safely
    # ------------------------------------------------------------------
    def test_12_malformed_handoff_terminates_safely(self) -> None:
        json_file = self.root / "handoffs" / "current.json"
        json_file.parent.mkdir(parents=True, exist_ok=True)
        json_file.write_text("{corrupt json:::invalid format", encoding="utf-8")

        ctx = self.orch.build_continue_context()
        self.assertTrue(ctx.is_terminal)
        self.assertEqual(ctx.terminal_reason, "MALFORMED_HANDOFF")

    # ------------------------------------------------------------------
    # 13. Cancelled task cannot continue
    # ------------------------------------------------------------------
    def test_13_cancelled_task_cannot_continue(self) -> None:
        task = self.tm.create_task(title="Cancelled task", description="Cancelled")
        self.tm.update_status(task.task_id, TaskStatus.CANCELLED)

        ctx = self.orch.build_continue_context()
        self.assertTrue(ctx.is_terminal)
        self.assertEqual(ctx.terminal_reason, "CANCELLED")

        next_ctx, result = self.orch.continue_work()
        self.assertTrue(next_ctx.is_terminal)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # 14. Handoff record schema compatibility with continuation metadata
    # ------------------------------------------------------------------
    def test_14_existing_handoff_tests_contract_compatible(self) -> None:
        record = HandoffRecord(
            task="Test task",
            objective="Verify metadata serialization",
            task_type="verification",
            continuation_depth=2,
            max_continuation_depth=5,
            verification_depth=1,
            max_verification_depth=1,
            continuation_budget=4,
            is_terminal=True,
            terminal_reason="VERIFICATION_COMPLETE",
            verification_for="task-prev",
        )
        path = self.hm.write_handoff(record)
        self.assertTrue(path.exists())

        loaded = self.hm.get_current_record()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.task_type, "verification")
        self.assertEqual(loaded.continuation_depth, 2)
        self.assertEqual(loaded.verification_depth, 1)
        self.assertEqual(loaded.continuation_budget, 4)
        self.assertTrue(loaded.is_terminal)
        self.assertEqual(loaded.terminal_reason, "VERIFICATION_COMPLETE")
        self.assertEqual(loaded.verification_for, "task-prev")

        # Markdown format contains Phase 4A metadata fields
        md = self.hm.get_current_handoff()
        self.assertIn("**Task Type:** `verification`", md)
        self.assertIn("**Continuation Depth:** `2 / 5`", md)
        self.assertIn("**Verification Depth:** `1 / 1`", md)
        self.assertIn("**Terminal:** `True` (VERIFICATION_COMPLETE)", md)

    # ------------------------------------------------------------------
    # 15. Sandbox worktree compatibility under continuation contract
    # ------------------------------------------------------------------
    def test_15_existing_phase3_sandbox_contract_preserved(self) -> None:
        task = self.orch.plan_and_dispatch(
            "Write new controller module",
            files=["controllers/test.py"],
            execution_options={"is_mutating": True},
        )
        self.assertEqual(task.continuation_depth, 0)
        self.assertEqual(task.verification_depth, 0)
        self.assertFalse(task.is_terminal)


if __name__ == "__main__":
    unittest.main()
