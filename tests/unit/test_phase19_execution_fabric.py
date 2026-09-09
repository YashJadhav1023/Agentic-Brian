"""Unit tests for Phase 19 Universal Execution Fabric."""
import os
import tempfile
import unittest
from pathlib import Path

from brain.analytics.performance_registry import PerformanceRegistry
from brain.orchestrator.execution_fabric import ExecutionFabric, ExecutionResult
from brain.planner.plan_model import PlanStep, StepStatus


class TestPhase19ExecutionFabric(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "perf.jsonl"
        self.perf_reg = PerformanceRegistry(log_path=self.log_path)
        self.fabric = ExecutionFabric(performance_registry=self.perf_reg)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_safety_guard_blocks_prohibited_command(self):
        """Test safety guard stops attempts to kill protected PID 5854 or wipe ~/.gemini."""
        step = PlanStep(
            step_id="step-danger",
            title="Dangerous kill",
            command="kill -9 5854",
            risk_level="DESTRUCTIVE",
            requires_approval=False,
        )
        res = self.fabric.execute_step(step)
        self.assertEqual(res.status, "BLOCKED_SAFETY_VIOLATION")
        self.assertIn("Safety violation", res.error)

        # Also test rm -rf ~/.gemini
        res_rm = self.fabric.execute_task("rm -rf ~/.gemini", command="rm -rf ~/.gemini")
        self.assertEqual(res_rm.status, "BLOCKED_SAFETY_VIOLATION")

    def test_approval_gate_blocks_unapproved_step(self):
        """Test unapproved step with requires_approval=True is blocked."""
        step = PlanStep(
            step_id="step-approval",
            title="Delete old database records",
            risk_level="DESTRUCTIVE",
            requires_approval=True,
            approved=False,
        )
        res = self.fabric.execute_step(step)
        self.assertEqual(res.status, "BLOCKED_ON_APPROVAL")
        self.assertEqual(step.status, StepStatus.APPROVAL_REQUIRED)

    def test_dry_run_execution(self):
        """Test dry-run mode returns preview without actual execution."""
        step = PlanStep(
            step_id="step-dry",
            title="Inspect project structure",
            agent="antigravity-account-1",
        )
        res = self.fabric.execute_step(step, dry_run=True)
        self.assertEqual(res.status, "DRY_RUN")
        self.assertIn("[DRY-RUN]", res.output)
        self.assertIn("domain", res.context_summary)

    def test_successful_step_execution_and_telemetry(self):
        """Test successful execution updates step status and records to performance registry."""
        step = PlanStep(
            step_id="step-success",
            title="Run echo test",
            command="echo 'Operation completed successfully'",
            verification_rules=["no_errors"],
        )
        res = self.fabric.execute_step(step)
        self.assertEqual(res.status, "SUCCESS")
        self.assertTrue(res.verification["success"])
        self.assertEqual(step.status, StepStatus.COMPLETED)

        # Check telemetry in performance registry
        summary = self.perf_reg.get_summary()
        self.assertEqual(summary["total_executions"], 1)
        self.assertEqual(summary["success_rate"], 1.0)

    def test_failed_step_with_rollback(self):
        """Test execution failure triggers registered rollback action."""
        temp_file = Path(self.temp_dir.name) / "rollback_target.txt"
        temp_file.write_text("initial state")

        step = PlanStep(
            step_id="step-fail",
            title="Failing command with rollback",
            command="exit 1",
            rollback_action=f"rm -f {temp_file}",
        )
        res = self.fabric.execute_step(step)
        self.assertEqual(res.status, "ROLLED_BACK")
        self.assertTrue(res.rollback_applied)
        self.assertFalse(temp_file.exists())
        self.assertEqual(step.status, StepStatus.ROLLED_BACK)


if __name__ == "__main__":
    unittest.main()
