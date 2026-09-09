"""Unit tests for Generic API Provider Onboarding and Structured Validation."""

import json
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from providers.api.onboarding import (
    APIProviderOnboarder,
    ValidationReport,
    ValidationStatus,
)
from providers.registry.account_registry import AccountRegistry, AccountStatus, AuthenticationType
from providers.registry.credential_manager import get_credential_manager
from providers.registry.lifecycle import AccountLifecycleState, AuthState, HealthState


class TestAPIProviderCredentials(unittest.TestCase):
    """Verify API provider validation statuses and safe credential handling."""

    def setUp(self):
        self.registry = AccountRegistry()

    def test_validation_statuses_complete(self):
        """All required validation status values must be present in ValidationStatus enum."""
        expected = {
            "CONNECTED",
            "UNAUTHORIZED",
            "RATE_LIMITED",
            "NETWORK_ERROR",
            "INVALID_CONFIGURATION",
            "UNAVAILABLE",
        }
        actual = {s.value for s in ValidationStatus}
        self.assertEqual(actual, expected)

    def test_empty_api_key_returns_invalid_configuration(self):
        """Validating an empty API key immediately reports INVALID_CONFIGURATION."""
        rep = APIProviderOnboarder.validate_credentials("openai", "")
        self.assertEqual(rep.status, ValidationStatus.INVALID_CONFIGURATION)
        self.assertIn("empty", rep.error_message.lower())

    @patch("urllib.request.urlopen")
    def test_openai_connected_flow(self, mock_urlopen):
        """HTTP 200 from OpenAI /models yields CONNECTED status and discovered models."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}, {"id": "o1"}]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        rep = APIProviderOnboarder.validate_credentials("openai", "sk-proj-testkey1234567890abcdef")
        self.assertEqual(rep.status, ValidationStatus.CONNECTED)
        self.assertIn("gpt-4o", rep.models_discovered)
        self.assertIn("o1", rep.models_discovered)

    @patch("urllib.request.urlopen")
    def test_openai_unauthorized_flow(self, mock_urlopen):
        """HTTP 401 from OpenAI yields UNAUTHORIZED status."""
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.openai.com/v1/models",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )

        rep = APIProviderOnboarder.validate_credentials("openai", "sk-invalid-key")
        self.assertEqual(rep.status, ValidationStatus.UNAUTHORIZED)
        self.assertIn("Unauthorized", rep.error_message)

    @patch("urllib.request.urlopen")
    def test_openai_rate_limited_flow(self, mock_urlopen):
        """HTTP 429 yields RATE_LIMITED status."""
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.openai.com/v1/models",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )

        rep = APIProviderOnboarder.validate_credentials("openai", "sk-quota-exceeded-key")
        self.assertEqual(rep.status, ValidationStatus.RATE_LIMITED)

    @patch("urllib.request.urlopen")
    def test_anthropic_http_400_confirms_connected(self, mock_urlopen):
        """Anthropic API returns HTTP 400 when auth is valid but payload schema is minimal."""
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=None,
        )

        rep = APIProviderOnboarder.validate_credentials("anthropic", "sk-ant-test-key")
        self.assertEqual(rep.status, ValidationStatus.CONNECTED)
        self.assertIn("claude-3-7-sonnet-20250219", rep.models_discovered)

    @patch("urllib.request.urlopen")
    def test_network_error_handled_gracefully(self, mock_urlopen):
        """Connection failure yields NETWORK_ERROR status."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        rep = APIProviderOnboarder.validate_credentials("openai", "sk-test", base_url="http://127.0.0.1:9999/v1")
        self.assertEqual(rep.status, ValidationStatus.NETWORK_ERROR)

    @patch("urllib.request.urlopen")
    def test_onboard_account_end_to_end(self, mock_urlopen):
        """onboard_account persists credential in CredentialManager, constructs Account, and registers."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "data": [{"id": "gpt-4o"}]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        raw_key = "sk-proj-onboard-test-key-987654321"
        account, report = APIProviderOnboarder.onboard_account(
            account_id="openai-account-new",
            provider_id="openai",
            api_key=raw_key,
            registry=self.registry,
            validate_first=True,
        )

        self.assertIsNotNone(account)
        self.assertEqual(report.status, ValidationStatus.CONNECTED)
        self.assertEqual(account.id, "openai-account-new")
        self.assertEqual(account.status, AccountStatus.ONLINE)
        self.assertEqual(account.lifecycle_state, AccountLifecycleState.ONLINE)
        self.assertEqual(account.auth_state, AuthState.AUTHENTICATED)
        self.assertEqual(account.health_state, HealthState.HEALTHY)

        # Secret is stored under secret URI
        cred_ref = account.credential_reference
        self.assertEqual(cred_ref, "secret://mission-control/openai/openai-account-new/api_key")
        cm = get_credential_manager()
        self.assertEqual(cm.retrieve(cred_ref), raw_key)

        # Ensure raw secret is never present in to_dict()
        serialized = str(account.to_dict())
        self.assertNotIn(raw_key, serialized)

    @patch("urllib.request.urlopen")
    def test_gemini_uses_header_not_query_string(self, mock_urlopen):
        """Gemini API validation passes api_key in x-goog-api-key header and not in query string."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "models": [{"name": "models/gemini-2.0-flash"}]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        key = "AIzaSyTestSecretKey-12345"
        rep = APIProviderOnboarder.validate_credentials("gemini", key)
        self.assertEqual(rep.status, ValidationStatus.CONNECTED)
        self.assertIn("gemini-2.0-flash", rep.models_discovered)

        # Inspect request passed to urlopen
        req = mock_urlopen.call_args[0][0]
        self.assertNotIn("?key=", req.full_url)
        self.assertNotIn(key, req.full_url)
        self.assertEqual(req.headers.get("X-goog-api-key"), key)

    @patch("urllib.request.urlopen")
    def test_rejects_html_error_responses(self, mock_urlopen):
        """Pre-flight checks reject HTML error pages with INVALID_CONFIGURATION."""
        html_content = b"<html><head><title>502 Bad Gateway</title></head><body>Server Error</body></html>"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = html_content
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        # Test OpenAI compatible
        rep_openai = APIProviderOnboarder.validate_credentials("openai", "sk-test")
        self.assertEqual(rep_openai.status, ValidationStatus.INVALID_CONFIGURATION)

        # Test Gemini
        rep_gemini = APIProviderOnboarder.validate_credentials("gemini", "AIzaSyTest")
        self.assertEqual(rep_gemini.status, ValidationStatus.INVALID_CONFIGURATION)

        # Test Anthropic
        rep_anthropic = APIProviderOnboarder.validate_credentials("anthropic", "sk-ant-test")
        self.assertEqual(rep_anthropic.status, ValidationStatus.INVALID_CONFIGURATION)


if __name__ == "__main__":
    unittest.main()
