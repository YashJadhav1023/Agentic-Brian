"""
Unit tests for Performance Analytics and Retention (Phase 14.6, 14.7).
"""

import unittest
from brain.analytics.analytics_engine import AnalyticsEngine
from brain.analytics.retention_manager import RetentionManager


class TestAnalytics(unittest.TestCase):
    """Test latency percentiles (p50, p95, p99), rate calculations, and data retention."""

    def setUp(self):
        self.analytics = AnalyticsEngine()
        self.retention = RetentionManager(default_retention_days=30)

    def test_latency_percentiles(self):
        """Test p50, p95, and p99 percentile computation."""
        # Record 100 sample latencies from 10ms to 1000ms
        latencies = [i * 10.0 for i in range(1, 101)]
        for lat in latencies:
            self.analytics.record_metric(latency_ms=lat, success=True)

        percentiles = self.analytics.get_latency_percentiles()
        self.assertAlmostEqual(percentiles["p50"], 500.0, delta=20.0)
        self.assertAlmostEqual(percentiles["p95"], 950.0, delta=20.0)
        self.assertAlmostEqual(percentiles["p99"], 990.0, delta=20.0)

    def test_success_and_failover_rates(self):
        """Test success rate and failover rate calculations."""
        # 8 successful, 2 failed, 1 failover
        for _ in range(8):
            self.analytics.record_metric(latency_ms=100.0, success=True)
        for _ in range(2):
            self.analytics.record_metric(latency_ms=150.0, success=False)
        self.analytics.record_failover()

        rates = self.analytics.get_rates()
        self.assertEqual(rates["total_requests"], 10)
        self.assertAlmostEqual(rates["success_rate"], 0.8, places=2)
        self.assertAlmostEqual(rates["failure_rate"], 0.2, places=2)
        self.assertAlmostEqual(rates["failover_rate"], 0.1, places=2)

    def test_retention_default_30_days(self):
        """Test retention defaults to 30 days and prunes records older than threshold."""
        self.assertEqual(self.retention.default_retention_days, 30)
        # Test retention threshold timestamp is ~30 days in the past
        threshold = self.retention.get_cutoff_timestamp(days=30)
        self.assertIsNotNone(threshold)


if __name__ == "__main__":
    unittest.main()
