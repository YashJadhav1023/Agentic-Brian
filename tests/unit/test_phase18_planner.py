"""Unit tests for Phase 18: Autonomous Task Planner and Approval Gates."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.planner.plan_model import PlanStatus, StepStatus
from brain.planner.planner import Planner


class TestPhase18Planner(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.planner = Planner(storage_dir=Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_plan_generation_and_approval_gate(self):
        # Destructive task requires approval
        plan = self.planner.create_plan("Delete stale azure resources")
        self.assertTrue(plan.requires_approval)
        self.assertEqual(plan.status, PlanStatus.BLOCKED_ON_APPROVAL)

        # Attempt execution before approval
        res = self.planner.execute_plan(plan.plan_id)
        self.assertEqual(res["status"], "BLOCKED_ON_APPROVAL")

        # Approve and execute
        ok = self.planner.approve_plan(plan.plan_id)
        self.assertTrue(ok)
        self.assertEqual(plan.status, PlanStatus.READY)

        res2 = self.planner.execute_plan(plan.plan_id)
        self.assertEqual(res2["status"], "COMPLETED")
        self.assertEqual(res2["total_steps"], len(plan.steps))

    def test_read_only_task_auto_ready(self):
        plan = self.planner.create_plan("Read project documentation and inspect architecture")
        self.assertFalse(plan.requires_approval)
        self.assertEqual(plan.status, PlanStatus.READY)

        res = self.planner.execute_plan(plan.plan_id)
        self.assertEqual(res["status"], "COMPLETED")

    def test_dry_run_mode(self):
        plan = self.planner.create_plan("Deploy services to AKS cluster", dry_run=True)
        self.assertTrue(plan.is_dry_run)

        # Remove approval requirement for dry run test
        self.planner.approve_plan(plan.plan_id)
        res = self.planner.execute_plan(plan.plan_id)
        self.assertEqual(res["status"], "DRY_RUN_COMPLETED")
        self.assertIn("steps_preview", res)


if __name__ == "__main__":
    unittest.main()
