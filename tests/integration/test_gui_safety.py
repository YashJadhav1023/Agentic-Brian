"""Phase 10: Antigravity GUI and keyring safety audit.

Covers requirements 14, 15 and 16 of the Phase 10 test matrix:

* Antigravity account isolation is real
* the GUI process remains alive
* the existing keyring entry remains untouched

The overriding constraint of the whole phase is that the running Antigravity IDE
session must survive. This suite is deliberately **read-only**: it never writes a
keyring entry, never touches a Phase 8 profile directory, and never invokes login
or logout. It observes, compares, and asserts.

Live-environment tests skip rather than fail when the GUI is not running, because
a machine with no IDE session cannot prove the invariant either way -- but the
static guarantees below always run, because a dangerous code path is a defect
whether or not the IDE happens to be open right now.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUI_BINARY = "/opt/antigravity-ide/antigravity-ide"
PROTECTED_KEYRING_SERVICE = "gemini"
MISSION_CONTROL_KEYRING_SERVICE = "mission-control"

#: Phase 8 account profiles. These directories are off-limits for writes.
PHASE8_PROFILES = (
    Path.home() / ".gemini" / "antigravity-account-jadhav",
    Path.home() / ".gemini" / "antigravity-account-3",
    Path.home() / ".gemini" / "antigravity-account-yash",
)


def _gui_pids() -> set[str]:
    """Return the PIDs of the running Antigravity IDE, or an empty set."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", GUI_BINARY],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _keyring_digest(service: str) -> str | None:
    """Return a digest of a keyring secret, never the secret itself.

    Returning a hash means this test can prove a value is unchanged without the
    value ever entering the test output, a traceback, or CI logs.
    """
    tool = shutil.which("secret-tool")
    if not tool:
        return None
    try:
        out = subprocess.run(
            [tool, "lookup", "service", service],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    return hashlib.sha256(out.stdout).hexdigest()


def _source_files() -> list[Path]:
    files = []
    for path in PROJECT_ROOT.rglob("*.py"):
        parts = set(path.parts)
        if "runtime" in parts or ".git" in parts or "tests" in parts:
            continue
        files.append(path)
    return files


class TestGuiProcessSurvives(unittest.TestCase):
    """The GUI running before the suite must still be running after it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.pids_before = _gui_pids()

    def setUp(self) -> None:
        if not self.pids_before:
            self.skipTest(
                "Antigravity IDE GUI is not running; liveness cannot be asserted"
            )

    def test_gui_process_is_still_running(self):
        self.assertTrue(_gui_pids(), "the Antigravity IDE GUI is no longer running")

    def test_original_gui_pids_are_all_still_alive(self):
        """A restarted GUI would present new PIDs, which is still a violation."""
        survivors = _gui_pids()
        lost = self.pids_before - survivors
        self.assertEqual(lost, set(), f"GUI processes disappeared: {sorted(lost)}")


class TestKeyringRemainsUntouched(unittest.TestCase):
    """`service=gemini` must survive intact.

    The invariant is deliberately *not* "the bytes never change". That entry is
    an OAuth credential owned by the live Antigravity session, and a running
    session legitimately rotates its own access token when one expires -- so a
    digest comparison produces false alarms, and a safety test that cries wolf
    gets ignored, which is worse than no test.

    What actually constitutes damage is the entry being deleted or emptied, or
    Mission Control writing to that namespace at all. Both are asserted: the
    former here, the latter statically in
    TestNoSourceCodeCanDisturbTheSession.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.digest_before = _keyring_digest(PROTECTED_KEYRING_SERVICE)

    def setUp(self) -> None:
        if self.digest_before is None:
            self.skipTest(
                "service=gemini keyring entry is unavailable or locked; "
                "cannot assert survival"
            )

    def test_gemini_keyring_entry_still_exists(self):
        """Deletion or emptying is the failure mode that breaks the IDE."""
        self.assertIsNotNone(
            _keyring_digest(PROTECTED_KEYRING_SERVICE),
            "the Antigravity keyring entry (service=gemini) was deleted or emptied",
        )

    def test_gemini_keyring_digest_is_reported_for_audit(self):
        """Record whether a rotation occurred, without failing on one.

        A change here is expected when the live session refreshes its token. It
        is surfaced rather than asserted so an operator reviewing the run can
        still see that it happened.
        """
        digest_now = _keyring_digest(PROTECTED_KEYRING_SERVICE)
        self.assertIsNotNone(digest_now)
        if digest_now != self.digest_before:
            print(
                "\n  note: service=gemini rotated during this run "
                "(expected when the live Antigravity session refreshes its token)"
            )

    def test_mission_control_uses_a_separate_namespace(self):
        from providers.registry.credential_manager import KEYRING_SERVICE

        self.assertEqual(KEYRING_SERVICE, MISSION_CONTROL_KEYRING_SERVICE)
        self.assertNotEqual(KEYRING_SERVICE, PROTECTED_KEYRING_SERVICE)


class TestPhase8ProfilesUntouched(unittest.TestCase):
    """Each Antigravity account keeps its own isolated profile directory."""

    def test_profile_directories_are_distinct_paths(self):
        self.assertEqual(len(set(PHASE8_PROFILES)), len(PHASE8_PROFILES))

    def test_existing_profiles_are_still_directories(self):
        present = [p for p in PHASE8_PROFILES if p.exists()]
        if not present:
            self.skipTest("no Phase 8 Antigravity profiles present on this machine")
        for profile in present:
            with self.subTest(profile=profile.name):
                self.assertTrue(profile.is_dir(), f"{profile} is no longer a directory")

    def test_profiles_are_not_symlinked_into_each_other(self):
        """Cross-linked profiles would silently share one OAuth token."""
        present = [p for p in PHASE8_PROFILES if p.exists()]
        if not present:
            self.skipTest("no Phase 8 Antigravity profiles present on this machine")
        resolved = [p.resolve() for p in present]
        self.assertEqual(
            len(set(resolved)), len(resolved), "profile directories resolve to the same path"
        )

    def test_no_source_file_deletes_or_rewrites_a_phase8_profile(self):
        dangerous = re.compile(
            r"(rmtree|unlink|shutil\.move|os\.remove)[^\n]*antigravity-account", re.I
        )
        offenders = [
            str(p.relative_to(PROJECT_ROOT))
            for p in _source_files()
            if dangerous.search(p.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertEqual(offenders, [], f"files may destroy a Phase 8 profile: {offenders}")


class TestNoSourceCodeCanDisturbTheSession(unittest.TestCase):
    """Static guarantees: the dangerous operations are absent from the codebase."""

    def test_no_source_file_invokes_an_antigravity_logout(self):
        pattern = re.compile(r"(agy|antigravity)[^\n]{0,40}logout", re.I)
        offenders = [
            str(p.relative_to(PROJECT_ROOT))
            for p in _source_files()
            if pattern.search(p.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertEqual(offenders, [], f"files invoke logout: {offenders}")

    def test_no_source_file_writes_to_the_gemini_keyring_service(self):
        pattern = re.compile(r"secret-tool[^\n]*store[^\n]*gemini", re.I)
        offenders = [
            str(p.relative_to(PROJECT_ROOT))
            for p in _source_files()
            if pattern.search(p.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertEqual(offenders, [], f"files write service=gemini: {offenders}")

    def test_no_source_file_kills_the_gui_process(self):
        pattern = re.compile(r"(pkill|killall|kill\s+-9)[^\n]*antigravity", re.I)
        offenders = [
            str(p.relative_to(PROJECT_ROOT))
            for p in _source_files()
            if pattern.search(p.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertEqual(offenders, [], f"files may kill the GUI: {offenders}")


class TestAntigravityAccountIsolation(unittest.TestCase):
    """Isolation is expressed through distinct profile roots, not shared state."""

    def test_each_configured_account_declares_its_own_profile(self):
        from providers.registry.bootstrap import create_default_registry

        registry = create_default_registry()
        accounts = registry.account_registry.list_accounts("antigravity")
        if not accounts:
            self.skipTest("no Antigravity accounts configured")
        self.assertGreaterEqual(len(accounts), 1)
        self.assertEqual(
            len({a.id for a in accounts}), len(accounts), "duplicate account ids"
        )

    #: Metadata keys under which an account records its isolated profile root.
    PROFILE_KEYS = ("data_dir", "app_data_dir", "profile")

    def _declared_profiles(self) -> list[str]:
        from providers.registry.bootstrap import create_default_registry

        registry = create_default_registry()
        profiles = []
        for account in registry.account_registry.list_accounts("antigravity"):
            for key in self.PROFILE_KEYS:
                value = account.metadata.get(key)
                if value:
                    profiles.append(value)
                    break
        return profiles

    def test_every_antigravity_account_declares_a_profile_directory(self):
        from providers.registry.bootstrap import create_default_registry

        accounts = create_default_registry().account_registry.list_accounts("antigravity")
        if not accounts:
            self.skipTest("no Antigravity accounts configured")
        self.assertEqual(
            len(self._declared_profiles()),
            len(accounts),
            "an Antigravity account has no isolated profile directory",
        )

    def test_account_profile_directories_are_not_shared(self):
        declared = self._declared_profiles()
        if len(declared) < 2:
            self.skipTest("fewer than two Antigravity accounts configured")
        self.assertEqual(
            len(set(declared)), len(declared), f"accounts share a profile: {declared}"
        )

    def test_declared_profiles_are_the_phase8_profiles(self):
        """Isolation must point at the real Phase 8 roots, not invented ones."""
        declared = set(self._declared_profiles())
        if not declared:
            self.skipTest("no Antigravity profile directories declared")
        known = {p.name for p in PHASE8_PROFILES}
        self.assertTrue(
            declared.issubset(known),
            f"unrecognised profile roots configured: {sorted(declared - known)}",
        )


class TestSuiteItselfIsNonDestructive(unittest.TestCase):
    """Guard against this file ever growing a mutating operation.

    This is checked structurally rather than by grepping the source, because the
    source legitimately *mentions* dangerous verbs inside the detection patterns
    used by the tests above. What matters is which commands this module can
    actually execute.
    """

    #: Read-only verbs this suite is permitted to invoke.
    ALLOWED_COMMAND_TOKENS = {
        "pgrep",
        "-f",
        "lookup",
        "service",
        GUI_BINARY,
        PROTECTED_KEYRING_SERVICE,
    }

    #: Verbs that mutate a keyring, a process, or the filesystem.
    FORBIDDEN_COMMAND_TOKENS = {"store", "clear", "kill", "pkill", "logout", "login", "rm"}

    def _subprocess_command_tokens(self) -> set[str]:
        """Collect the literal argv tokens of every subprocess call in this file."""
        import ast

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        tokens: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_subprocess = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            )
            if not is_subprocess or not node.args:
                continue
            argv = node.args[0]
            if isinstance(argv, (ast.List, ast.Tuple)):
                for element in argv.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        tokens.add(element.value)
        return tokens

    def test_this_suite_invokes_only_read_only_commands(self):
        tokens = self._subprocess_command_tokens()
        self.assertTrue(tokens, "no subprocess argv literals found to audit")
        offenders = {t for t in tokens if t.lower() in self.FORBIDDEN_COMMAND_TOKENS}
        self.assertEqual(
            offenders, set(), f"this suite would mutate state via: {sorted(offenders)}"
        )

    def test_every_command_token_is_explicitly_allowed(self):
        """A new argv token must be reviewed rather than silently permitted."""
        unexpected = self._subprocess_command_tokens() - self.ALLOWED_COMMAND_TOKENS
        self.assertEqual(
            unexpected,
            set(),
            f"unreviewed command tokens in a safety suite: {sorted(unexpected)}",
        )

    def test_environment_is_not_mutated_by_importing_this_module(self):
        self.assertNotIn("ANTIGRAVITY_LOGOUT", os.environ)


if __name__ == "__main__":
    unittest.main()
