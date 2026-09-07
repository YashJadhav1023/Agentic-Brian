"""Unit tests for SmartRouter task classification and routing."""
import unittest

from brain.router.smart_router import SmartRouter
from models.policies.model_policy import Complexity
from providers.registry.bootstrap import create_default_registry


class TestSmartRouter(unittest.TestCase):

    def setUp(self):
        self.registry = create_default_registry()
        self.router = SmartRouter(self.registry)

    def test_route_architecture_to_account1(self):
        dec = self.router.route("Draft architecture RFC for secure cross-agent communication")
        self.assertEqual(dec.agent_id, "antigravity-account-1")

    def test_route_refactoring_to_account2(self):
        dec = self.router.route("Refactor telemetry and component handlers across microservices")
        self.assertEqual(dec.agent_id, "antigravity-account-2")

    def test_route_terminal_to_kiro(self):
        dec = self.router.route("Run pytest suite and verify docker container health")
        self.assertEqual(dec.agent_id, "kiro-cli")

    def test_route_frontend_to_cline(self):
        dec = self.router.route("Fix CSS flexbox styling on the navigation layout")
        self.assertEqual(dec.agent_id, "cline")

    def test_explicit_override(self):
        dec = self.router.route("Any instruction", preferred_agent="antigravity-account-2", preferred_model="gemini-3.8-flash-low")
        self.assertEqual(dec.agent_id, "antigravity-account-2")
        self.assertEqual(dec.model, "gemini-3.8-flash-low")


if __name__ == "__main__":
    unittest.main()
