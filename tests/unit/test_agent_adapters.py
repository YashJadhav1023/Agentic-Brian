"""Unit tests for Agent Adapters and Provider Registry."""
import unittest
from pathlib import Path

from agents.base.adapter import Capability, ExecutionMode
from agents.antigravity.account1.adapter import AntigravityAccount1Adapter
from agents.antigravity.account2.adapter import AntigravityAccount2Adapter
from agents.kiro.adapter import KiroAdapter
from agents.cline.adapter import ClineAdapter
from providers.registry.bootstrap import create_default_registry


class TestAgentAdapters(unittest.TestCase):

    def setUp(self):
        self.registry = create_default_registry()

    def test_active_adapters_count(self):
        adapters = self.registry.list_active_adapters()
        agent_ids = {a.agent_id for a in adapters}
        self.assertIn("antigravity-account-1", agent_ids)
        self.assertIn("antigravity-account-2", agent_ids)
        self.assertIn("kiro-cli", agent_ids)
        self.assertIn("cline", agent_ids)
        self.assertNotIn("gemini-api", agent_ids)

    def test_account_isolation(self):
        a1 = self.registry.get_adapter("antigravity-account-1")
        a2 = self.registry.get_adapter("antigravity-account-2")
        self.assertIsNotNone(a1)
        self.assertIsNotNone(a2)
        self.assertNotEqual(a1.agent_id, a2.agent_id)
        self.assertNotEqual(a1.account_id, a2.account_id)
        self.assertEqual(a1._data_dir_name, "antigravity-cli")
        self.assertEqual(a2._data_dir_name, "antigravity-ide")

    def test_capabilities_declaration(self):
        a1 = self.registry.get_adapter("antigravity-account-1")
        self.assertIn(Capability.ARCHITECTURE, a1.capabilities())
        kiro = self.registry.get_adapter("kiro-cli")
        self.assertIn(Capability.TERMINAL_OPERATIONS, kiro.capabilities())

    def test_execution_mode_is_headless(self):
        for adapter in self.registry.list_active_adapters():
            self.assertEqual(adapter.execution_mode, ExecutionMode.HEADLESS)


if __name__ == "__main__":
    unittest.main()
