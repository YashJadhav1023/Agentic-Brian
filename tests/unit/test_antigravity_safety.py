"""
Unit tests for Antigravity GUI and Account Safety Invariants (Safety Rule #1-#5).
"""

import unittest
from providers.registry.bootstrap import create_default_registry
from providers.registry.account_registry import Account, AccountRegistry


class TestAntigravitySafety(unittest.TestCase):
    """Verify Antigravity accounts are frozen and protected against removal or tampering."""

    def setUp(self):
        self.provider_reg = create_default_registry()
        self.registry = self.provider_reg.account_registry

    def test_protected_antigravity_accounts_exist(self):
        """Phase 8 accounts must exist and remain registered."""
        protected = ["antigravity-account-1", "antigravity-account-2", "antigravity-account-3"]
        for aid in protected:
            acct = self.registry.get_account(aid)
            self.assertIsNotNone(acct, f"Protected account {aid} missing from registry")
            self.assertEqual(acct.provider_id, "antigravity")

    def test_protected_accounts_cannot_be_deleted(self):
        """Protected Antigravity accounts cannot be deleted via remove_account."""
        protected = ["antigravity-account-1", "antigravity-account-2", "antigravity-account-3"]
        for aid in protected:
            res = self.registry.remove_account(aid)
            self.assertFalse(res, f"Protected account {aid} was improperly removed")
            self.assertIsNotNone(self.registry.get_account(aid))

    def test_no_plaintext_secrets_in_account_info(self):
        """Account metadata must never contain plaintext credentials."""
        for aid in ["antigravity-account-1", "antigravity-account-2", "antigravity-account-3"]:
            acct = self.registry.get_account(aid)
            d = str(acct.to_dict())
            self.assertNotIn("Bearer ", d)
            self.assertNotIn("ya29.", d)
            self.assertNotIn("refresh_token", d)


if __name__ == "__main__":
    unittest.main()
