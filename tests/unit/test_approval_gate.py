"""BUG-002 Regression Tests: Approval Gate for Destructive Queued Tasks.

Full-system validation (Phase 1-21) discovered that instructions containing
destructive verbs could be dispatched through plan_and_dispatch and executed by
the swarm queue without any operator approval, even though the Phase 18 planner
correctly flagged the *plan* as BLOCKED_ON_APPROVAL.

The fix stamps `requires_approval` on the queued Task at the
plan_and_dispatch chokepoint and enforces it in SwarmWorkerPool.run_queue and
SwarmWorkerPool.execute_task. Orchestrator.approve_task/reject_task provide the
explicit operator release path.

These tests run entirely in temporary directories with mocked adapters: no live
agent, repository file, or GUI component is touched.
"""
from __future__ import annotations

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
from brain.orchestrator.orchestrator import (
    DESTRUCTIVE_INSTRUCTION_PATTERN,
    Orchestrator,
)
from brain.worktree.worktree_manager import WorktreeManager
from events.bus import EventBus
from handoffs.handoff_manager import HandoffManager
from memory.store.memory_store import MemoryStore
from providers.registry.provider_registry import ProviderRegistry
from sessions.session_manager import SessionManager
from tasks.manager import TaskManager, TaskStatus

DESTRUCTIVE_SAMPLE = "Delete all build artifacts and purge the stale cache tables"
BENIGN_SAMPLE = "Inspect the repository structure and summarize the top-level directories"


