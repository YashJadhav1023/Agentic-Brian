"""Phase 10: rate limiting, cooldown and backoff.

Covers requirement 12 of the Phase 10 test matrix and the RATE LIMIT / COOLDOWN
section of the brief:

* a rate-limited account is marked `RATE_LIMITED` and given a `cooldown_until`
* the next job routes to a different healthy account
* a failed account is not hammered while in cooldown
* after cooldown the account becomes eligible again
"""
from __future__ import annotations

import time
import unittest

from providers.registry.account_registry import (
    Account,
    AccountPool,
    AccountRegistry,
    AccountStatus,
    AuthenticationType,
)


def _account(account_id: str, priority: int = 10, **kwargs) -> Account:
    defaults = dict(
        id=account_id,
        provider_id="openai",
        account_name=account_id,
        authentication_type=AuthenticationType.API_KEY,
        credential_reference=f"secret://mission-control/openai/{account_id}",
        status=AccountStatus.ONLINE,
        enabled=True,
        priority=priority,
    )
    defaults.update(kwargs)
    return Account(**defaults)


class TestRateLimitMarking(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = AccountPool("openai")
        self.pool.add_account(_account("openai-a", priority=100))

    def test_rate_limit_sets_the_rate_limited_state(self):
        self.pool.record_failure("openai-a", is_rate_limit=True)
        self.assertIs(
            self.pool.get_account("openai-a").status, AccountStatus.RATE_LIMITED
        )

    def test_rate_limit_records_a_cooldown_deadline_in_the_future(self):
        self.pool.record_failure("openai-a", is_rate_limit=True, cooldown_seconds=30.0)
        cooldown_until = self.pool.get_account("openai-a").cooldown_until
        self.assertIsNotNone(cooldown_until)
        self.assertGreater(cooldown_until, time.time())

    def test_a_rate_limited_account_is_not_available(self):
        self.pool.record_failure("openai-a", is_rate_limit=True)
        self.assertFalse(self.pool.get_account("openai-a").is_available())

    def test_rate_limiting_is_inferred_from_the_error_text(self):
        """A 429 or quota message must be classified without an explicit flag."""
        for message in (
            "HTTP 429: Too Many Requests",
            "rate limit exceeded for this key",
            "monthly QUOTA exhausted",
        ):
            with self.subTest(error=message):
                pool = AccountPool("openai")
                pool.add_account(_account("openai-x"))
                pool.record_failure("openai-x", error=message)
                self.assertIs(
                    pool.get_account("openai-x").status, AccountStatus.RATE_LIMITED
                )

    def test_an_ordinary_failure_is_not_classified_as_a_rate_limit(self):
        self.pool.record_failure("openai-a", error="connection reset by peer")
        self.assertIsNot(
            self.pool.get_account("openai-a").status, AccountStatus.RATE_LIMITED
        )

    def test_recording_failure_for_an_unknown_account_is_a_no_op(self):
        self.pool.record_failure("ghost", is_rate_limit=True)  # must not raise


class TestCooldownBehaviour(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = AccountPool("openai")
        self.pool.add_account(_account("openai-a", priority=100))
        self.pool.add_account(_account("openai-b", priority=50))

    def test_next_job_routes_to_another_healthy_account(self):
        self.pool.record_failure("openai-a", is_rate_limit=True)
        self.assertEqual(self.pool.get_available_account().id, "openai-b")

    def test_a_cooled_down_account_is_not_reselected_while_cooling(self):
        """Do not repeatedly hammer a failed account."""
        self.pool.record_failure("openai-a", is_rate_limit=True, cooldown_seconds=60.0)
        for _ in range(10):
            self.assertNotEqual(self.pool.get_available_account().id, "openai-a")

    def test_account_becomes_eligible_again_once_cooldown_elapses(self):
        self.pool.record_failure("openai-a", is_rate_limit=True, cooldown_seconds=0.05)
        self.assertFalse(self.pool.get_account("openai-a").is_available())
        time.sleep(0.1)
        self.assertTrue(self.pool.get_account("openai-a").is_available())

    def test_expired_cooldown_restores_the_online_state(self):
        """`is_available()` performs the post-cooldown health transition."""
        self.pool.record_failure("openai-a", is_rate_limit=True, cooldown_seconds=0.05)
        time.sleep(0.1)
        self.pool.get_account("openai-a").is_available()
        self.assertIs(self.pool.get_account("openai-a").status, AccountStatus.ONLINE)

    def test_success_clears_cooldown_and_failure_count_immediately(self):
        self.pool.record_failure("openai-a", is_rate_limit=True)
        self.pool.record_success("openai-a")
        account = self.pool.get_account("openai-a")
        self.assertIsNone(account.cooldown_until)
        self.assertEqual(account.failure_count, 0)
        self.assertIs(account.status, AccountStatus.ONLINE)
        self.assertTrue(account.is_available())

    def test_health_check_timestamp_is_recorded_on_failure_and_success(self):
        self.pool.record_failure("openai-a", is_rate_limit=True)
        self.assertIsNotNone(self.pool.get_account("openai-a").last_health_check)
        self.pool.record_success("openai-a")
        self.assertIsNotNone(self.pool.get_account("openai-a").last_health_check)


class TestProgressiveBackoff(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = AccountPool("openai")
        self.pool.add_account(_account("openai-a"))

    def test_failure_count_increments_per_failure(self):
        for expected in (1, 2, 3):
            self.pool.record_failure("openai-a", error="connection reset")
            self.assertEqual(self.pool.get_account("openai-a").failure_count, expected)

    def test_backoff_grows_with_consecutive_failures(self):
        self.pool.record_failure("openai-a", error="reset", cooldown_seconds=1.0)
        first = self.pool.get_account("openai-a").cooldown_until - time.time()
        self.pool.record_failure("openai-a", error="reset", cooldown_seconds=1.0)
        second = self.pool.get_account("openai-a").cooldown_until - time.time()
        self.assertGreater(second, first)

    def test_backoff_is_capped_so_an_account_is_never_retired_forever(self):
        for _ in range(20):
            self.pool.record_failure("openai-a", error="reset", cooldown_seconds=60.0)
        remaining = self.pool.get_account("openai-a").cooldown_until - time.time()
        self.assertLessEqual(remaining, 600.0 + 1.0)

    def test_repeated_non_rate_limit_failures_mark_the_account_offline(self):
        for _ in range(3):
            self.pool.record_failure("openai-a", error="connection reset")
        self.assertIs(self.pool.get_account("openai-a").status, AccountStatus.OFFLINE)


class TestRateLimitIsolationAcrossProviders(unittest.TestCase):
    def test_rate_limiting_one_provider_does_not_affect_another(self):
        registry = AccountRegistry()
        registry.register_account(_account("openai-a"))
        anthropic = _account("anthropic-a")
        anthropic.provider_id = "anthropic"
        registry.register_account(anthropic)

        registry.get_pool("openai").record_failure("openai-a", is_rate_limit=True)

        self.assertFalse(registry.get_account("openai-a").is_available())
        self.assertTrue(registry.get_account("anthropic-a").is_available())


if __name__ == "__main__":
    unittest.main()
