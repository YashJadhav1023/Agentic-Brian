"""Part 14 — Safe account removal tests (Phase 22, Part 13).

Removal is the most destructive operation in the account subsystem, so these
tests concentrate on what it must *refuse* to do:

* never delete the live Antigravity IDE GUI profile,
* never delete a runtime root itself,
* never escape the allowed runtime roots,
* never delete an account that still has work in flight,
* never touch a sibling account's credential or directories.

Filesystem assertions deliberately use only the Cline runtime root
(``~/.mission-control/cline``). Nothing here creates, deletes, or stats a path
under ``~/.gemini``, because that root is owned by the running GUI and Part 15
requires its mtime to remain untouched. The ``~/.gemini`` protections are
asserted through path validation, which resolves paths without needing them to
exist.
"""

import unittest
import uuid
from pathlib import Path

from providers.registry.account_registry import (
    Account,
    AccountRegistry,
    AuthenticationType,
)
from providers.registry.account_removal import (
    FROZEN_ACCOUNT_IDS,
    PROTECTED_PROFILE_NAMES,
    ProtectedProfileError,
    RemovalResult,
    assert_removable_profile,
)
from providers.registry.lifecycle import AccountLifecycleState

_GEMINI_ROOT = Path.home() / ".gemini"
_CLINE_ROOT = Path.home() / ".mission-control" / "cline"


class TestRemovalPathGuard(unittest.TestCase):
    """assert_removable_profile is the single hard guard on deletion."""

    def test_protected_gui_profiles_are_refused_by_name(self):
        for name in PROTECTED_PROFILE_NAMES:
            with self.assertRaises(ProtectedProfileError):
                assert_removable_profile(_GEMINI_ROOT / name)

    def test_the_live_ide_profile_is_refused(self):
        """The IDE's own profile is the one directory that must never be removed."""
        with self.assertRaises(ProtectedProfileError):
            assert_removable_profile(_GEMINI_ROOT / "antigravity-ide")

    def test_runtime_roots_themselves_are_refused(self):
        for root in (_GEMINI_ROOT, _CLINE_ROOT):
            with self.assertRaises(ProtectedProfileError):
                assert_removable_profile(root)

    def test_an_ancestor_of_a_protected_profile_is_refused(self):
        """Deleting ~ or ~/.gemini's parent would take the GUI profile with it."""
        with self.assertRaises(ProtectedProfileError):
            assert_removable_profile(Path.home())

    def test_paths_outside_the_allowed_roots_are_refused(self):
        for outside in ("/tmp", "/etc", str(Path.home() / "Documents"), "/"):
            with self.assertRaises(ProtectedProfileError):
                assert_removable_profile(Path(outside))

    def test_traversal_cannot_escape_an_allowed_root(self):
        """A crafted path must be judged after resolution, not before."""
        escape = _CLINE_ROOT / ".." / ".." / ".." / "etc"
        with self.assertRaises(ProtectedProfileError):
            assert_removable_profile(escape)

    def test_traversal_back_into_a_protected_profile_is_refused(self):
        escape = _CLINE_ROOT / ".." / ".." / ".gemini" / "antigravity-ide"
        with self.assertRaises(ProtectedProfileError):
            assert_removable_profile(escape)

    def test_a_genuine_isolated_profile_is_allowed(self):
        candidate = _CLINE_ROOT / f"phase22-guard-{uuid.uuid4().hex[:8]}"
        resolved = assert_removable_profile(candidate)
        self.assertEqual(resolved, candidate.resolve())


class TestRemovalResultShape(unittest.TestCase):
    """The result must be serialisable and free of secret material."""

    def test_result_dict_carries_outcome_without_credentials(self):
        result = RemovalResult(account_id="rm-1", provider_id="cline")
        result.credential_deleted = True
        payload = result.to_dict()

        for key in (
            "account_id",
            "provider_id",
            "removed",
            "already_absent",
            "credential_deleted",
            "directories_removed",
            "directories_skipped",
            "drained_inflight",
            "correlation_id",
            "error",
        ):
            self.assertIn(key, payload)

        # The result reports *that* a credential went away, never its value.
        self.assertIsInstance(payload["credential_deleted"], bool)
        self.assertNotIn("api_key", payload)
        self.assertNotIn("credential", payload)


