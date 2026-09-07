"""Unit tests for Phase 4H Mission Control Telemetry & Phase 4I Provider Extensibility.

Covers /api/metrics/tokens, /api/router/history, and ProviderRegistry discovery helpers.
"""
import unittest

from brain.orchestrator.orchestrator import Orchestrator
from providers.registry.bootstrap import create_default_registry


class TestProviderRegistryExtensibility(unittest.TestCase):
    def setUp(self):
        self.registry = create_default_registry()

    def test_discover_models(self):
        # Discover models for registered provider
        models = self.registry.discover_models("antigravity")
        self.assertTrue(models)
        self.assertIn("gemini-3.7-flash-high", models)

    def test_discover_capabilities(self):
        caps = self.registry.discover_capabilities("antigravity")
        self.assertTrue(caps)

    def test_authenticate(self):
        # Authenticate verifies adapter health
        auth_ok = self.registry.authenticate("antigravity")
        self.assertTrue(auth_ok)

    def test_unknown_provider_returns_empty(self):
        self.assertEqual(self.registry.discover_models("nonexistent"), [])
        self.assertEqual(self.registry.discover_capabilities("nonexistent"), [])
        self.assertFalse(self.registry.authenticate("nonexistent"))


class TestMissionControlTelemetryEndpoints(unittest.TestCase):
    def setUp(self):
        self.orchestrator = Orchestrator()

    def test_token_telemetry_get_metrics(self):
        tracker = self.orchestrator.swarm.token_tracker
        metrics = tracker.get_metrics()
        self.assertIn("total_tasks_recorded", metrics)
        self.assertIn("total_known_tokens", metrics)
        self.assertIn("by_agent", metrics)
        self.assertIn("by_model", metrics)

    def test_router_get_history(self):
        history = self.orchestrator.router.get_routing_history(10)
        self.assertIsInstance(history, list)


if __name__ == "__main__":
    unittest.main()
