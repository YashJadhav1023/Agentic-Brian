"""Unit tests for Phase 19 PerformanceRegistry, Dynamic Routing Weights, and Explainability."""
import tempfile
import unittest
from pathlib import Path

from brain.analytics.performance_registry import ExecutionRecord, PerformanceRegistry
from brain.router.smart_router import SmartRouter
from providers.registry.bootstrap import create_default_registry


class TestPhase19PerformanceAndLearning(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "perf.jsonl"
        self.perf_reg = PerformanceRegistry(log_path=self.log_path)
        self.prov_reg = create_default_registry()
        self.router = SmartRouter(
            self.prov_reg,
            performance_registry=self.perf_reg,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_performance_registry_recording_and_summary(self):
        """Test recording telemetry records and generating aggregate summary."""
        rec1 = ExecutionRecord(
            record_id="rec-001",
            task="Refactor user model",
            domain="coding",
            agent_id="antigravity-account-2",
            model="gemini-3.8-flash-medium",
            provider="antigravity",
            account="account-2",
            success=True,
            latency_seconds=1.5,
            tokens_used=450,
            estimated_cost=0.0012,
        )
        rec2 = ExecutionRecord(
            record_id="rec-002",
            task="Run unit tests",
            domain="testing",
            agent_id="kiro-cli",
            model="auto",
            provider="kiro",
            account="kiro-account-1",
            success=True,
            latency_seconds=3.0,
            tokens_used=800,
            estimated_cost=0.0020,
        )
        rec3 = ExecutionRecord(
            record_id="rec-003",
            task="Failed compilation",
            domain="coding",
            agent_id="cline",
            model="deepseek",
            provider="cline",
            account="cline-account",
            success=False,
            latency_seconds=0.5,
            tokens_used=100,
            estimated_cost=0.0002,
        )

        self.perf_reg.record_execution(rec1)
        self.perf_reg.record_execution(rec2)
        self.perf_reg.record_execution(rec3)

        summary = self.perf_reg.get_summary()
        self.assertEqual(summary["total_executions"], 3)
        self.assertEqual(round(summary["success_rate"], 2), 0.67)
        self.assertEqual(summary["total_tokens_consumed"], 1350)
        self.assertGreater(summary["total_estimated_cost_usd"], 0.0)

    def test_agent_affinity_boost(self):
        """Test empirical affinity boost calculation based on historical track record."""
        # 0 or 1 record should be neutral (0.0) due to cold-start threshold (< 2)
        self.assertEqual(self.perf_reg.get_agent_affinity_boost("antigravity-account-2", "coding"), 0.0)

        # 2 successful executions in coding -> 100% success rate -> positive boost (+2.0)
        for i in range(2):
            self.perf_reg.record_execution(ExecutionRecord(
                record_id=f"rec-{i}",
                task="task",
                domain="coding",
                agent_id="antigravity-account-2",
                model="gemini",
                provider="antigravity",
                account="acc-2",
                success=True,
            ))
        boost = self.perf_reg.get_agent_affinity_boost("antigravity-account-2", "coding")
        self.assertEqual(boost, 2.0)

        # 2 failed executions in coding for cline -> 0% success rate -> negative penalty (-2.0)
        for i in range(2):
            self.perf_reg.record_execution(ExecutionRecord(
                record_id=f"rec-fail-{i}",
                task="task",
                domain="coding",
                agent_id="cline",
                model="deepseek",
                provider="cline",
                account="cline-acc",
                success=False,
            ))
        penalty = self.perf_reg.get_agent_affinity_boost("cline", "coding")
        self.assertEqual(penalty, -2.0)

    def test_explain_routing(self):
        """Test explain_routing produces comprehensive, verifiable output structure."""
        expl = self.router.explain_routing("Refactor the auth components and modernize modules")
        self.assertIn("selected_agent", expl)
        self.assertIn("selected_provider", expl)
        self.assertIn("selected_model", expl)
        self.assertIn("total_score", expl)
        self.assertIn("reason", expl)
        self.assertIn("recommended_mcps", expl)
        self.assertIn("recommended_tools", expl)
        self.assertIn("recommended_knowledge", expl)
        self.assertIn("fallback_chain", expl)
        self.assertIn("candidate_scores", expl)
        self.assertEqual(expl["selected_agent"], "antigravity-account-2")


if __name__ == "__main__":
    unittest.main()
