"""
Unit tests for Explainable Routing and Decision History (Phase 13.7, 13.8).
"""

import unittest
from brain.router.models import RoutingCandidate, RoutingMode
from brain.router.smart_router import SmartRouter, RouterConfig


class TestRoutingExplanations(unittest.TestCase):
    """Test routing decision explanation generation, breakdown transparency, and zero credential leakage."""

    def setUp(self):
        self.router = SmartRouter(config=RouterConfig(mode=RoutingMode.BALANCED))

    def test_explain_decision_transparency(self):
        """Verify explanation clearly presents winning score and factor breakdown."""
        c1 = RoutingCandidate(
            provider_id="cline",
            account_id="cline-account-2",
            model_id="claude-3-5-sonnet",
            capabilities=["coding", "reasoning"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            latency_ms=120.0,
            success_rate=0.98,
            account_priority=90
        )
        c2 = RoutingCandidate(
            provider_id="mock_api",
            account_id="mock-1",
            model_id="mock-model",
            capabilities=["quick_question"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            latency_ms=800.0,
            success_rate=0.80,
            account_priority=40
        )

        decision = self.router.select_best_candidate(
            candidates=[c1, c2],
            required_capabilities=["coding"],
            task_prompt="Implement websocket server in Python",
            mode=RoutingMode.BALANCED,
            job_id="job-test-explain"
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.account_id, "cline-account-2")

        explanation = self.router.explain_decision(decision)
        self.assertIn("cline-account-2", explanation)
        self.assertIn("Total Score", explanation)
        self.assertIn("capability_match", explanation)

    def test_explanation_never_leaks_secrets(self):
        """Verify explanations never contain API keys or auth tokens."""
        c = RoutingCandidate(
            provider_id="openai",
            account_id="openai-account-prod",
            model_id="gpt-4o",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True
        )
        decision = self.router.select_best_candidate(
            candidates=[c],
            required_capabilities=["coding"],
            task_prompt="Review secret auth token and fix vulnerability",
            job_id="job-sec-check"
        )
        explanation = self.router.explain_decision(decision)
        self.assertNotIn("sk-", explanation)
        self.assertNotIn("Bearer ", explanation)
        self.assertNotIn("password", explanation.lower())

    def test_routing_history_persistence_and_inspection(self):
        """Verify decisions are stored in history and can be inspected by job_id."""
        c = RoutingCandidate(
            provider_id="antigravity",
            account_id="antigravity-account-1",
            model_id="gemini-2.0-flash",
            capabilities=["coding", "planning"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True
        )
        decision = self.router.select_best_candidate(
            candidates=[c],
            required_capabilities=["coding"],
            task_prompt="Refactor database schemas",
            job_id="job-inspect-hist-123"
        )
        self.router.record_decision(decision)

        retrieved = self.router.get_decision("job-inspect-hist-123")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.job_id, "job-inspect-hist-123")
        self.assertEqual(retrieved.account_id, "antigravity-account-1")


if __name__ == "__main__":
    unittest.main()
