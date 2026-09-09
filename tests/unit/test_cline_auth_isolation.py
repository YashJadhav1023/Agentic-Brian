"""Unit tests for Cline Dynamic Discovery and Multi-Account Isolation."""

import tempfile
import unittest
from pathlib import Path

from agents.cline.auth import ClineAuthManager, ClineCapabilities
from providers.registry.account_registry import AccountRegistry, AccountStatus, AuthenticationType
from providers.registry.credential_manager import get_credential_manager
from providers.registry.lifecycle import AccountLifecycleState


class TestClineAuthIsolation(unittest.TestCase):
    """Verify Cline dynamic discovery, directory isolation, and credential security."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp_dir.name)
        self.registry = AccountRegistry()
        self.auth_mgr = ClineAuthManager(
            base_dir=self.base_dir,
            registry=self.registry,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_dynamic_capabilities_discovery(self):
        """discover_capabilities inspects installed cline binary without crashing."""
        caps = self.auth_mgr.discover_capabilities(force_refresh=True)
        self.assertIsInstance(caps, ClineCapabilities)
        if caps.executable_path:
            self.assertEqual(caps.version, "3.0.61")
            self.assertTrue(caps.has_auth_command)
            self.assertTrue(caps.supports_config_flag)
            self.assertTrue(caps.supports_data_dir_flag)
            self.assertIn("-p", caps.auth_flags)
            self.assertIn("-k", caps.auth_flags)

    def test_setup_account_isolation_creates_distinct_paths(self):
        """setup_account_isolation generates separate config and data dirs."""
        c1, d1 = self.auth_mgr.setup_account_isolation("cline-account-test-1")
        c2, d2 = self.auth_mgr.setup_account_isolation("cline-account-test-2")

        self.assertTrue(c1.is_dir())
        self.assertTrue(d1.is_dir())
        self.assertTrue(c2.is_dir())
        self.assertTrue(d2.is_dir())

        self.assertNotEqual(str(c1), str(c2))
        self.assertNotEqual(str(d1), str(d2))

    def test_credentials_stored_via_secret_uri(self):
        """API keys are stored in CredentialManager under secret:// and never plaintext in configs."""
        raw_key = "sk-test-cline-isolated-key-1234567890"
        ref = self.auth_mgr.store_credential("cline-account-test", raw_key)
        self.assertEqual(ref, "secret://mission-control/cline/cline-account-test/api_key")

        cm = get_credential_manager()
        self.assertTrue(cm.exists(ref))
        self.assertEqual(cm.retrieve(ref), raw_key)

        # Masked representation
        masked = cm.get_masked_credential(ref)
        self.assertTrue(masked.startswith("************"))
        self.assertNotIn(raw_key, masked)

    def test_authenticate_dry_run_redacts_keys(self):
        """authenticate without execute_cli produces redacted command with zero plaintext secrets."""
        raw_key = "sk-ant-api03-test-12345678901234567890"
        res = self.auth_mgr.authenticate(
            account_id="cline-test",
            provider="anthropic",
            api_key=raw_key,
            execute_cli=False,
        )
        self.assertTrue(res["success"])
        self.assertIn("command_redacted", res)
        self.assertNotIn(raw_key, res["command_redacted"])
        self.assertIn("<API_KEY_REDACTED>", res["command_redacted"])

    def test_register_account_creates_valid_record(self):
        """register_account sets up isolated Account entity."""
        raw_key = "sk-test-register-1234567890"
        ref = self.auth_mgr.store_credential("cline-reg-test", raw_key)
        acct = self.auth_mgr.register_account(
            account_id="cline-reg-test",
            credential_reference=ref,
            display_name="Test Cline Account",
            persist_config=False,
        )

        self.assertEqual(acct.id, "cline-reg-test")
        self.assertEqual(acct.provider_id, "cline")
        self.assertEqual(acct.authentication_type, AuthenticationType.API_KEY)
        self.assertEqual(acct.credential_reference, ref)
        self.assertEqual(acct.status, AccountStatus.ONLINE)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.ONLINE)

        # Confirm to_dict never outputs raw key
        serialized = str(acct.to_dict())
        self.assertNotIn(raw_key, serialized)

    def test_setup_account_isolation_rejects_path_traversal(self):
        """Path traversal characters or invalid names in account_id must be rejected."""
        invalid_ids = [
            "../escaped",
            "../../etc/passwd",
            "sub/dir",
            "foo\\bar",
            "account;evil",
            "account*name",
            "",
            "   ",
        ]
        for bad_id in invalid_ids:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValueError):
                    self.auth_mgr.setup_account_isolation(bad_id)

    def test_discovery_fallback_flags_when_k_missing(self):
        """When discovery fails to detect -k, fallback flags are provided so keys are not dropped."""
        from unittest.mock import MagicMock, patch
        with patch.object(self.auth_mgr, "resolve_executable", return_value="/usr/bin/cline"), \
             patch("subprocess.run") as mock_run:
            m_res = MagicMock()
            m_res.returncode = 0
            m_res.stdout = "Usage: cline auth -p <provider>\n"  # No -k
            mock_run.return_value = m_res

            caps = self.auth_mgr.discover_capabilities(force_refresh=True)
            self.assertIn("-k", caps.auth_flags)
            self.assertIn("-m", caps.auth_flags)
            self.assertIn("-b", caps.auth_flags)
            self.assertIn("--config", caps.auth_flags)
            self.assertIn("--data-dir", caps.auth_flags)
            self.assertTrue(caps.supports_config_flag)
            self.assertTrue(caps.supports_data_dir_flag)


if __name__ == "__main__":
    unittest.main()
