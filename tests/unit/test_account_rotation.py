"""
Unit tests for Intelligent Account Rotation (Phase 13.5).
"""

import unittest
from brain.router.models import RoutingCandidate, RoutingMode
from brain.router.smart_router import SmartRouter, RouterConfig


class TestAccountRotation(unittest.TestCase):
    """Test multi-factor account selection and avoiding cooldown/exhausted accounts."""

    def setUp(self):
        self.router = SmartRouter(config=RouterConfig(mode=RoutingMode.BALANCED))

    def test_never_selects_cooldown_account(self):
        """Account-2 is in cooldown, router must select account-1 or account-3."""
        c1 = RoutingCandidate(
            provider_id="cline",
            account_id="cline-account-1",
            model_id="claude-3-5-sonnet",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            cooldown_active=False
        )
        c2 = RoutingCandidate(
            provider_id="cline",
            account_id="cline-account-2",
            model_id="claude-3-5-sonnet",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            cooldown_active=True,  # Cooldown!
            account_priority=100   # Even with higher priority!
        )
        c3 = RoutingCandidate(
            provider_id="cline",
            account_id="cline-account-3",
            model_id="claude-3-5-sonnet",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            cooldown_active=False
        )

        decision = self.router.select_best_candidate(
            candidates=[c1, c2, c3],
            required_capabilities=["coding"],
            task_prompt="Implement fast sorting function"
        )
        self.assertNotEqual(decision.account_id, "cline-account-2")
        self.assertIn(decision.account_id, ["cline-account-1", "cline-account-3"])

    def test_never_selects_exhausted_quota_account(self):
        """Account with zero remaining quota must not be chosen over one with ample quota."""
        c_exhausted = RoutingCandidate(
            provider_id="openai",
            account_id="openai-account-1",
            model_id="gpt-4o",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            quota_remaining=0  # Exhausted!
        )
        c_available = RoutingCandidate(
            provider_id="openai",
            account_id="openai-account-2",
            model_id="gpt-4o",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            quota_remaining=500000
        )

        decision = self.router.select_best_candidate(
            candidates=[c_exhausted, c_available],
            required_capabilities=["coding"],
            task_prompt="Refactor CSS styles"
        )
        self.assertEqual(decision.account_id, "openai-account-2")

    def test_penalizes_high_failure_account(self):
        """Account with recent consecutive failures receives severe penalty."""
        c_clean = RoutingCandidate(
            provider_id="agent",
            account_id="acct-clean",
            model_id="m1",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            failure_rate=0.0
        )
        c_failing = RoutingCandidate(
            provider_id="agent",
            account_id="acct-failing",
            model_id="m1",
            capabilities=["coding"],
            provider_healthy=True,
            account_healthy=True,
            model_healthy=True,
            failure_rate=0.60
        )

        decision = self.router.select_best_candidate(
            candidates=[c_clean, c_failing],
            required_capabilities=["coding"],
            task_prompt="Run unit tests"
        )
        self.assertEqual(decision.account_id, "acct-clean")


if __name__ == "__main__":
    unittest.main()