class _NoopAdapter(AgentAdapter):
    """Adapter that records invocations and never actually runs anything."""

    def __init__(self, agent_id: str, provider: str, account_id: str) -> None:
        self._agent_id = agent_id
        self._provider = provider
        self._account_id = account_id
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
        return frozenset({Capability.TERMINAL_OPERATIONS})

    def available_models(self) -> tuple[str, ...]:
        return ("mock-model",)

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
        self.invocations.append({"task_id": task_id, "prompt": prompt})
        return TaskExecutionResult(
            task_id=task_id,
            agent_id=self._agent_id,
            account_id=self._account_id,
            provider=self._provider,
            success=True,
            exit_code=0,
            output="mock execution",
            error="",
            actual_model="mock-model",
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

class TestApprovalGate(unittest.TestCase):
    """Regression tests for the BUG-002 destructive-task approval gate."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tm = TaskManager(root_tasks_dir=self.root / "tasks")
        self.eb = EventBus(log_path=self.root / "events.jsonl")
        self.reg = ProviderRegistry()
        self.adapter = _NoopAdapter("antigravity-account-1", "antigravity", "account-1")
        self.reg.register_adapter("antigravity", self.adapter)
        self.orch = Orchestrator(
            task_manager=self.tm,
            registry=self.reg,
            event_bus=self.eb,
            workspace_dir=self.root,
            handoff_manager=HandoffManager(root_dir=self.root / "handoffs"),
            session_manager=SessionManager(registry_file=self.root / "sessions.json"),
            memory_store=MemoryStore(db_path=self.root / "memory.db"),
            worktree_manager=WorktreeManager(canonical_repo=self.root),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Gate stamping at dispatch
    # ------------------------------------------------------------------
    def test_01_destructive_instruction_is_flagged_at_dispatch(self) -> None:
        task = self.orch.plan_and_dispatch(DESTRUCTIVE_SAMPLE)
        self.assertTrue(task.requires_approval)
        self.assertIsNotNone(task.approval_reason)
        stored = self.tm.get_task(task.task_id)
        self.assertIsNotNone(stored)
        self.assertTrue(stored.requires_approval, "gate flag must survive persistence")

    def test_02_benign_instruction_is_not_flagged(self) -> None:
        task = self.orch.plan_and_dispatch(BENIGN_SAMPLE)
        self.assertFalse(task.requires_approval)
        self.assertIsNone(task.approval_reason)

    def test_03_destructive_pattern_matches_all_documented_verbs(self) -> None:
        for verb in ("delete", "destroy", "drop", "purge", "rm -rf", "truncate", "erase", "wipe", "kill"):
            self.assertIsNotNone(
                DESTRUCTIVE_INSTRUCTION_PATTERN.search(f"please {verb} the thing"),
                f"pattern must match '{verb}'",
            )
        self.assertIsNone(DESTRUCTIVE_INSTRUCTION_PATTERN.search(BENIGN_SAMPLE))
    # ------------------------------------------------------------------
    # Queue pickup enforcement (swarm.run_queue)
    # ------------------------------------------------------------------
    def test_04_run_queue_never_executes_gated_task(self) -> None:
        task = self.orch.plan_and_dispatch(DESTRUCTIVE_SAMPLE)
        results = self.orch.execute_next()
        self.assertEqual(results, [], "gated task must not be executed by run_queue")
        fresh = self.tm.get_task(task.task_id)
        self.assertEqual(fresh.status, TaskStatus.BLOCKED)
        self.assertEqual(fresh.stage, "BLOCKED_ON_APPROVAL")
        self.assertEqual(self.adapter.invocations, [], "adapter must not be invoked")

    def test_05_run_queue_still_executes_benign_tasks(self) -> None:
        task = self.orch.plan_and_dispatch(BENIGN_SAMPLE)
        results = self.orch.execute_next()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        fresh = self.tm.get_task(task.task_id)
        self.assertEqual(fresh.status, TaskStatus.COMPLETED)

    # ------------------------------------------------------------------
    # Direct execution enforcement (swarm.execute_task)
    # ------------------------------------------------------------------
    def test_06_execute_task_refuses_gated_task(self) -> None:
        task = self.orch.plan_and_dispatch(DESTRUCTIVE_SAMPLE)
        result = self.orch.execute_task_now(task)
        self.assertFalse(result.success)
        self.assertIn("BLOCKED_ON_APPROVAL", result.error)
        self.assertEqual(self.adapter.invocations, [], "adapter must not be invoked")
        fresh = self.tm.get_task(task.task_id)
        self.assertEqual(fresh.status, TaskStatus.BLOCKED)
    # ------------------------------------------------------------------
    # Explicit approval / rejection workflow
    # ------------------------------------------------------------------
    def test_07_approval_releases_task_to_queue(self) -> None:
        task = self.orch.plan_and_dispatch(DESTRUCTIVE_SAMPLE)
        approved = self.orch.approve_task(task.task_id, approver="test-operator")
        self.assertIsNotNone(approved)
        self.assertFalse(approved.requires_approval)
        self.assertEqual(approved.status, TaskStatus.READY)
        # After approval the swarm may execute it.
        results = self.orch.execute_next()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertEqual(len(self.adapter.invocations), 1)

    def test_08_rejection_terminates_task_without_execution(self) -> None:
        task = self.orch.plan_and_dispatch(DESTRUCTIVE_SAMPLE)
        rejected = self.orch.reject_task(task.task_id, reason="validation test")
        self.assertIsNotNone(rejected)
        self.assertEqual(rejected.status, TaskStatus.REJECTED)
        self.assertTrue(rejected.is_terminal_state)
        self.assertEqual(self.adapter.invocations, [])
        results = self.orch.execute_next()
        self.assertEqual(results, [], "rejected task must never execute")

    def test_09_approval_of_ungated_task_is_a_noop(self) -> None:
        task = self.orch.plan_and_dispatch(BENIGN_SAMPLE)
        approved = self.orch.approve_task(task.task_id)
        self.assertIsNotNone(approved)
        self.assertFalse(approved.requires_approval)
        self.assertEqual(approved.status, TaskStatus.READY)

    def test_10_approve_and_reject_unknown_ids_return_none(self) -> None:
        self.assertIsNone(self.orch.approve_task("task-does-not-exist"))
        self.assertIsNone(self.orch.reject_task("task-does-not-exist"))


if __name__ == "__main__":
    unittest.main()