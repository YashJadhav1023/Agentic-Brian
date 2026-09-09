"""Phase 10: usage tracking.

Covers the USAGE TRACKING section of the brief. Two negative guarantees matter
as much as the positive ones:

* prompt contents are not stored
* credentials are not stored
"""
from __future__ import annotations

import inspect
import json
import unittest

from brain.orchestrator.job import Job
from brain.orchestrator.job_manager import UsageTracker
from providers.base import RateLimitInfo, UsageStats

FAKE_SECRET = "sk-usage-tracking-test-secret-0123456789"
PROMPT_TEXT = "Refactor the billing module and remove the deprecated tax helper"


class TestUsageRecordShape(unittest.TestCase):
    """Each usage record must carry every dimension the brief enumerates."""

    REQUIRED_DIMENSIONS = (
        "provider_id",
        "account_id",
        "model",
        "job_id",
        "timestamp",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_usd",
    )

    def setUp(self) -> None:
        self.tracker = UsageTracker()
        self.tracker.record(
            job_id="job-1",
            provider_id="openai",
            account_id="openai-personal",
            model="gpt-4o",
            prompt_tokens=120,
            completion_tokens=80,
            cost_usd=0.0031,
        )

    def _record(self) -> dict:
        return self.tracker._records[0]

    def test_record_contains_every_required_dimension(self):
        record = self._record()
        for dimension in self.REQUIRED_DIMENSIONS:
            with self.subTest(dimension=dimension):
                self.assertIn(dimension, record)

    def test_total_tokens_is_derived_when_not_supplied(self):
        self.assertEqual(self._record()["total_tokens"], 200)

    def test_explicit_total_tokens_is_respected(self):
        tracker = UsageTracker()
        tracker.record(
            job_id="job-2",
            provider_id="openai",
            account_id="a",
            model="gpt-4o",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=99,
        )
        self.assertEqual(tracker._records[0]["total_tokens"], 99)

    def test_timestamp_is_recorded(self):
        self.assertTrue(self._record()["timestamp"])

    def test_zero_token_usage_is_still_recorded(self):
        """Providers that report no usage must not silently vanish from billing."""
        tracker = UsageTracker()
        tracker.record(job_id="j", provider_id="p", account_id="a", model="m")
        self.assertEqual(len(tracker._records), 1)
        self.assertEqual(tracker._records[0]["total_tokens"], 0)


class TestUsageAggregation(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = UsageTracker()
        self.tracker.record(
            job_id="j1",
            provider_id="openai",
            account_id="openai-personal",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.001,
        )
        self.tracker.record(
            job_id="j2",
            provider_id="openai",
            account_id="openai-work",
            model="gpt-4o",
            prompt_tokens=200,
            completion_tokens=100,
            cost_usd=0.002,
        )
        self.tracker.record(
            job_id="j3",
            provider_id="anthropic",
            account_id="anthropic-work",
            model="claude-3-5-sonnet",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.0005,
        )

    def test_global_summary_aggregates_every_record(self):
        summary = self.tracker.get_summary()
        self.assertEqual(summary["requests"], 3)
        self.assertEqual(summary["total_tokens"], 465)
        self.assertAlmostEqual(summary["estimated_cost_usd"], 0.0035, places=6)

    def test_summary_can_be_scoped_to_a_provider(self):
        summary = self.tracker.get_summary(provider_id="openai")
        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["total_tokens"], 450)

    def test_summary_can_be_scoped_to_an_account(self):
        summary = self.tracker.get_summary(account_id="openai-work")
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(summary["total_tokens"], 300)

    def test_summary_for_an_unknown_scope_is_zeroed_not_an_error(self):
        summary = self.tracker.get_summary(provider_id="does-not-exist")
        self.assertEqual(summary["requests"], 0)
        self.assertEqual(summary["total_tokens"], 0)
        self.assertEqual(summary["estimated_cost_usd"], 0)

    def test_an_empty_tracker_reports_zeroes(self):
        summary = UsageTracker().get_summary()
        self.assertEqual(summary["requests"], 0)
        self.assertEqual(summary["total_tokens"], 0)


class TestUsageStoresNoSensitiveData(unittest.TestCase):
    def test_prompt_content_is_not_stored_in_a_usage_record(self):
        tracker = UsageTracker()
        tracker.record(
            job_id="j",
            provider_id="openai",
            account_id="a",
            model="gpt-4o",
            prompt_tokens=10,
        )
        blob = json.dumps(tracker._records)
        self.assertNotIn(PROMPT_TEXT, blob)
        for prompt_field in ("prompt", "messages", "input", "content", "task"):
            self.assertNotIn(f'"{prompt_field}"', blob)

    def test_the_record_signature_accepts_no_prompt_argument(self):
        """Usage tracking cannot leak a prompt it has no way to receive."""
        params = set(inspect.signature(UsageTracker.record).parameters)
        for forbidden in ("prompt", "messages", "content", "text", "task"):
            self.assertNotIn(forbidden, params)

    def test_the_record_signature_accepts_no_credential_argument(self):
        params = set(inspect.signature(UsageTracker.record).parameters)
        for forbidden in ("api_key", "secret", "token", "credential"):
            self.assertNotIn(forbidden, params)

    def test_no_credential_appears_in_stored_usage(self):
        tracker = UsageTracker()
        tracker.record(job_id="j", provider_id="openai", account_id="a", model="gpt-4o")
        self.assertNotIn(FAKE_SECRET, json.dumps(tracker._records))


class TestProviderLevelUsageCounters(unittest.TestCase):
    def test_usage_stats_serialises_token_counters(self):
        stats = UsageStats()
        stats.request_count += 1
        stats.input_tokens += 10
        stats.output_tokens += 5
        stats.total_tokens += 15
        payload = stats.to_dict()
        self.assertEqual(payload["request_count"], 1)
        self.assertEqual(payload["total_tokens"], 15)

    def test_rate_limit_info_serialises(self):
        self.assertIsInstance(RateLimitInfo().to_dict(), dict)


class TestJobLevelUsageLinkage(unittest.TestCase):
    """A usage record must be attributable to a job, provider, account and model."""

    def test_job_carries_the_attribution_fields_usage_needs(self):
        job = Job(
            provider="openai",
            account="openai-personal",
            model="gpt-4o",
            task=PROMPT_TEXT,
        )
        for field_name in ("id", "provider", "account", "model", "duration", "status"):
            self.assertTrue(hasattr(job, field_name))

    def test_a_completed_job_records_duration_and_success_state(self):
        job = Job(provider="openai", account="a", model="gpt-4o", task=PROMPT_TEXT)
        job.mark_started()
        job.mark_completed(result={"output": "done"}, duration=1.25)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.duration, 1.25)

    def test_a_failed_job_records_duration_and_failure_state(self):
        job = Job(provider="openai", account="a", model="gpt-4o", task=PROMPT_TEXT)
        job.mark_started()
        job.mark_failed("HTTP 429: Too Many Requests", duration=0.5)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.duration, 0.5)
        self.assertIn("429", job.error)


if __name__ == "__main__":
    unittest.main()
