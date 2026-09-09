"""
Unit tests for Quota Manager and Rate/Limit Enforcement (Phase 14.3).
"""

import unittest
import tempfile
import os
from brain.analytics.quota_manager import QuotaManager, QuotaRule, QuotaScope


class TestQuotaManager(unittest.TestCase):
    """Test quota setting, usage accumulation, compliance checks, and exhaustion handling."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.storage_file = os.path.join(self.tmp_dir, "quotas.json")
        self.manager = QuotaManager(storage_file=self.storage_file)

    def tearDown(self):
        if os.path.exists(self.storage_file):
            os.remove(self.storage_file)
        os.rmdir(self.tmp_dir)

    def test_daily_token_quota_exhaustion(self):
        """When daily token quota is exhausted, is_available must return False."""
        rule = QuotaRule(
            scope=QuotaScope.ACCOUNT,
            target_id="openai-account-1",
            daily_token_limit=1000000
        )
        self.manager.set_quota(rule)

        # Record 750,000 used -> still available
        self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="openai-account-1", tokens=750000)
        status = self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="openai-account-1")
        self.assertTrue(status.is_available)
        self.assertEqual(status.remaining_daily_tokens, 250000)

        # Record another 300,000 -> exceeds 1,000,000 -> exhausted
        self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="openai-account-1", tokens=300000)
        status = self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="openai-account-1")
        self.assertFalse(status.is_available)
        self.assertTrue(status.is_exhausted)
        self.assertEqual(status.remaining_daily_tokens, 0)

    def test_daily_request_quota_exhaustion(self):
        """When daily requests exceed limit, is_available must return False."""
        rule = QuotaRule(
            scope=QuotaScope.PROVIDER,
            target_id="test-provider",
            daily_request_limit=10
        )
        self.manager.set_quota(rule)

        for _ in range(10):
            self.manager.record_usage(scope=QuotaScope.PROVIDER, target_id="test-provider", requests=1)

        status = self.manager.check_quota(scope=QuotaScope.PROVIDER, target_id="test-provider")
        self.assertFalse(status.is_available)

    def test_cost_limit_exhaustion(self):
        """When monthly cost limit is reached, account must be marked exhausted."""
        rule = QuotaRule(
            scope=QuotaScope.ACCOUNT,
            target_id="costly-account",
            cost_limit_usd=50.0
        )
        self.manager.set_quota(rule)

        self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="costly-account", cost_usd=51.20)
        status = self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="costly-account")
        self.assertFalse(status.is_available)

    def test_quota_reset(self):
        """Test resetting quota counters to restore availability."""
        rule = QuotaRule(
            scope=QuotaScope.ACCOUNT,
            target_id="reset-account",
            daily_request_limit=5
        )
        self.manager.set_quota(rule)
        for _ in range(5):
            self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="reset-account", requests=1)
        self.assertFalse(self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="reset-account").is_available)

        # Reset
        self.manager.reset_counters()
        self.assertTrue(self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="reset-account").is_available)


if __name__ == "__main__":
    unittest.main()
