"""Phase 10: provider management, discovery and normalisation.

Covers requirement 8 (provider discovery) and requirement 9 (model discovery)
of the Phase 10 test matrix, and asserts the normalised provider architecture:
every provider category reaches Mission Control through the same `AIProvider`
contract rather than through category-specific branches in callers.
"""
from __future__ import annotations

import unittest

from agents.base.adapter import Capability
from providers.api.anthropic_api import AnthropicAPIProvider
from providers.api.gemini_api import GeminiAPIProvider
from providers.api.litellm_gateway import LiteLLMGatewayProvider
from providers.api.ollama_local import OllamaProvider
from providers.api.openai_compatible import OpenAICompatibleProvider
from providers.base import AIProvider, ProviderType
from providers.registry.bootstrap import create_default_registry
from providers.registry.provider_registry import ProviderRegistry


class TestProviderDiscovery(unittest.TestCase):
    """The registry must discover configured providers without hard-coding."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = create_default_registry()

    def test_agent_providers_are_discovered(self):
        ids = {p.id for p in self.registry.list_providers()}
        self.assertIn("antigravity", ids)
        self.assertIn("kiro", ids)
        self.assertIn("cline", ids)

    def test_ai_provider_list_is_distinct_from_agent_provider_list(self):
        """list_providers() and list_ai_providers() are deliberately separate.

        Agent providers hold AgentAdapter objects; AI providers hold AIProvider
        objects. Conflating them silently drops API providers from the UI.

        The assertion registers its own API provider rather than relying on one
        being present in config/providers.json, because which optional API
        providers are configured is an operator choice, not an invariant.
        """
        agent_ids = {p.id for p in self.registry.list_providers()}
        ai_ids = {p.provider_id for p in self.registry.list_ai_providers()}
        self.assertTrue(agent_ids)
        self.assertTrue(ai_ids)

        reg = ProviderRegistry()
        reg.register_ai_provider(
            OpenAICompatibleProvider(
                provider_id="api-only", base_url="https://api.example.test/v1"
            )
        )
        self.assertEqual({p.provider_id for p in reg.list_ai_providers()}, {"api-only"})
        self.assertEqual(reg.list_providers(), [])

    def test_model_discovery_returns_models_per_provider(self):
        for provider_id in ("antigravity", "kiro", "cline"):
            with self.subTest(provider=provider_id):
                models = self.registry.discover_models(provider_id)
                self.assertIsInstance(models, list)
                self.assertTrue(models, f"{provider_id} discovered no models")

    def test_capability_discovery_returns_capabilities_per_provider(self):
        caps = self.registry.discover_capabilities("kiro")
        self.assertIsInstance(caps, list)
        self.assertTrue(caps)

    def test_unknown_provider_discovery_is_empty_not_an_error(self):
        self.assertEqual(self.registry.discover_models("does-not-exist"), [])
        self.assertEqual(self.registry.discover_capabilities("does-not-exist"), [])

    def test_registry_serialization_contains_no_credential_material(self):
        blob = repr(self.registry.to_dict()).lower()
        for forbidden in ("sk-", "bearer ", "api_key=", "aiza"):
            self.assertNotIn(forbidden, blob)


class TestProviderCategoryNormalisation(unittest.TestCase):
    """All four Phase 10 provider categories implement one interface."""

    def _providers(self) -> dict[str, AIProvider]:
        return {
            "api-openai": OpenAICompatibleProvider(
                provider_id="openai", base_url="https://api.openai.com/v1"
            ),
            "api-gemini": GeminiAPIProvider(provider_id="gemini-api"),
            "api-anthropic": AnthropicAPIProvider(provider_id="anthropic"),
            "gateway-litellm": LiteLLMGatewayProvider(
                provider_id="litellm", base_url="http://localhost:4000"
            ),
            "local-ollama": OllamaProvider(),
        }

    def test_every_provider_implements_the_aiprovider_contract(self):
        for label, provider in self._providers().items():
            with self.subTest(provider=label):
                self.assertIsInstance(provider, AIProvider)
                for attr in (
                    "provider_id",
                    "provider_type",
                    "display_name",
                    "capabilities",
                    "health",
                    "list_models",
                    "execute",
                    "validate_config",
                    "get_usage",
                    "get_limits",
                ):
                    self.assertTrue(hasattr(provider, attr), f"{label} missing {attr}")

    def test_provider_types_cover_api_gateway_and_local_categories(self):
        providers = self._providers()
        self.assertIs(providers["api-openai"].provider_type, ProviderType.API)
        self.assertIs(providers["local-ollama"].provider_type, ProviderType.LOCAL_MODEL)
        self.assertIn(
            providers["gateway-litellm"].provider_type,
            (ProviderType.GATEWAY, ProviderType.API),
        )

    def test_capabilities_are_a_frozenset_of_capability_flags(self):
        for label, provider in self._providers().items():
            with self.subTest(provider=label):
                caps = provider.capabilities()
                self.assertIsInstance(caps, frozenset)
                for c in caps:
                    self.assertIsInstance(c, Capability)

    def test_validate_config_is_offline_and_returns_structured_errors(self):
        ok, errors = OpenAICompatibleProvider(
            provider_id="", base_url=""
        ).validate_config()
        self.assertFalse(ok)
        self.assertIsInstance(errors, list)
        self.assertTrue(errors)
        # A well-formed provider validates cleanly
        ok2, errors2 = OpenAICompatibleProvider(
            provider_id="ok", base_url="https://example.test/v1"
        ).validate_config()
        self.assertTrue(ok2)
        self.assertEqual(errors2, [])


class TestProviderRegistration(unittest.TestCase):
    def test_ai_provider_can_be_registered_and_retrieved(self):
        reg = ProviderRegistry()
        provider = OpenAICompatibleProvider(
            provider_id="custom-endpoint", base_url="https://gw.example.test/v1"
        )
        reg.register_ai_provider(provider)
        self.assertIs(reg.get_ai_provider("custom-endpoint"), provider)
        self.assertIn(
            "custom-endpoint", [p.provider_id for p in reg.list_ai_providers()]
        )

    def test_missing_ai_provider_returns_none(self):
        self.assertIsNone(ProviderRegistry().get_ai_provider("nope"))


if __name__ == "__main__":
    unittest.main()
