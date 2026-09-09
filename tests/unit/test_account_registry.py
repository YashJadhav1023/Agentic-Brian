"""Unit tests for Account, AccountPool, and AccountRegistry."""
import time
import unittest

from providers.registry.account_registry import (
    Account,
    AccountPool,
    AccountRegistry,
    AccountStatus,
    AuthenticationType,
)


class TestAccountRegistry(unittest.TestCase):
    def test_account_dataclass_and_availability(self):
        acc = Account(
            id="openai-acct-1",
            provider_id="openai",
            account_name="acct-1",
            authentication_type=AuthenticationType.API_KEY,
            credential_reference="secret://mission-control/openai/acct-1",
            status=AccountStatus.ONLINE,
            enabled=True,
            concurrency_limit=2,
        )
        self.assertTrue(acc.is_available())
        self.assertEqual(acc.current_concurrency, 0)

        # Max out concurrency
        acc.current_concurrency = 2
        self.assertFalse(acc.is_available())
        acc.current_concurrency = 0

        # Disable
        acc.enabled = False
        self.assertFalse(acc.is_available())
        acc.enabled = True

        # Rate limited
        acc.status = AccountStatus.RATE_LIMITED
        self.assertFalse(acc.is_available())
        acc.status = AccountStatus.ONLINE

        # Cooldown active
        acc.cooldown_until = time.time() + 100
        self.assertFalse(acc.is_available())
        acc.cooldown_until = None
        self.assertTrue(acc.is_available())

    def test_account_pool_lifecycle(self):
        pool = AccountPool("openai")
        a1 = Account(id="o1", provider_id="openai", account_name="o1", concurrency_limit=2)
        a2 = Account(id="o2", provider_id="openai", account_name="o2", concurrency_limit=2)
        pool.add_account(a1)
        pool.add_account(a2)

        self.assertEqual(len(pool.list_accounts()), 2)
        self.assertEqual(pool.get_account("o1"), a1)

        # Checkout and release
        self.assertTrue(pool.acquire("o1"))
        self.assertEqual(a1.current_concurrency, 1)
        self.assertTrue(pool.acquire("o1"))
        self.assertEqual(a1.current_concurrency, 2)
        # Cannot acquire above limit
        self.assertFalse(pool.acquire("o1"))

        pool.release("o1")
        self.assertEqual(a1.current_concurrency, 1)
        pool.release("o1")
        self.assertEqual(a1.current_concurrency, 0)

        # Failure cooldown
        pool.record_failure("o1", error="Rate limit exceeded")
        self.assertEqual(a1.failure_count, 1)
        self.assertIsNotNone(a1.cooldown_until)
        self.assertFalse(a1.is_available())

        # Success reset
        pool.record_success("o1")
        self.assertEqual(a1.failure_count, 0)
        self.assertIsNone(a1.cooldown_until)
        self.assertTrue(a1.is_available())

        # Selection preference
        best = pool.get_available_account(preferred_account="o2")
        self.assertEqual(best, a2)

    def test_account_registry_multi_provider_and_isolation(self):
        reg = AccountRegistry()
        o1 = Account(id="openai-1", provider_id="openai", account_name="o1")
        o2 = Account(id="openai-2", provider_id="openai", account_name="o2")
        g1 = Account(id="gemini-1", provider_id="gemini", account_name="g1")

        reg.register_account(o1)
        reg.register_account(o2)
        reg.register_account(g1)

        # List all
        self.assertEqual(len(reg.list_accounts()), 3)
        # List per provider
        self.assertEqual(len(reg.list_accounts(provider_id="openai")), 2)
        self.assertEqual(len(reg.list_accounts(provider_id="gemini")), 1)

        # Isolation
        pool_o = reg.get_pool("openai")
        self.assertIn(o1, pool_o.list_accounts())
        self.assertIn(o2, pool_o.list_accounts())
        self.assertNotIn(g1, pool_o.list_accounts())

        # Enable / disable
        reg.disable_account("openai-1")
        self.assertFalse(reg.get_account("openai-1").enabled)
        reg.enable_account("openai-1")
        self.assertTrue(reg.get_account("openai-1").enabled)

        # Remove
        self.assertTrue(reg.remove_account("openai-2"))
        self.assertIsNone(reg.get_account("openai-2"))
        self.assertEqual(len(reg.list_accounts(provider_id="openai")), 1)


class TestBootstrapClineAccountIdentity(unittest.TestCase):
    """BUG-005 regression: multi-account providers must not collapse.

    providers/config.py defines cline-account-{1,2,3}, but bootstrap() built
    every ClineAdapter with the class default agent_id="cline", so
    Provider.add_adapter() keyed all three under one dict entry. Account
    isolation, telemetry attribution, and routing were all silently degraded
    to a single identity.
    """

    def test_cline_accounts_have_distinct_adapters(self):
        from providers.registry.bootstrap import create_default_registry

        registry = create_default_registry()
        cline_provider = registry.get_provider("cline")
        self.assertIsNotNone(cline_provider)
        adapter_ids = set(cline_provider.adapters.keys())
        self.assertIn("cline-account-1", adapter_ids)
        self.assertIn("cline-account-2", adapter_ids)
        self.assertIn("cline-account-3", adapter_ids)

        # The first-class AccountRegistry must agree with the adapter view
        # for every cline account defined in configuration.
        registry_ids = {
            a.id for a in registry.account_registry.list_accounts()
            if a.provider_id == "cline"
        }
        self.assertEqual(registry_ids, adapter_ids)


if __name__ == "__main__":
    unittest.main()
