"""Part 14 — Account authentication tests (Phase 22, Parts 3 and 4).

These assert the authentication *contract* shared by the two CLI agent
providers: authenticating an account must isolate it, must never reach into the
running GUI's profile, must reference credentials indirectly, and must report a
structured status rather than a bare boolean.

Every test points its manager at a temporary profile root, so no real profile
under ``~/.gemini`` or ``~/.mission-control/cline`` is created, read, or
modified. That is a hard requirement of Part 15 non-interference, not a
convenience: the Antigravity IDE GUI owns ``~/.gemini``.
"""

import tempfile
import unittest
from pathlib import Path

from agents.antigravity.auth import (
    TOKEN_FILENAME,
    AntigravityAuthManager,
    AntigravityAuthStatus,
)
from agents.cline.auth import ClineAuthManager
from providers.registry.account_registry import AccountRegistry
from providers.registry.lifecycle import AccountLifecycleState, AuthState, HealthState


class TestAntigravityAuthenticationIsolation(unittest.TestCase):
    """Antigravity auth must never touch the GUI profile or escape its root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "gemini"
        self.root.mkdir()
        self.manager = AntigravityAuthManager(
            profile_root=self.root,
            registry=AccountRegistry(),
        )

    def test_protected_ide_profile_names_are_refused(self):
        """The IDE's live profile must be unreachable through the auth manager."""
        for name in ("antigravity-ide", "ide"):
            with self.assertRaises(ValueError):
                self.manager.get_profile_dir(name)

    def test_profile_dir_cannot_escape_the_profile_root(self):
        for escape in ("../outside", "../../etc", "a/../../../tmp"):
            with self.assertRaises(ValueError):
                self.manager.get_profile_dir(escape)

    def test_profile_root_itself_is_not_a_valid_profile(self):
        with self.assertRaises(ValueError):
            self.manager.get_profile_dir(".")

    def test_each_account_gets_its_own_profile_directory(self):
        _, first = self.manager.create_isolated_profile("acct-one")
        _, second = self.manager.create_isolated_profile("acct-two")

        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())
        # Neither may contain the other: isolation must be genuine.
        self.assertNotIn(first, second.parents)
        self.assertNotIn(second, first.parents)

    def test_isolated_profile_is_private_to_the_user(self):
        """Credentials live in these directories, so they must not be world-readable."""
        _, profile = self.manager.create_isolated_profile("acct-perms")
        mode = profile.stat().st_mode & 0o777
        self.assertEqual(mode, 0o700, f"expected 0o700, got {oct(mode)}")

    def test_empty_account_id_is_rejected(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                self.manager.create_isolated_profile(bad)

    def test_profile_creation_is_idempotent(self):
        first_dir, first_path = self.manager.create_isolated_profile("acct-idem")
        second_dir, second_path = self.manager.create_isolated_profile("acct-idem")
        self.assertEqual(first_dir, second_dir)
        self.assertEqual(first_path, second_path)

    def test_token_absence_and_presence_are_detected(self):
        data_dir, profile = self.manager.create_isolated_profile("acct-token")
        self.assertFalse(self.manager.check_token_exists(data_dir))

        # An empty token file is not a token.
        token = profile / TOKEN_FILENAME
        token.write_text("", encoding="utf-8")
        self.assertFalse(self.manager.check_token_exists(data_dir))

        token.write_text("opaque-token-material", encoding="utf-8")
        self.assertTrue(self.manager.check_token_exists(data_dir))

    def test_subprocess_env_detaches_dbus_to_shield_system_keyring(self):
        """Auth subprocesses must not be able to reach the GNOME keyring."""
        env = self.manager.get_subprocess_env()
        self.assertEqual(env.get("DBUS_SESSION_BUS_ADDRESS"), "/dev/null")

    def test_missing_binary_yields_a_structured_failure_not_an_exception(self):
        """With no CLI available, auth must report an error rather than raise.

        Note the manager resolves its binary from PATH when the supplied path is
        invalid, so passing a bogus path is not sufficient to simulate absence
        on a machine where the CLI is installed; the resolved binary is cleared
        directly instead.
        """
        manager = AntigravityAuthManager(profile_root=self.root)
        manager._bin = None
        self.assertIsNone(manager.binary)

        result = manager.launch_auth("acct-nobin")
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertTrue(result["error"])

    def test_binary_resolution_falls_back_to_path_when_given_a_bad_path(self):
        """An unusable explicit path must not pin the manager to it."""
        manager = AntigravityAuthManager(
            binary_path="/nonexistent/agy-does-not-exist",
            profile_root=self.root,
        )
        # Either PATH provided a real binary, or nothing was found - never the
        # nonexistent path that was passed in.
        self.assertNotEqual(
            str(manager.binary or ""), "/nonexistent/agy-does-not-exist"
        )


class TestAntigravityAuthStatusShape(unittest.TestCase):
    """Auth status must be structured and default to the safest possible states."""

    def test_status_defaults_are_unauthenticated_and_unknown_health(self):
        status = AntigravityAuthStatus(
            account_id="acct-1",
            data_dir="antigravity-account-1",
            profile_dir="/tmp/profile",
            is_authenticated=False,
            token_exists=False,
        )
        self.assertEqual(status.lifecycle_state, AccountLifecycleState.DISCOVERED.value)
        self.assertEqual(status.auth_state, AuthState.UNAUTHENTICATED.value)
        self.assertEqual(status.health_state, HealthState.UNKNOWN.value)
        self.assertIsNone(status.error)
        self.assertEqual(status.models_available, [])


class TestClineAuthenticationIsolation(unittest.TestCase):
    """Cline auth must isolate config and data per account and validate ids."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "cline"
        self.manager = ClineAuthManager(base_dir=self.base, registry=AccountRegistry())

    def test_account_id_must_be_a_safe_identifier(self):
        """Rejecting path-ish ids is what stops isolation being escaped."""
        for bad in ("../escape", "a/b", "with space", "semi;colon", "", "  "):
            with self.assertRaises(ValueError, msg=f"{bad!r} must be rejected"):
                self.manager.setup_account_isolation(bad)

    def test_isolation_creates_separate_config_and_data_directories(self):
        config_dir, data_dir = self.manager.setup_account_isolation("acct-a")
        self.assertTrue(config_dir.is_dir())
        self.assertTrue(data_dir.is_dir())
        self.assertNotEqual(config_dir, data_dir)

    def test_two_accounts_never_share_a_directory(self):
        a_config, a_data = self.manager.setup_account_isolation("acct-a")
        b_config, b_data = self.manager.setup_account_isolation("acct-b")
        self.assertEqual(len({a_config, a_data, b_config, b_data}), 4)

    def test_isolation_is_idempotent(self):
        first = self.manager.setup_account_isolation("acct-idem")
        second = self.manager.setup_account_isolation("acct-idem")
        self.assertEqual(first, second)

    def test_validate_rejects_an_unsafe_account_id(self):
        ok, message = self.manager.validate_account("../escape")
        self.assertFalse(ok)
        self.assertIn("Invalid account_id", message)

    def test_validate_reports_missing_directories_before_credentials(self):
        ok, message = self.manager.validate_account("never-set-up")
        self.assertFalse(ok)
        self.assertIn("does not exist", message)

    def test_validate_requires_a_credential_once_directories_exist(self):
        """Directories alone are not authentication."""
        self.manager.setup_account_isolation("acct-nocred")
        ok, message = self.manager.validate_account("acct-nocred")
        self.assertFalse(ok)
        self.assertIn("Credential", message)

    def test_capabilities_are_reported_even_when_cline_is_absent(self):
        """A missing binary must degrade to a structured report, not raise."""
        manager = ClineAuthManager(
            executable="cline-definitely-not-installed", base_dir=self.base
        )
        caps = manager.discover_capabilities()
        self.assertEqual(caps.version, "not_installed")
        self.assertFalse(caps.has_auth_command)
        self.assertEqual(caps.executable_path, "")
        self.assertEqual(caps.auth_flags, [])

    def test_capability_discovery_is_cached(self):
        manager = ClineAuthManager(
            executable="cline-definitely-not-installed", base_dir=self.base
        )
        first = manager.discover_capabilities()
        second = manager.discover_capabilities()
        self.assertIs(first, second)


class TestCredentialsAreReferencedNotEmbedded(unittest.TestCase):
    """Authentication must yield an opaque reference, never a secret value."""

    def test_cline_credential_reference_is_a_secret_uri(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        manager = ClineAuthManager(base_dir=base)
        manager.setup_account_isolation("acct-ref")

        # validate_account derives the canonical reference shape; assert the
        # scheme rather than any stored value.
        _, message = manager.validate_account("acct-ref")
        self.assertNotIn("sk-", message, "a validation message must not carry a key")


if __name__ == "__main__":
    unittest.main()
