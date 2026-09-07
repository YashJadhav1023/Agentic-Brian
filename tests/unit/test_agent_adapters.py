"""Agent adapters and Provider Registry contract tests."""
import unittest

from agents.base.adapter import AgentStatus, Capability, ExecutionMode
from providers.registry.bootstrap import create_default_registry
from providers.registry.config import get_accounts, max_concurrent_agents


class TestAgentAdapters(unittest.TestCase):

    def setUp(self):
        self.registry = create_default_registry()

    def test_active_adapters_count(self):
        agent_ids = {a.agent_id for a in self.registry.list_active_adapters()}
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
        self.assertEqual(a1.data_dir, "antigravity-cli")
        self.assertEqual(a2.data_dir, "antigravity-ide")

    def test_capabilities_declaration(self):
        a1 = self.registry.get_adapter("antigravity-account-1")
        self.assertIn(Capability.ARCHITECTURE, a1.capabilities())
        kiro = self.registry.get_adapter("kiro-cli")
        self.assertIn(Capability.TERMINAL_OPERATIONS, kiro.capabilities())

    def test_execution_mode_is_headless(self):
        for adapter in self.registry.list_active_adapters():
            self.assertEqual(adapter.execution_mode, ExecutionMode.HEADLESS)

    def test_every_adapter_implements_the_optional_interface(self):
        for adapter in self.registry.list_active_adapters():
            self.assertIsInstance(adapter.status(), AgentStatus)
            described = adapter.describe()
            self.assertEqual(described["agent_id"], adapter.agent_id)
            self.assertIn("health", described)

    def test_registry_serialization_exposes_account2(self):
        """Requirement: Mission Control must see Account 2 as a real agent."""
        data = self.registry.to_dict()
        accounts = data["antigravity"]["accounts"]
        self.assertIn("antigravity-account-2", accounts)
        entry = accounts["antigravity-account-2"]
        self.assertTrue(entry["healthy"])
        self.assertEqual(entry["account_id"], "account-2")
        self.assertIn("antigravity-ide", entry["profile"])


class TestProviderConfig(unittest.TestCase):

    def test_accounts_load_from_config(self):
        accounts = {a.agent_id for a in get_accounts("antigravity")}
        self.assertEqual(accounts, {"antigravity-account-1", "antigravity-account-2"})

    def test_bounded_concurrency_for_this_host(self):
        self.assertEqual(max_concurrent_agents(), 2)


if __name__ == "__main__":
    unittest.main()
