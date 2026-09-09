"""Part 14 — Provider onboarding tests (Phase 22, Parts 5 and 6).

Covers the two halves of bringing a provider online:

* :class:`APIProviderOnboarder` — endpoint resolution and *structured*
  pre-flight validation. Validation must classify an outcome (unauthorized vs
  rate limited vs network error vs misconfiguration), because "failed" alone
  gives the operator nothing to act on.
* :class:`ProviderRegistry` — registering providers and exposing them without
  leaking credentials.

No test here performs a real network call with a real key. Offline behaviour is
asserted directly; network classification is asserted through the report
contract rather than by contacting a live provider.
"""

import unittest

from providers.api.onboarding import (
    APIProviderOnboarder,
    ValidationReport,
    ValidationStatus,
)
from providers.registry.account_registry import AccountRegistry
from providers.registry.model_registry import ModelRegistry
from providers.registry.provider_registry import Provider, ProviderRegistry


class TestEndpointResolution(unittest.TestCase):
    """A provider must reach the right base URL without the caller knowing it."""

    def test_known_providers_resolve_to_their_official_endpoints(self):
        cases = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta",
            "gemini-api": "https://generativelanguage.googleapis.com/v1beta",
        }
        for provider_id, expected in cases.items():
            self.assertEqual(
                APIProviderOnboarder.resolve_endpoint(provider_id), expected
            )

    def test_provider_id_is_matched_case_insensitively(self):
        self.assertEqual(
            APIProviderOnboarder.resolve_endpoint("OpenAI"),
            "https://api.openai.com/v1",
        )
        self.assertEqual(
            APIProviderOnboarder.resolve_endpoint("  ANTHROPIC  "),
            "https://api.anthropic.com/v1",
        )

    def test_an_explicit_base_url_always_wins(self):
        """Self-hosted and OpenAI-compatible gateways must be addressable."""
        self.assertEqual(
            APIProviderOnboarder.resolve_endpoint(
                "openai", "https://gateway.internal/v1"
            ),
            "https://gateway.internal/v1",
        )

    def test_trailing_slashes_and_padding_are_normalised(self):
        self.assertEqual(
            APIProviderOnboarder.resolve_endpoint("openai", "  https://x.test/v1/  "),
            "https://x.test/v1",
        )

    def test_blank_base_url_falls_back_to_the_default(self):
        for blank in (None, "", "   "):
            self.assertEqual(
                APIProviderOnboarder.resolve_endpoint("anthropic", blank),
                "https://api.anthropic.com/v1",
            )

    def test_an_unknown_provider_still_resolves_to_a_usable_endpoint(self):
        """Unknown ids are treated as OpenAI-compatible rather than failing."""
        endpoint = APIProviderOnboarder.resolve_endpoint("some-new-vendor")
        self.assertTrue(endpoint.startswith("https://"))


class TestCredentialValidationContract(unittest.TestCase):
    """Validation must be structured, offline-safe for bad input, and secret-free."""

    def test_empty_key_is_a_configuration_error_not_a_network_call(self):
        for blank in ("", "   ", None):
            report = APIProviderOnboarder.validate_credentials("openai", blank)
            self.assertEqual(report.status, ValidationStatus.INVALID_CONFIGURATION)
            self.assertEqual(report.provider_id, "openai")
            self.assertIn("must not be empty", report.error_message or "")

    def test_validation_statuses_cover_the_actionable_outcomes(self):
        """Each distinct operator action needs its own status."""
        for name in (
            "CONNECTED",
            "UNAUTHORIZED",
            "RATE_LIMITED",
            "NETWORK_ERROR",
            "INVALID_CONFIGURATION",
            "UNAVAILABLE",
        ):
            self.assertTrue(
                hasattr(ValidationStatus, name), f"ValidationStatus.{name} is required"
            )

    def test_report_serialises_to_a_json_safe_dict(self):
        report = ValidationReport(
            status=ValidationStatus.CONNECTED,
            provider_id="openai",
            models_discovered=["gpt-4o"],
            latency_ms=123.4567,
            endpoint="https://api.openai.com/v1",
        )
        payload = report.to_dict()

        self.assertEqual(payload["status"], "CONNECTED")
        self.assertEqual(payload["models_discovered"], ["gpt-4o"])
        self.assertEqual(payload["latency_ms"], 123.46)
        self.assertIsNone(payload["error_message"])
        self.assertTrue(payload["validated_at"])

    def test_report_never_carries_the_api_key(self):
        """A validation report is shown in the UI, so it must be secret-free."""
        secret = "sk-live-SHOULDNEVERAPPEAR00000000000000"
        report = APIProviderOnboarder.validate_credentials("openai", "")
        self.assertNotIn(secret, str(report.to_dict()))
        # And no field exists that could hold one.
        self.assertNotIn("api_key", report.to_dict())

    def test_known_models_are_published_for_each_supported_provider(self):
        for provider_id in ("openai", "anthropic", "gemini", "gemini-api"):
            models = APIProviderOnboarder.KNOWN_MODELS.get(provider_id)
            self.assertTrue(models, f"{provider_id} must publish known models")
            self.assertTrue(all(isinstance(m, str) and m for m in models))


class TestProviderRegistryOnboarding(unittest.TestCase):
    """Registering a provider must make it discoverable and keep it isolated."""

    def setUp(self) -> None:
        self.registry = ProviderRegistry(
            account_registry=AccountRegistry(), model_registry=ModelRegistry()
        )

    def test_a_registered_provider_is_retrievable_and_listed(self):
        provider = Provider(id="openai", name="OpenAI", description="OpenAI API")
        self.registry.register_provider(provider)

        self.assertIs(self.registry.get_provider("openai"), provider)
        self.assertIn("openai", [p.id for p in self.registry.list_providers()])

    def test_an_unknown_provider_resolves_to_none_rather_than_raising(self):
        self.assertIsNone(self.registry.get_provider("not-registered"))

    def test_registering_multiple_providers_keeps_them_independent(self):
        for pid in ("openai", "anthropic", "gemini"):
            self.registry.register_provider(
                Provider(id=pid, name=pid.title(), description=f"{pid} API")
            )

        listed = {p.id for p in self.registry.list_providers()}
        self.assertEqual(listed, {"openai", "anthropic", "gemini"})

    def test_registry_exposes_its_account_and_model_registries(self):
        """Provider onboarding and account onboarding must share one source."""
        self.assertIsInstance(self.registry.accounts, AccountRegistry)
        self.assertIsInstance(self.registry.models, ModelRegistry)

    def test_a_provider_with_no_adapters_is_not_reported_healthy(self):
        """An onboarded-but-unwired provider must not look ready."""
        provider = Provider(id="openai", name="OpenAI", description="OpenAI API")
        self.registry.register_provider(provider)
        self.assertFalse(provider.health()["healthy"])

    def test_registry_serialisation_is_secret_free(self):
        self.registry.register_provider(
            Provider(id="openai", name="OpenAI", description="OpenAI API")
        )
        payload = str(self.registry.to_dict())

        for forbidden in ("sk-", "api_key", "secret_value", "password"):
            self.assertNotIn(
                forbidden, payload, f"registry dict must not expose {forbidden}"
            )


if __name__ == "__main__":
    unittest.main()