class TestSafeRemoveAccount(unittest.TestCase):
    """End-to-end removal behaviour through the registry."""

    def setUp(self) -> None:
        self.registry = AccountRegistry()
        self.account = Account(
            id=f"cline-rm-{uuid.uuid4().hex[:8]}",
            provider_id="cline",
            account_name="removable",
            authentication_type=AuthenticationType.API_KEY,
        )
        self.registry.register_account(self.account)

    def test_removing_an_unknown_account_is_an_idempotent_success(self):
        """Re-running a removal must not error, so retries and replays are safe."""
        result = self.registry.safe_remove_account("does-not-exist")
        self.assertTrue(result.removed)
        self.assertTrue(result.already_absent)
        self.assertIsNone(result.error)

    def test_frozen_accounts_are_refused(self):
        for frozen_id in FROZEN_ACCOUNT_IDS:
            result = self.registry.safe_remove_account(frozen_id)
            self.assertFalse(result.removed)
            self.assertIsNotNone(result.error)
            self.assertIn("frozen", result.error.lower())

    def test_removal_detaches_the_account_from_registry_and_pool(self):
        acct_id = self.account.id
        result = self.registry.safe_remove_account(acct_id, remove_directories=False)

        self.assertTrue(result.removed)
        self.assertIsNone(self.registry.get_account(acct_id))
        self.assertNotIn(
            acct_id, [a.id for a in self.registry.get_accounts_for_provider("cline")]
        )

    def test_removal_carries_a_correlation_id_for_audit_tracing(self):
        result = self.registry.safe_remove_account(
            self.account.id, remove_directories=False
        )
        self.assertTrue(result.correlation_id)

    def test_inflight_work_blocks_removal_and_leaves_account_disabled(self):
        """Removal drains rather than kills: live work is never destroyed."""
        self.account.current_concurrency = 1
        acct_id = self.account.id

        result = self.registry.safe_remove_account(
            acct_id, remove_directories=False, drain_timeout=0.0
        )

        self.assertFalse(result.removed)
        self.assertIsNotNone(result.error)
        self.assertIn("in-flight", result.error)
        # The account survives, but disabled so no new work is admitted.
        survivor = self.registry.get_account(acct_id)
        self.assertIsNotNone(survivor)
        self.assertEqual(survivor.lifecycle_state, AccountLifecycleState.DISABLED)
        self.assertFalse(survivor.is_available())

    def test_removal_does_not_touch_sibling_accounts(self):
        sibling = Account(
            id=f"cline-keep-{uuid.uuid4().hex[:8]}",
            provider_id="cline",
            account_name="keeper",
            credential_reference="secret://mission-control/cline/keeper",
        )
        self.registry.register_account(sibling)

        self.registry.safe_remove_account(self.account.id, remove_directories=False)

        kept = self.registry.get_account(sibling.id)
        self.assertIsNotNone(kept)
        self.assertEqual(
            kept.credential_reference, "secret://mission-control/cline/keeper"
        )

    def test_removal_only_deletes_this_accounts_own_directory(self):
        """A real directory under the Cline root is removed; a sibling's is not."""
        _CLINE_ROOT.mkdir(parents=True, exist_ok=True)
        mine = _CLINE_ROOT / f"phase22-mine-{uuid.uuid4().hex[:8]}"
        theirs = _CLINE_ROOT / f"phase22-theirs-{uuid.uuid4().hex[:8]}"
        mine.mkdir()
        theirs.mkdir()
        self.addCleanup(lambda: mine.exists() and mine.rmdir())
        self.addCleanup(lambda: theirs.exists() and theirs.rmdir())

        self.account.metadata["profile_dir"] = str(mine)
        result = self.registry.safe_remove_account(self.account.id)

        self.assertTrue(result.removed)
        self.assertFalse(mine.exists(), "the account's own profile should be removed")
        self.assertTrue(theirs.exists(), "a sibling's profile must be untouched")

    def test_removal_refuses_when_metadata_points_at_a_protected_profile(self):
        """A malicious or corrupt metadata entry must not delete the GUI profile."""
        self.account.metadata["profile_dir"] = str(_GEMINI_ROOT / "antigravity-ide")
        with self.assertRaises(ProtectedProfileError):
            self.registry.safe_remove_account(self.account.id)

        # The GUI profile guard fires before any deletion, and the account
        # remains present rather than being half-removed.
        self.assertIsNotNone(self.registry.get_account(self.account.id))


if __name__ == "__main__":
    unittest.main()
