"""Unit tests for Phase 18: ExecutionGraph DAG and VerificationEngine."""
from __future__ import annotations

import unittest
from brain.planner.plan_model import ExecutionGraph, PlanStep, StepStatus
from brain.planner.verification_engine import RollbackManager, VerificationEngine


class TestPhase18ExecutionGraph(unittest.TestCase):
    def test_topological_sort(self):
        s1 = PlanStep(step_id="s1", title="Step 1", dependencies=[])
        s2 = PlanStep(step_id="s2", title="Step 2", dependencies=["s1"])
        s3 = PlanStep(step_id="s3", title="Step 3", dependencies=["s2"])

        graph = ExecutionGraph([s3, s1, s2])
        order = graph.get_topological_order()
        self.assertEqual([s.step_id for s in order], ["s1", "s2", "s3"])

    def test_cycle_detection(self):
        s1 = PlanStep(step_id="s1", title="Step 1", dependencies=["s2"])
        s2 = PlanStep(step_id="s2", title="Step 2", dependencies=["s1"])

        with self.assertRaises(ValueError):
            ExecutionGraph([s1, s2])

    def test_ready_steps(self):
        s1 = PlanStep(step_id="s1", title="Step 1", dependencies=[], status=StepStatus.PENDING)
        s2 = PlanStep(step_id="s2", title="Step 2", dependencies=["s1"], status=StepStatus.PENDING)

        graph = ExecutionGraph([s1, s2])
        ready = graph.get_ready_steps()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].step_id, "s1")

        # After s1 completes, s2 is ready
        s1.status = StepStatus.COMPLETED
        ready2 = graph.get_ready_steps()
        self.assertEqual(len(ready2), 1)
        self.assertEqual(ready2[0].step_id, "s2")


class TestPhase18VerificationAndRollback(unittest.TestCase):
    def test_verification_success_and_failure(self):
        # Exit code 0 check
        res_ok = VerificationEngine.verify_step(command_returncode=0, output="All good")
        self.assertTrue(res_ok.success)

        res_fail = VerificationEngine.verify_step(command_returncode=1, output="Fatal error")
        self.assertFalse(res_fail.success)

        # Rule check
        res_rule_fail = VerificationEngine.verify_step(output="Error: connection refused", rules=["no_errors"])
        self.assertFalse(res_rule_fail.success)

    def test_rollback_manager_lifo(self):
        mgr = RollbackManager()
        # Register echo commands as safe mock rollback actions
        mgr.register_rollback("s1", "echo rolled back s1")
        mgr.register_rollback("s2", "echo rolled back s2")

        results = mgr.execute_rollback()
        self.assertEqual(len(results), 2)
        # LIFO order: s2 first, then s1
        self.assertEqual(results[0]["step_id"], "s2")
        self.assertEqual(results[1]["step_id"], "s1")
        self.assertTrue(results[0]["success"])
        self.assertTrue(results[1]["success"])


if __name__ == "__main__":
    unittest.main()
