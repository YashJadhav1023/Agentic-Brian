"""
Unit tests for Provider-Independent Usage Tracker (Phase 14.1).
"""

import unittest
import tempfile
import os
from brain.analytics.usage_tracker import UsageTracker, UsageRecord


class TestUsageTracker(unittest.TestCase):
    """Test usage recording across providers, accounts, models, and temporal windows."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp_dir, "usage.jsonl")
        self.tracker = UsageTracker(storage_file=self.log_file)

    def tearDown(self):
        if os.path.exists(self.log_file):
            os.remove(self.log_file)
        os.rmdir(self.tmp_dir)

    def test_record_and_aggregate_usage(self):
        """Test recording usage records and verifying aggregated metrics."""
        rec1 = UsageRecord(
            job_id="job-1",
            provider_id="openai",
            account_id="openai-account-1",
            model_id="gpt-4o",
            success=True,
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            latency_ms=850.0,
            estimated_cost_usd=0.015
        )
        rec2 = UsageRecord(
            job_id="job-2",
            provider_id="cline",
            account_id="cline-account-2",
            model_id="claude-3-5-sonnet",
            success=True,
            input_tokens=2000,
            output_tokens=1000,
            total_tokens=3000,
            latency_ms=1200.0,
            estimated_cost_usd=0.021
        )
        rec3 = UsageRecord(
            job_id="job-3",
            provider_id="openai",
            account_id="openai-account-1",
            model_id="gpt-4o",
            success=False,
            input_tokens=500,
            output_tokens=0,
            total_tokens=500,
            latency_ms=300.0,
            estimated_cost_usd=0.0025,
            error_type="RateLimitError",
            retries=1,
            failovers=1
        )

        self.tracker.record_usage(rec1)
        self.tracker.record_usage(rec2)
        self.tracker.record_usage(rec3)

        summary = self.tracker.get_summary()
        self.assertEqual(summary["requests"], 3)
        self.assertEqual(summary["successful_requests"], 2)
        self.assertEqual(summary["failed_requests"], 1)
        self.assertEqual(summary["input_tokens"], 3500)
        self.assertEqual(summary["output_tokens"], 1500)
        self.assertEqual(summary["total_tokens"], 5000)
        self.assertEqual(summary["retries"], 1)
        self.assertEqual(summary["failovers"], 1)

    def test_group_by_provider(self):
        """Test aggregation grouped by provider."""
        self.tracker.record_usage(UsageRecord(
            job_id="j1", provider_id="prov_a", account_id="a1", model_id="m1",
            input_tokens=100, output_tokens=50, total_tokens=150
        ))
        self.tracker.record_usage(UsageRecord(
            job_id="j2", provider_id="prov_b", account_id="b1", model_id="m2",
            input_tokens=200, output_tokens=100, total_tokens=300
        ))

        by_prov = self.tracker.get_usage_by_provider()
        self.assertIn("prov_a", by_prov)
        self.assertIn("prov_b", by_prov)
        self.assertEqual(by_prov["prov_a"]["total_tokens"], 150)
        self.assertEqual(by_prov["prov_b"]["total_tokens"], 300)

    def test_group_by_account(self):
        """Test aggregation grouped by account."""
        self.tracker.record_usage(UsageRecord(
            job_id="j1", provider_id="cline", account_id="cline-account-1", model_id="m",
            total_tokens=1000
        ))
        self.tracker.record_usage(UsageRecord(
            job_id="j2", provider_id="cline", account_id="cline-account-2", model_id="m",
            total_tokens=2000
        ))
        by_acct = self.tracker.get_usage_by_account()
        self.assertEqual(by_acct["cline-account-1"]["total_tokens"], 1000)
        self.assertEqual(by_acct["cline-account-2"]["total_tokens"], 2000)


if __name__ == "__main__":
    unittest.main()
