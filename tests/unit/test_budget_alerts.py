"""
Unit tests for Budget Alerts and Threshold Notifications (Phase 14.5).
"""

import unittest
import tempfile
import os
from brain.analytics.quota_manager import QuotaManager, QuotaRule, QuotaScope


class TestBudgetAlerts(unittest.TestCase):
    """Test 50%, 75%, 90%, and 100% threshold detection and alerting."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.storage_file = os.path.join(self.tmp_dir, "alerts.json")
        self.manager = QuotaManager(storage_file=self.storage_file)

    def tearDown(self):
        if os.path.exists(self.storage_file):
            os.remove(self.storage_file)
        os.rmdir(self.tmp_dir)

    def test_alert_thresholds_50_75_90_100(self):
        """Verify alerts are triggered at 50%, 75%, 90%, and 100% budget usage."""
        rule = QuotaRule(
            scope=QuotaScope.ACCOUNT,
            target_id="test-budget-account",
            cost_limit_usd=100.0
        )
        self.manager.set_quota(rule)

        # 40% -> No alert
        self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="test-budget-account", cost_usd=40.0)
        alerts = self.manager.get_active_alerts()
        self.assertEqual(len(alerts), 0)

        # 55% -> 50% threshold crossed
        self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="test-budget-account", cost_usd=15.0)
        status = self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="test-budget-account")
        self.assertEqual(status.alert_threshold, 50)

        # 78% -> 75% threshold crossed
        self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="test-budget-account", cost_usd=23.0)
        status = self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="test-budget-account")
        self.assertEqual(status.alert_threshold, 75)

        # 92% -> 90% threshold crossed
        self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="test-budget-account", cost_usd=14.0)
        status = self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="test-budget-account")
        self.assertEqual(status.alert_threshold, 90)

        # 102% -> 100% threshold crossed
        self.manager.record_usage(scope=QuotaScope.ACCOUNT, target_id="test-budget-account", cost_usd=10.0)
        status = self.manager.check_quota(scope=QuotaScope.ACCOUNT, target_id="test-budget-account")
        self.assertEqual(status.alert_threshold, 100)
        self.assertTrue(status.is_exhausted)


if __name__ == "__main__":
    unittest.main()
