"""
Unit tests for Intelligent Multi-Factor Routing Engine (Phase 13).
"""

import unittest
from brain.router.classification import TaskClassifier, TaskDomain
from brain.router.models import RoutingCandidate, RoutingScore, RoutingMode
from brain.router.smart_router import SmartRouter, RouterConfig


class TestRoutingEngine(unittest.TestCase):
    """Test task classification, 16-factor scoring, modes, and candidate selection."""

    def setUp(self):
        self.classifier = TaskClassifier()
        self.router = SmartRouter(config=RouterConfig(mode=RoutingMode.BALANCED))

    def test_task_classification_domains(self):
        """Verify deterministic classification into 10 required domains."""
        tests = [
            ("Write a python script to parse logs", TaskDomain.CODING),
            ("Fix null pointer exception and memory leak", TaskDomain.DEBUGGING),
            ("Design multi-tier microservice architecture", TaskDomain.ARCHITECTURE),
            ("Deploy docker container to kubernetes cluster", TaskDomain.DEVOPS),
            ("Write API documentation and readme guide", TaskDomain.DOCUMENTATION),
            ("Compare academic literature on transformer attention", TaskDomain.RESEARCH),
            ("Prove that P!=NP using mathematical logic", TaskDomain.REASONING),
            ("What is the capital of France?", TaskDomain.QUICK_QUESTION),
            ("Analyze this entire 500-page book repository", TaskDomain.LONG_CONTEXT),
            ("Run cron job and auto-sync nightly backup", TaskDomain.AUTOMATION)
        ]
        for prompt, expected_domain in tests:
            result = self.classifier.classify(prompt)
            self.assertEqual(result.primary_domain, expected_domain, f"Failed on prompt: {prompt}")

    def test_scoring_uses_capability_match_field(self):
        """Verify the exact field name capability_match is present and capability_overlap is NOT used."""
        c = RoutingCandidate(
            provider_id="test-provider",
            account_id="test-account",
            model_id="test-model",
            capabilities=["coding", "reasoning"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            latency_ms=250.0,
            success_rate=0.99,
            failure_rate=0.01,
            account_priority=80,
            provider_priority=70,
            model_priority=85,
            quota_remaining=100000,
            estimated_cost_per_1m=5.0
        )
        score = self.router.score_candidate(
            c,
            required_capabilities=["coding"],
            mode=RoutingMode.BALANCED,
            context_tokens=1000
        )
        # MUST use capability_match, NOT capability_overlap
        self.assertIn("capability_match", score.breakdown)
        self.assertNotIn("capability_overlap", score.breakdown)
        self.assertGreater(score.capability_match, 0)
        self.assertEqual(score.total_score, sum(score.breakdown.values()))

    def test_all_16_factors_evaluated(self):
        """Verify all 16 multi-dimensional factors are scored."""
        c = RoutingCandidate(
            provider_id="prov1",
            account_id="acct1",
            model_id="mod1",
            capabilities=["coding", "streaming", "tools"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            latency_ms=100.0,
            success_rate=1.0,
            failure_rate=0.0,
            account_priority=50,
            provider_priority=50,
            model_priority=50,
            quota_remaining=500000,
            estimated_cost_per_1m=2.0
        )
        score = self.router.score_candidate(
            c,
            required_capabilities=["coding"],
            mode=RoutingMode.BALANCED,
            context_tokens=2000,
            require_streaming=True,
            require_tools=True
        )
        expected_factors = [
            "capability_match", "provider_health", "account_health", "model_health",
            "latency", "recent_success_rate", "failure_rate", "account_priority",
            "provider_priority", "model_priority", "quota_remaining", "estimated_cost",
            "task_complexity", "context_size", "streaming_support", "tool_support"
        ]
        for factor in expected_factors:
            self.assertIn(factor, score.breakdown, f"Missing factor {factor}")

    def test_routing_modes(self):
        """Verify routing modes change scoring weights appropriately."""
        c_fast_costly = RoutingCandidate(
            provider_id="fast_api",
            account_id="fast_1",
            model_id="fast-mod",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            latency_ms=50.0,
            success_rate=0.99,
            failure_rate=0.01,
            estimated_cost_per_1m=30.0
        )
        c_slow_cheap = RoutingCandidate(
            provider_id="cheap_local",
            account_id="cheap_1",
            model_id="cheap-mod",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            latency_ms=1200.0,
            success_rate=0.99,
            failure_rate=0.01,
            estimated_cost_per_1m=0.0
        )

        # In PERFORMANCE mode, fast candidate should get higher latency score
        score_perf_fast = self.router.score_candidate(c_fast_costly, ["coding"], mode=RoutingMode.PERFORMANCE)
        score_perf_slow = self.router.score_candidate(c_slow_cheap, ["coding"], mode=RoutingMode.PERFORMANCE)
        self.assertGreater(score_perf_fast.breakdown["latency"], score_perf_slow.breakdown["latency"])

        # In COST mode, cheap candidate should get higher cost score
        score_cost_fast = self.router.score_candidate(c_fast_costly, ["coding"], mode=RoutingMode.COST)
        score_cost_cheap = self.router.score_candidate(c_slow_cheap, ["coding"], mode=RoutingMode.COST)
        self.assertGreater(score_cost_cheap.breakdown["estimated_cost"], score_cost_fast.breakdown["estimated_cost"])


if __name__ == "__main__":
    unittest.main()
