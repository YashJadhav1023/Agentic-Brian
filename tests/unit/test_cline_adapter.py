"""Phase 10: Cline adapter contract and non-interference guarantees.

Cline holds live user credentials for its own configured providers, so the
Phase 10 brief forbids logging it out or overwriting its credentials. These
tests encode that boundary: Cline's configuration is observed, never authored.
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from agents.base.adapter import AgentAdapter, AgentStatus, Capability, ExecutionMode
from agents.cline.adapter import ClineAdapter
from providers.registry.bootstrap import create_default_registry


class TestClineAdapterContract(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ClineAdapter()

    def test_adapter_implements_the_common_agent_interface(self):
        self.assertIsInstance(self.adapter, AgentAdapter)
        for member in (
            "agent_id",
            "provider",
            "account_id",
            "execution_mode",
            "capabilities",
            "available_models",
            "status",
            "health",
            "execute",
            "continue_session",
            "cancel",
        ):
            self.assertTrue(hasattr(self.adapter, member), f"missing {member}")

    def test_identity(self):
        self.assertEqual(self.adapter.provider, "cline")
        self.assertEqual(self.adapter.agent_id, "cline")
        self.assertTrue(self.adapter.account_id)

    def test_execution_is_headless(self):
        self.assertIs(self.adapter.execution_mode, ExecutionMode.HEADLESS)

    def test_capabilities_reflect_a_focused_coding_agent(self):
        caps = self.adapter.capabilities()
        self.assertIsInstance(caps, frozenset)
        self.assertIn(Capability.EDITOR_REFACTORING, caps)
        for c in caps:
            self.assertIsInstance(c, Capability)

    def test_models_are_declared_and_non_empty(self):
        models = self.adapter.available_models()
        self.assertIsInstance(models, tuple)
        self.assertTrue(models)
        # `models` is the property alias of the `available_models()` method
        self.assertEqual(self.adapter.models, models)

    def test_initial_status_is_idle(self):
        self.assertIs(self.adapter.status(), AgentStatus.IDLE)

    def test_health_returns_a_boolean_and_a_reason_without_raising(self):
        healthy, reason = self.adapter.health()
        self.assertIsInstance(healthy, bool)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason)

    def test_health_reports_unavailable_for_a_missing_executable(self):
        adapter = ClineAdapter(executable="cline-does-not-exist-phase10")
        healthy, reason = adapter.health()
        self.assertFalse(healthy)
        self.assertTrue(reason)


class TestClineDirectoryBoundary(unittest.TestCase):
    """Config and data directories are injectable, and default to untouched."""

    def test_directories_default_to_none_so_the_installed_default_is_used(self):
        adapter = ClineAdapter()
        self.assertIsNone(adapter.config_dir)
        self.assertIsNone(adapter.data_dir)

    def test_directories_can_be_pointed_at_an_isolated_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClineAdapter(
                config_dir=Path(tmp) / "cfg", data_dir=Path(tmp) / "data"
            )
            self.assertEqual(adapter.config_dir, Path(tmp) / "cfg")
            self.assertEqual(adapter.data_dir, Path(tmp) / "data")

    def test_pointing_at_a_directory_does_not_create_or_write_it(self):
        """Construction must be inert; nothing is provisioned on the filesystem."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "never-created"
            ClineAdapter(config_dir=target, data_dir=target)
            self.assertFalse(target.exists())

    def test_user_paths_are_expanded_rather_than_treated_literally(self):
        adapter = ClineAdapter(config_dir="~/.cline-phase10-test")
        self.assertTrue(str(adapter.config_dir).startswith(str(Path.home())))


class TestClineAccountBoundary(unittest.TestCase):
    def test_adapter_exposes_an_account_parameterised_boundary(self):
        self.assertIn("account_id", inspect.signature(ClineAdapter.__init__).parameters)

    def test_isolated_cline_accounts_are_registered(self):
        reg = create_default_registry()
        cline_accounts = {a.id for a in reg.account_registry.list_accounts("cline")}
        for expected in ("cline-account-1", "cline-account-2", "cline-account-3"):
            self.assertIn(expected, cline_accounts)


class TestClineNonInterference(unittest.TestCase):
    def test_adapter_never_invokes_logout(self):
        self.assertNotIn("logout", inspect.getsource(ClineAdapter).lower())

    def test_adapter_never_writes_cline_credentials(self):
        source = inspect.getsource(ClineAdapter)
        for forbidden in ("credentials.json", "secret-tool store", "auth login"):
            self.assertNotIn(forbidden, source.lower())

    def test_construction_performs_no_authentication(self):
        source = inspect.getsource(ClineAdapter.__init__).lower()
        for forbidden in ("login", "logout", "credential"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
