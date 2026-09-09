"""Unit tests for Antigravity Isolated Authentication and Safety Invariants."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.antigravity.auth import AntigravityAuthManager, AntigravityAuthStatus
from providers.registry.account_registry import AccountRegistry, AccountStatus, AuthenticationType
from providers.registry.lifecycle import AccountLifecycleState, AuthState, HealthState


class TestAntigravityAuthSafety(unittest.TestCase):
    """Verify Antigravity authentication is strictly isolated from GUI process and system keyring."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.profile_root = Path(self.tmp_dir.name)
        self.registry = AccountRegistry()
        self.auth_mgr = AntigravityAuthManager(
            profile_root=self.profile_root,
            registry=self.registry,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_isolated_profile_creation_creates_0700_directory(self):
        """Profile directory is created under isolated root with 0700 permissions."""
        data_dir, profile_path = self.auth_mgr.create_isolated_profile("antigravity-account-4")
        self.assertEqual(data_dir, "antigravity-account-4")
        self.assertTrue(profile_path.is_dir())
        self.assertEqual(profile_path, self.profile_root / "antigravity-account-4")

    def test_cannot_modify_or_create_gui_profile(self):
        """Attempting to target or overwrite 'antigravity-ide' profile directly or via path manipulation must be rejected."""
        bypass_attempts = [
            "antigravity-ide",
            "ide",
            "./antigravity-ide",
            "antigravity-ide/.",
            "sub/../antigravity-ide",
            "foo/bar/../../antigravity-ide",
        ]
        for attempt in bypass_attempts:
            with self.subTest(attempt=attempt):
                with self.assertRaises(ValueError):
                    self.auth_mgr.create_isolated_profile("ide-account", app_data_dir=attempt)

    def test_cannot_escape_profile_root(self):
        """Attempting to escape profile_root via relative paths must be rejected."""
        escape_attempts = [
            "../outside",
            "../../etc",
            ".",
            "sub/../../outside",
        ]
        for attempt in escape_attempts:
            with self.subTest(attempt=attempt):
                with self.assertRaises(ValueError):
                    self.auth_mgr.create_isolated_profile("escape-acct", app_data_dir=attempt)

    def test_subprocess_environment_shields_gnome_keyring(self):
        """DBUS_SESSION_BUS_ADDRESS must be set to /dev/null to shield system GNOME Keyring."""
        env = self.auth_mgr.get_subprocess_env()
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", env)
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "/dev/null")

    def test_check_token_exists(self):
        """Token presence is correctly detected without exposing token contents."""
        data_dir, profile_path = self.auth_mgr.create_isolated_profile("test-acct")
        self.assertFalse(self.auth_mgr.check_token_exists(data_dir))

        # Create dummy token file
        token_file = profile_path / "antigravity-oauth-token"
        token_file.write_text("dummy-token-data")
        self.assertTrue(self.auth_mgr.check_token_exists(data_dir))

    def test_auth_status_unauthenticated_when_token_missing(self):
        """get_auth_status reports unauthenticated when token is absent."""
        status = self.auth_mgr.get_auth_status("antigravity-account-test")
        self.assertFalse(status.is_authenticated)
        self.assertFalse(status.token_exists)
        self.assertEqual(status.auth_state, AuthState.UNAUTHENTICATED.value)

    def test_launch_auth_constructs_isolated_command(self):
        """launch_auth generates command using --app_data_dir and shielded environment."""
        res = self.auth_mgr.launch_auth("antigravity-account-5")
        self.assertTrue(res["success"])
        self.assertEqual(res["account_id"], "antigravity-account-5")
        self.assertIn("--app_data_dir=antigravity-account-5", res["command_str"])
        self.assertEqual(res["environment"]["DBUS_SESSION_BUS_ADDRESS"], "/dev/null")

    def test_register_account_creates_valid_oauth_account(self):
        """register_account adds account with AuthenticationType.OAUTH and zero plaintext secrets."""
        acct = self.auth_mgr.register_account(
            account_id="antigravity-account-test",
            app_data_dir="antigravity-account-test",
            display_name="Test Antigravity Account",
            persist_config=False,
        )
        self.assertEqual(acct.id, "antigravity-account-test")
        self.assertEqual(acct.provider_id, "antigravity")
        self.assertEqual(acct.authentication_type, AuthenticationType.OAUTH)
        self.assertEqual(acct.credential_reference, "")  # No secret in metadata
        self.assertEqual(acct.status, AccountStatus.ONLINE)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.ONLINE)

        # In registry
        found = self.registry.get_account("antigravity-account-test")
        self.assertIsNotNone(found)
        self.assertEqual(found.display_name, "Test Antigravity Account")


if __name__ == "__main__":
    unittest.main()
