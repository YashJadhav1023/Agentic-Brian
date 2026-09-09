"""
Regression tests for the full-system validation BUG-002 fix: the dispatch-time
approval gate for destructive instructions.

Root cause: `Orchestrator.plan_and_dispatch()` queued destructive instructions
directly into the shared swarm queue, where `run_queue()` executed them without
any approval check, even though the Phase 18 Planner correctly showed
BLOCKED_ON_APPROVAL for the same instruction.

Fix: destructive instructions are stamped with requires_approval at dispatch,
the swarm refuses them at queue pickup and at direct execution, and the state
can only be released via Orchestrator.approve_task().

These tests use an isolated temp workspace and NEVER invoke real agents:
only the gate paths (which return before any adapter call) are exercised.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from brain.orchestrator.orchestrator import Orchestrator
from tasks.manager import TaskStatus


class TestDestructiveApprovalGate(unittest.TestCase):
    """Destructive instructions must never auto-execute from the swarm queue."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="mc-gate-regression-"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.orch = Orchestrator(workspace_dir=self._tmp)

    def test_destructive_instruction_is_gated_at_dispatch(self):
        task = self.orch.plan_and_dispatch(
            "Delete all build artifacts and purge the stale database tables"
        )
        self.assertTrue(task.requires_approval)
        self.assertIsNotNone(task.approval_reason)

    def test_safe_instruction_is_not_gated(self):
        task = self.orch.plan_and_dispatch(
            "Inspect the repository structure and summarize the top-level directories"
        )
        self.assertFalse(task.requires_approval)
        self.assertIsNone(task.approval_reason)

    def test_queue_run_refuses_gated_task_without_execution(self):
        task = self.orch.plan_and_dispatch("Wipe the cache directory completely")
        results = self.orch.execute_next()
        self.assertEqual(results, [])
        fresh = self.orch.tasks.get_task(task.task_id)
        self.assertEqual(fresh.status, TaskStatus.BLOCKED)
        self.assertEqual(fresh.stage, "BLOCKED_ON_APPROVAL")
        self.assertTrue(fresh.requires_approval)

    def test_pending_approval_tasks_lists_gated_tasks_in_both_states(self):
        task = self.orch.plan_and_dispatch("Erase the local telemetry logs")
        # READY state (freshly stamped, queue not yet inspected)
        pending_ready = [t.task_id for t in self.orch.pending_approval_tasks()]
        self.assertIn(task.task_id, pending_ready)
        # BLOCKED state (after swarm pickup refusal)
        self.orch.execute_next()
        pending_blocked = [t.task_id for t in self.orch.pending_approval_tasks()]
        self.assertIn(task.task_id, pending_blocked)

    def test_direct_execution_gate_blocks_without_adapter_call(self):
        task = self.orch.plan_and_dispatch("Purge every deployment record")
        result = self.orch.swarm.execute_task(task)
        self.assertFalse(result.success)
        self.assertIn("BLOCKED_ON_APPROVAL", result.error)
        self.assertEqual(result.provider, "approval-gate")
        fresh = self.orch.tasks.get_task(task.task_id)
        self.assertEqual(fresh.status, TaskStatus.BLOCKED)

    def test_approve_task_releases_gate_without_executing(self):
        task = self.orch.plan_and_dispatch("Delete outdated report drafts")
        self.orch.execute_next()  # flips to BLOCKED_ON_APPROVAL
        approved = self.orch.approve_task(task.task_id, approver="regression-test")
        self.assertIsNotNone(approved)
        self.assertFalse(approved.requires_approval)
        self.assertEqual(approved.status, TaskStatus.READY)

    def test_reject_task_terminates_gated_task(self):
        task = self.orch.plan_and_dispatch("Drop the entire staging index")
        rejected = self.orch.reject_task(task.task_id, reason="regression test")
        self.assertEqual(rejected.status, TaskStatus.REJECTED)
        self.assertTrue(rejected.is_terminal)
        pending = [t.task_id for t in self.orch.pending_approval_tasks()]
        self.assertNotIn(task.task_id, pending)


if __name__ == "__main__":
    unittest.main()
