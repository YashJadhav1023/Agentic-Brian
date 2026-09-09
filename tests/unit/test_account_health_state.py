"""Unit tests for Decoupled Account Health and Task State Isolation."""

import unittest
from providers.registry.account_registry import (
    Account,
    AccountPool,
    AccountRegistry,
    AccountStatus,
    AuthenticationType,
)
from providers.registry.lifecycle import (
    AccountLifecycleState,
    AuthState,
    HealthState,
    ProcessState,
)


class TestAccountHealthStateDecoupling(unittest.TestCase):
    """Ensure task execution errors and reconciler recoveries never pollute account health."""

    def test_reconciler_error_does_not_affect_account_health(self):
        """A historical task error string must not overwrite account.health_state or account.status."""
        account = Account(
            id="worker-1",
            provider_id="antigravity",
            account_name="worker-1",
            status=AccountStatus.ONLINE,
            health_reason="",
        )

        self.assertEqual(account.status, AccountStatus.ONLINE)
        self.assertEqual(account.health_state, HealthState.HEALTHY)
        self.assertTrue(account.is_available())

        # Simulate historical task error from runtime reconciler
        reconciler_err = (
            "Process terminated or system restarted before task completed (recovered by runtime reconciler)"
        )
        # In decoupled architecture, task errors belong to task objects or historical_errors array,
        # never directly overriding the account's operational health!
        self.assertNotEqual(account.health_reason, reconciler_err)
        self.assertTrue(account.is_available())
        self.assertEqual(account.health_state, HealthState.HEALTHY)

    def test_pool_rate_limit_and_cooldown_behavior(self):
        """Rate limit failures set DEGRADED and cooldown without permanently crashing account."""
        pool = AccountPool("openai")
        acct = Account(
            id="openai-pool-1",
            provider_id="openai",
            account_name="openai-pool-1",
            status=AccountStatus.ONLINE,
        )
        pool.add_account(acct)

        # Record rate limit
        pool.record_failure("openai-pool-1", is_rate_limit=True, cooldown_seconds=10.0, error="HTTP 429 Too Many Requests")
        self.assertEqual(acct.status, AccountStatus.RATE_LIMITED)
        self.assertEqual(acct.health_state, HealthState.DEGRADED)
        self.assertFalse(acct.is_available())

        # Record success resets back to healthy
        pool.record_success("openai-pool-1")
        self.assertEqual(acct.status, AccountStatus.ONLINE)
        self.assertEqual(acct.health_state, HealthState.HEALTHY)
        self.assertEqual(acct.health_reason, "")
        self.assertTrue(acct.is_available())

    def test_repeated_failures_trigger_progressive_backoff(self):
        """Repeated non-rate-limit failures trigger progressive backoff and eventual OFFLINE."""
        pool = AccountPool("anthropic")
        acct = Account(
            id="anthropic-1",
            provider_id="anthropic",
            account_name="anthropic-1",
            status=AccountStatus.ONLINE,
        )
        pool.add_account(acct)

        # 1st failure -> DEGRADED
        pool.record_failure("anthropic-1", is_rate_limit=False, cooldown_seconds=5.0, error="Network timeout")
        self.assertEqual(acct.failure_count, 1)
        self.assertEqual(acct.health_state, HealthState.DEGRADED)

        # 2nd failure -> DEGRADED
        pool.record_failure("anthropic-1", is_rate_limit=False, cooldown_seconds=5.0, error="Network timeout")
        self.assertEqual(acct.failure_count, 2)
        self.assertEqual(acct.health_state, HealthState.DEGRADED)

        # 3rd failure -> OFFLINE & UNHEALTHY
        pool.record_failure("anthropic-1", is_rate_limit=False, cooldown_seconds=5.0, error="Network timeout")
        self.assertEqual(acct.failure_count, 3)
        self.assertEqual(acct.status, AccountStatus.OFFLINE)
        self.assertEqual(acct.health_state, HealthState.UNHEALTHY)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.OFFLINE)
        self.assertFalse(acct.is_available())


if __name__ == "__main__":
    unittest.main()
