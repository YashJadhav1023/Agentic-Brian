"""End-to-End Test Suite for Master Optimization Benchmark (Phase 4B - 4I).

Integrates the 7 benchmark tests (Tests A through G) into the standard unittest discovery runner.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.benchmark import MasterBenchmarkSuite

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TestMasterBenchmarkE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.suite = MasterBenchmarkSuite(workspace_dir=PROJECT_ROOT)

    def test_benchmark_a_simple_task_reduction(self) -> None:
        result = self.suite.run_test_a_simple_task()
        self.assertTrue(result.success, f"Test A failed: {result.details}")
        self.assertGreaterEqual(
            result.reduction_percent,
            75.0,
            f"Context reduction {result.reduction_percent}% is below 75% target",
        )
        self.assertIn("README.md", result.details["target_files"])

    def test_benchmark_b_medium_task_scoping(self) -> None:
        result = self.suite.run_test_b_medium_task()
        self.assertTrue(result.success, f"Test B failed: {result.details}")
        self.assertGreater(result.reduction_percent, 50.0)

    def test_benchmark_c_complex_task_heavy_reasoning(self) -> None:
        result = self.suite.run_test_c_complex_task()
        self.assertTrue(result.success, f"Test C failed: {result.details}")
        self.assertTrue(result.details["requires_heavy_slot"])

    def test_benchmark_d_continuation_budget_and_depth(self) -> None:
        result = self.suite.run_test_d_continuation()
        self.assertTrue(result.success, f"Test D failed: {result.details}")
        self.assertEqual(result.details["continuation_depth"], 3)
        self.assertEqual(result.details["continuation_budget"], 2)

    def test_benchmark_e_verification_single_depth_termination(self) -> None:
        result = self.suite.run_test_e_verification()
        self.assertTrue(result.success, f"Test E failed: {result.details}")
        self.assertTrue(result.details["is_terminal"])
        self.assertEqual(result.details["terminal_reason"], "VERIFICATION_COMPLETE")

    def test_benchmark_f_provider_failure_and_failover(self) -> None:
        result = self.suite.run_test_f_failover()
        self.assertTrue(result.success, f"Test F failed: {result.details}")
        self.assertEqual(result.selected_agent, "antigravity-account-2")
        self.assertEqual(result.fallback_count, 1)

    def test_benchmark_g_routing_decision_multifactor(self) -> None:
        result = self.suite.run_test_g_routing_decision()
        self.assertTrue(result.success, f"Test G failed: {result.details}")
        self.assertTrue(result.details["breakdown_logged"])
        self.assertGreater(result.details["num_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
