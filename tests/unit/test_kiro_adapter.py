"""Phase 10: Kiro adapter contract and non-interference guarantees.

The Phase 10 brief is explicit that the running Kiro installation must not be
disturbed and that its authentication mechanism must not be assumed. These tests
encode both constraints, plus the requirement that if isolated accounts are not
safely supported then isolation must not be faked.
"""
from __future__ import annotations

import inspect
import unittest

from agents.base.adapter import AgentAdapter, AgentStatus, Capability, ExecutionMode
from agents.kiro.adapter import KiroAdapter
from providers.registry.bootstrap import create_default_registry


class TestKiroAdapterContract(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = KiroAdapter()

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
        self.assertEqual(self.adapter.provider, "kiro")
        self.assertEqual(self.adapter.agent_id, "kiro-cli")
        self.assertTrue(self.adapter.account_id)

    def test_execution_is_headless(self):
        self.assertIs(self.adapter.execution_mode, ExecutionMode.HEADLESS)

    def test_capabilities_reflect_a_terminal_and_cloud_agent(self):
        caps = self.adapter.capabilities()
        self.assertIsInstance(caps, frozenset)
        self.assertIn(Capability.TERMINAL_OPERATIONS, caps)
        for c in caps:
            self.assertIsInstance(c, Capability)

    def test_models_are_declared_and_non_empty(self):
        models = self.adapter.available_models()
        self.assertIsInstance(models, tuple)
        self.assertTrue(models)

    def test_initial_status_is_idle(self):
        self.assertIs(self.adapter.status(), AgentStatus.IDLE)

    def test_health_returns_a_boolean_and_a_reason_without_raising(self):
        healthy, reason = self.adapter.health()
        self.assertIsInstance(healthy, bool)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason)

    def test_health_reports_unavailable_for_a_missing_executable(self):
        adapter = KiroAdapter(executable="kiro-cli-does-not-exist-phase10")
        healthy, reason = adapter.health()
        self.assertFalse(healthy)
        self.assertTrue(reason)

    def test_capabilities_and_models_are_injectable_from_configuration(self):
        adapter = KiroAdapter(
            capabilities=frozenset({Capability.CODE_REVIEW}),
            models=("only-model",),
            agent_id="kiro-custom",
            account_id="acct-x",
        )
        self.assertEqual(adapter.capabilities(), frozenset({Capability.CODE_REVIEW}))
        self.assertEqual(adapter.available_models(), ("only-model",))
        self.assertEqual(adapter.agent_id, "kiro-custom")
        self.assertEqual(adapter.account_id, "acct-x")


class TestKiroAccountBoundary(unittest.TestCase):
    """Isolation must be a real capability or an honest absence, never a pretence."""

    def test_adapter_exposes_an_account_parameterised_boundary(self):
        """`account_id` is a constructor parameter, so isolation can be added later."""
        params = inspect.signature(KiroAdapter.__init__).parameters
        self.assertIn("account_id", params)

    def test_no_fabricated_kiro_account_profiles_are_registered(self):
        """`kiro-account-1..3` must not exist while isolation is unverified.

        Registering them would imply three isolated identities that actually
        share one credential store.
        """
        reg = create_default_registry()
        kiro_accounts = {a.id for a in reg.account_registry.list_accounts("kiro")}
        for faked in ("kiro-account-1", "kiro-account-2", "kiro-account-3"):
            self.assertNotIn(faked, kiro_accounts)

    def test_adapter_declares_no_profile_isolation_flag(self):
        """Unlike Antigravity's --app_data_dir, Kiro gets no profile-root option."""
        params = inspect.signature(KiroAdapter.__init__).parameters
        for isolation_param in ("app_data_dir", "profile_root", "profile"):
            self.assertNotIn(isolation_param, params)


class TestKiroNonInterference(unittest.TestCase):
    """Constructing or probing the adapter must not mutate the installation."""

    def test_adapter_construction_performs_no_authentication(self):
        source = inspect.getsource(KiroAdapter.__init__)
        for forbidden in ("login", "logout", "auth", "credential"):
            self.assertNotIn(forbidden, source.lower())

    def test_adapter_never_invokes_logout_anywhere(self):
        source = inspect.getsource(KiroAdapter)
        self.assertNotIn("logout", source.lower())

    def test_health_probe_does_not_shell_out_a_login_command(self):
        source = inspect.getsource(KiroAdapter.health)
        self.assertNotIn("login", source.lower())


if __name__ == "__main__":
    unittest.main()
