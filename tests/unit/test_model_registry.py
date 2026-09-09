"""Unit tests for ModelRegistry and ModelMetadata."""
import unittest

from providers.registry.model_registry import ModelMetadata, ModelRegistry


class TestModelRegistry(unittest.TestCase):
    def test_model_metadata(self):
        m = ModelMetadata(
            model_id="gpt-4o",
            provider_id="openai",
            display_name="GPT-4o",
            capabilities=frozenset(["chat", "streaming", "vision"]),
            context_window=128_000,
            input_cost_per_1m=5.0,
            output_cost_per_1m=15.0,
            enabled=True,
        )
        d = m.to_dict()
        self.assertEqual(d["model_id"], "gpt-4o")
        self.assertEqual(d["provider_id"], "openai")
        self.assertEqual(d["context_window"], 128_000)
        self.assertEqual(d["input_cost_per_1m"], 5.0)

    def test_model_registry_operations(self):
        reg = ModelRegistry()
        m1 = ModelMetadata(
            model_id="gpt-4o",
            provider_id="openai",
            display_name="GPT-4o",
            capabilities=frozenset(["chat", "vision"]),
            enabled=True,
        )
        m2 = ModelMetadata(
            model_id="claude-3-5-sonnet",
            provider_id="anthropic",
            display_name="Claude 3.5 Sonnet",
            capabilities=frozenset(["chat", "deep-reasoning"]),
            enabled=True,
        )
        m3 = ModelMetadata(
            model_id="disabled-model",
            provider_id="openai",
            display_name="Disabled",
            capabilities=frozenset(["chat"]),
            enabled=False,
        )

        reg.register_model(m1)
        reg.register_model(m2)
        reg.register_model(m3)

        self.assertEqual(len(reg.list_models(enabled_only=False)), 3)
        self.assertEqual(len(reg.list_models()), 2)
        self.assertEqual(len(reg.list_models(provider_id="openai")), 1)
        self.assertEqual(len(reg.list_models(provider_id="openai", enabled_only=False)), 2)
        self.assertEqual(len(reg.list_models(provider_id="anthropic")), 1)

        # Capability filtering
        vision_models = reg.list_models(capability="vision")
        self.assertEqual(len(vision_models), 1)
        self.assertEqual(vision_models[0].model_id, "gpt-4o")

        # Retrieval
        self.assertEqual(reg.get_model("claude-3-5-sonnet"), m2)
        self.assertIsNone(reg.get_model("nonexistent-model"))


if __name__ == "__main__":
    unittest.main()
