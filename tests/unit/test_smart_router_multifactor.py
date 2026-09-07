"""Unit tests for Phase 4D Multi-Factor Intelligent Routing.

Covers multi-factor score breakdown, historical performance weighting,
explainable routing reasons, and routing history logging/retrieval.
"""
import tempfile
import unittest
from pathlib import Path

from brain.router.smart_router import SmartRouter
from models.policies.model_policy import Complexity
from providers.registry.bootstrap import create_default_registry


class TestMultiFactorSmartRouter(unittest.TestCase):
    def setUp(self):
        self.tmp_history = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self.tmp_history.close()
        self.registry = create_default_registry()
        self.router = SmartRouter(self.registry, history_file=Path(self.tmp_history.name))

    def tearDown(self):
        Path(self.tmp_history.name).unlink(missing_ok=True)

    def test_score_breakdown_contains_all_factors(self):
        scores = self.router.score_candidates("Refactor telemetry handler component")
        self.assertTrue(scores)
        top = scores[0]
        self.assertTrue(hasattr(top, "score_breakdown"))
        bd = top.score_breakdown
        self.assertIn("keyword_affinity", bd)
        self.assertIn("capability_match", bd)
        self.assertIn("strength_fit", bd)
        self.assertIn("reliability", bd)
        self.assertIn("latency", bd)
        self.assertIn("token_efficiency", bd)
        self.assertIn("health", bd)
        self.assertIn("load_penalty", bd)
        self.assertAlmostEqual(top.score, sum(bd.values()))

    def test_routing_decision_logged_to_history(self):
        dec = self.router.route("Draft architecture RFC for secure comms")
        history = self.router.get_routing_history(limit=10)
        self.assertEqual(len(history), 1)
        record = history[0]
        self.assertEqual(record["selected_agent"], "antigravity-account-1")
        self.assertIn("architecture", record["task_text"].lower())
        self.assertTrue(record["candidates"])

    def test_explainable_routing_reason(self):
        dec = self.router.route("Refactor telemetry handler component")
        self.assertTrue(dec.reason)
        self.assertTrue(isinstance(dec.reason, str))
        self.assertGreater(len(dec.reason), 5)


if __name__ == "__main__":
    unittest.main()
