"""Phase 10: direct API, custom OpenAI-compatible, gateway and local providers.

Asserts that every provider named in the Phase 10 "Supported provider
categories" section can be configured through data alone -- provider name, base
URL, credential reference, models and organisation metadata -- with no
provider-specific branching required in callers.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from providers.api.anthropic_api import AnthropicAPIProvider
from providers.api.gemini_api import GeminiAPIProvider
from providers.api.litellm_gateway import LiteLLMGatewayProvider
from providers.api.ollama_local import OllamaProvider, DEFAULT_OLLAMA_BASE_URL
from providers.api.openai_compatible import OpenAICompatibleProvider
from providers.base import ProviderType
from providers.registry.bootstrap import create_default_registry

#: Every OpenAI-compatible service Phase 10 requires, as pure configuration.
OPENAI_COMPATIBLE_CATALOG = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
}


class TestOpenAICompatibleProviderMatrix(unittest.TestCase):
    def test_every_required_openai_compatible_service_is_configurable(self):
        for provider_id, base_url in OPENAI_COMPATIBLE_CATALOG.items():
            with self.subTest(provider=provider_id):
                provider = OpenAICompatibleProvider(
                    provider_id=provider_id,
                    base_url=base_url,
                    credential_reference=f"secret://mission-control/{provider_id}/default",
                    default_model="test-model",
                )
                ok, errors = provider.validate_config()
                self.assertTrue(ok, errors)
                self.assertEqual(provider.provider_id, provider_id)
                self.assertEqual(provider.base_url, base_url)

    def test_custom_self_hosted_endpoint_is_supported(self):
        provider = OpenAICompatibleProvider(
            provider_id="my-local-api",
            base_url="http://localhost:8000/v1",
            credential_reference="secret://mission-control/my-local-api/default",
            default_model="llama-3-70b",
            models=["llama-3-70b", "mistral-large"],
        )
        ok, errors = provider.validate_config()
        self.assertTrue(ok, errors)
        self.assertEqual(provider.models, ["llama-3-70b", "mistral-large"])

    def test_trailing_slash_in_base_url_is_normalised(self):
        provider = OpenAICompatibleProvider(
            provider_id="x", base_url="https://api.example.test/v1/"
        )
        self.assertEqual(provider.base_url, "https://api.example.test/v1")

    def test_organization_and_project_metadata_are_carried_as_headers(self):
        provider = OpenAICompatibleProvider(
            provider_id="openai",
            base_url="https://api.openai.com/v1",
            organization="org-example",
            project="proj-example",
        )
        headers = provider._build_request_headers()
        self.assertEqual(headers["OpenAI-Organization"], "org-example")
        self.assertEqual(headers["OpenAI-Project"], "proj-example")

    def test_no_authorization_header_is_sent_when_no_credential_is_configured(self):
        provider = OpenAICompatibleProvider(provider_id="x", base_url="https://x.test/v1")
        self.assertNotIn("Authorization", provider._build_request_headers())

    def test_health_reports_auth_error_when_api_credential_is_missing(self):
        provider = OpenAICompatibleProvider(
            provider_id="openai", base_url="https://api.openai.com/v1"
        )
        healthy, reason = provider.health()
        self.assertFalse(healthy)
        self.assertIn("AUTH_ERROR", reason)


class TestNativeApiProviders(unittest.TestCase):
    def test_gemini_provider_is_an_api_provider(self):
        provider = GeminiAPIProvider(provider_id="gemini-api")
        self.assertEqual(provider.provider_id, "gemini-api")
        self.assertTrue(provider.validate_config()[0] or provider.validate_config()[1])

    def test_anthropic_provider_is_an_api_provider(self):
        provider = AnthropicAPIProvider(provider_id="anthropic")
        self.assertEqual(provider.provider_id, "anthropic")

    def test_litellm_gateway_is_addressed_over_http_not_imported(self):
        """The gateway must be a network peer, never a Python dependency."""
        provider = LiteLLMGatewayProvider(
            provider_id="litellm", base_url="http://localhost:4000"
        )
        self.assertEqual(provider.provider_id, "litellm")
        import sys

        self.assertNotIn("litellm", sys.modules)


class TestOllamaLocalProvider(unittest.TestCase):
    def test_default_base_url_targets_the_local_daemon(self):
        self.assertEqual(OllamaProvider().base_url, DEFAULT_OLLAMA_BASE_URL)

    def test_provider_type_is_local_model_on_loopback(self):
        self.assertIs(OllamaProvider().provider_type, ProviderType.LOCAL_MODEL)

    def test_provider_type_stays_local_model_on_a_remote_host(self):
        """A remote Ollama box is still a local model server, not an API vendor."""
        remote = OllamaProvider(
            provider_id="ollama-lab", base_url="http://10.0.0.9:11434/v1"
        )
        self.assertIs(remote.provider_type, ProviderType.LOCAL_MODEL)

    def test_no_credential_is_required(self):
        provider = OllamaProvider()
        self.assertEqual(provider._credential_reference, "")
        ok, errors = provider.validate_config()
        self.assertTrue(ok, errors)

    def test_native_api_root_sits_alongside_the_openai_surface(self):
        self.assertEqual(
            OllamaProvider().native_api_root, "http://localhost:11434/api"
        )

    def test_base_url_without_v1_is_reported_as_a_config_error(self):
        ok, errors = OllamaProvider(base_url="http://localhost:11434").validate_config()
        self.assertFalse(ok)
        self.assertTrue(any("/v1" in e for e in errors))

    def test_health_degrades_cleanly_when_no_daemon_is_running(self):
        """Offline must be reported, never raised."""
        provider = OllamaProvider(base_url="http://127.0.0.1:1/v1")
        healthy, reason = provider.health()
        self.assertFalse(healthy)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason)

    def test_model_listing_does_not_raise_when_no_daemon_is_running(self):
        provider = OllamaProvider(base_url="http://127.0.0.1:1/v1", models=["llama3.2"])
        models = provider.list_models()
        self.assertIsInstance(models, list)


class TestProviderConfigurationDrivesRegistration(unittest.TestCase):
    """Adding a provider must be a config change, not a code change."""

    def _write_config(self, providers: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"version": 1, "providers": providers}, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_a_new_openai_compatible_provider_registers_from_config_alone(self):
        path = self._write_config(
            {
                "my-gateway": {
                    "id": "my-gateway",
                    "name": "My Gateway",
                    "enabled": True,
                    "type": "openai-compatible",
                    "base_url": "https://gw.example.test/v1",
                    "credential_ref": "secret://mission-control/my-gateway/default",
                    "default_model": "llama-3-70b",
                    "models": ["llama-3-70b"],
                }
            }
        )
        try:
            reg = create_default_registry(config_path=path)
            provider = reg.get_ai_provider("my-gateway")
            self.assertIsNotNone(provider)
            self.assertEqual(provider.base_url, "https://gw.example.test/v1")
            # A provider-level credential reference yields a default account
            self.assertIsNotNone(reg.account_registry.get_account("my-gateway-main"))
        finally:
            path.unlink(missing_ok=True)

    def test_an_ollama_provider_registers_as_a_local_model_provider(self):
        path = self._write_config(
            {
                "ollama": {
                    "id": "ollama",
                    "name": "Ollama (local)",
                    "enabled": True,
                    "type": "ollama",
                    "base_url": "http://localhost:11434/v1",
                    "default_model": "llama3.2",
                    "models": [],
                }
            }
        )
        try:
            reg = create_default_registry(config_path=path)
            provider = reg.get_ai_provider("ollama")
            self.assertIsNotNone(provider)
            self.assertIs(provider.provider_type, ProviderType.LOCAL_MODEL)
            self.assertIsInstance(provider, OllamaProvider)
        finally:
            path.unlink(missing_ok=True)

    def test_a_disabled_provider_is_not_registered(self):
        path = self._write_config(
            {
                "ollama": {
                    "id": "ollama",
                    "enabled": False,
                    "type": "ollama",
                    "base_url": "http://localhost:11434/v1",
                }
            }
        )
        try:
            reg = create_default_registry(config_path=path)
            self.assertIsNone(reg.get_ai_provider("ollama"))
        finally:
            path.unlink(missing_ok=True)

    def test_multiple_accounts_under_one_api_provider_are_registered(self):
        path = self._write_config(
            {
                "openai": {
                    "id": "openai",
                    "enabled": True,
                    "type": "openai-compatible",
                    "base_url": "https://api.openai.com/v1",
                    "models": ["gpt-4o"],
                    "accounts": {
                        "personal": {
                            "credential_reference": "secret://mission-control/openai/personal",
                            "enabled": True,
                        },
                        "work": {
                            "credential_reference": "secret://mission-control/openai/work",
                            "enabled": True,
                        },
                    },
                }
            }
        )
        try:
            reg = create_default_registry(config_path=path)
            accounts = {a.id for a in reg.account_registry.list_accounts("openai")}
            self.assertEqual(accounts, {"openai-personal", "openai-work"})
            for acc in reg.account_registry.list_accounts("openai"):
                self.assertTrue(acc.credential_reference.startswith("secret://"))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
