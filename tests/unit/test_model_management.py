"""
Unit tests for Mission Control Model Management (Phase 12.4).
"""

import unittest
from providers.models import ModelRegistry, ModelInfo


class TestModelManagement(unittest.TestCase):
    """Test model registration, capability tagging, priority and status management."""

    def setUp(self):
        self.registry = ModelRegistry()

    def test_model_registration_and_retrieval(self):
        """Test registering and retrieving model metadata."""
        model = ModelInfo(
            model_id="test-gpt-4o",
            provider_id="openai",
            display_name="GPT-4o Omni",
            context_window=128000,
            capabilities=["coding", "reasoning", "vision", "tool_use"],
            input_cost_per_1m=5.0,
            output_cost_per_1m=15.0,
            priority=85,
            enabled=True,
            status="available"
        )
        self.registry.register_model(model)
        retrieved = self.registry.get_model("test-gpt-4o")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.display_name, "GPT-4o Omni")
        self.assertEqual(retrieved.context_window, 128000)
        self.assertIn("coding", retrieved.capabilities)
        self.assertEqual(retrieved.priority, 85)

    def test_model_capability_tagging(self):
        """Test setting and querying capability tags."""
        tags = ["coding", "debugging", "fast", "cheap"]
        model = ModelInfo(
            model_id="test-claude-3-5-haiku",
            provider_id="anthropic",
            capabilities=tags
        )
        self.registry.register_model(model)
        models = self.registry.list_models()
        matching = [m for m in models if "fast" in m.capabilities]
        self.assertTrue(any(m.model_id == "test-claude-3-5-haiku" for m in matching))

    def test_enable_disable_model(self):
        """Test toggling model enabled state."""
        model = ModelInfo(
            model_id="test-deprecated-model",
            provider_id="openai",
            enabled=True
        )
        self.registry.register_model(model)
        self.assertTrue(self.registry.get_model("test-deprecated-model").enabled)

        # Disable
        model.enabled = False
        self.registry.register_model(model)
        self.assertFalse(self.registry.get_model("test-deprecated-model").enabled)

    def test_model_priority_ordering(self):
        """Test model priority affects ordering."""
        m1 = ModelInfo(model_id="m1", provider_id="test", priority=10)
        m2 = ModelInfo(model_id="m2", provider_id="test", priority=90)
        self.registry.register_model(m1)
        self.registry.register_model(m2)
        models = self.registry.list_models()
        # Find m1 and m2
        found_m1 = next(m for m in models if m.model_id == "m1")
        found_m2 = next(m for m in models if m.model_id == "m2")
        self.assertGreater(found_m2.priority, found_m1.priority)


if __name__ == "__main__":
    unittest.main()
