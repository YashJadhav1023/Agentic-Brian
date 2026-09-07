"""Unit tests verifying complete Antigravity Account 1 & 2 isolation and headless capabilities."""
import unittest
from pathlib import Path

from agents.antigravity.account1.adapter import AntigravityAccount1Adapter
from agents.antigravity.account2.adapter import AntigravityAccount2Adapter
from agents.base.adapter import ExecutionMode


class TestAntigravityAccounts(unittest.TestCase):

    def setUp(self):
        self.a1 = AntigravityAccount1Adapter()
        self.a2 = AntigravityAccount2Adapter()

    def test_account_isolation(self):
        self.assertEqual(self.a1.agent_id, "antigravity-account-1")
        self.assertEqual(self.a2.agent_id, "antigravity-account-2")
        self.assertEqual(self.a1.account_id, "account-1")
        self.assertEqual(self.a2.account_id, "account-2")
        self.assertEqual(self.a1._data_dir_name, "antigravity-cli")
        self.assertEqual(self.a2._data_dir_name, "antigravity-ide")
        self.assertNotEqual(self.a1._data_dir_name, self.a2._data_dir_name)

    def test_both_accounts_are_headless(self):
        self.assertEqual(self.a1.execution_mode, ExecutionMode.HEADLESS)
        self.assertEqual(self.a2.execution_mode, ExecutionMode.HEADLESS)

    def test_health_checks(self):
        ok1, reason1 = self.a1.health()
        self.assertTrue(ok1, f"Account 1 unhealthy: {reason1}")
        ok2, reason2 = self.a2.health()
        self.assertTrue(ok2, f"Account 2 unhealthy: {reason2}")


if __name__ == "__main__":
    unittest.main()
