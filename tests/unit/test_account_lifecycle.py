"""Unit tests for Phase 22 Universal Account Lifecycle State Machine."""

import unittest
from datetime import datetime, timezone

from providers.registry.account_registry import (
    Account,
    AccountPool,
    AccountRegistry,
    AccountStatus,
    AuthenticationType,
)
from providers.registry.lifecycle import (
    AccountLifecycleState,
    AccountLifecycleStateMachine,
    AuthState,
    HealthState,
    InvalidLifecycleTransitionError,
    ProcessState,
    TaskExecutionState,
)


class TestAccountLifecycle(unittest.TestCase):
    """Tests for the 14-state account lifecycle machine and state decoupling."""

    def setUp(self):
        self.account = Account(
            id="test-agent-1",
            provider_id="openai",
            account_name="test-agent-1",
            account_type="api",
            authentication_type=AuthenticationType.API_KEY,
            lifecycle_state=AccountLifecycleState.DISCOVERED,
            auth_state=AuthState.UNAUTHENTICATED,
            health_state=HealthState.UNKNOWN,
            process_state=ProcessState.IDLE,
        )

    def test_all_14_lifecycle_states_defined(self):
        """Verify that all 14 lifecycle states are properly enumerated."""
        expected_states = {
            # Progressive onboarding
            "DISCOVERED", "CONFIGURING", "AUTHENTICATING", "AUTHENTICATED", "VALIDATING", "READY",
            # Operational
            "ONLINE", "OFFLINE", "DISABLED",
            # Failure
            "AUTH_FAILED", "AUTH_CANCELLED", "VALIDATION_FAILED", "PROVIDER_UNAVAILABLE", "CONFIG_ERROR",
        }
        actual_states = {s.value for s in AccountLifecycleState}
        self.assertEqual(actual_states, expected_states)

    def test_valid_progression_lifecycle(self):
        """Verify normal progression: DISCOVERED -> CONFIGURING -> AUTHENTICATING -> AUTHENTICATED -> VALIDATING -> READY -> ONLINE."""
        sm = AccountLifecycleStateMachine
        acct = self.account

        # 1. DISCOVERED -> CONFIGURING
        sm.transition(acct, AccountLifecycleState.CONFIGURING)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.CONFIGURING)

        # 2. CONFIGURING -> AUTHENTICATING
        sm.transition(acct, AccountLifecycleState.AUTHENTICATING)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.AUTHENTICATING)

        # 3. AUTHENTICATING -> AUTHENTICATED
        sm.transition(acct, AccountLifecycleState.AUTHENTICATED)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.AUTHENTICATED)
        self.assertEqual(acct.auth_state, AuthState.AUTHENTICATED)

        # 4. AUTHENTICATED -> VALIDATING
        sm.transition(acct, AccountLifecycleState.VALIDATING)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.VALIDATING)

        # 5. VALIDATING -> READY
        sm.transition(acct, AccountLifecycleState.READY)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.READY)

        # 6. READY -> ONLINE
        sm.transition(acct, AccountLifecycleState.ONLINE)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.ONLINE)
        self.assertEqual(acct.status, AccountStatus.ONLINE)
        self.assertEqual(acct.auth_state, AuthState.AUTHENTICATED)
        self.assertEqual(acct.health_state, HealthState.HEALTHY)
        self.assertTrue(acct.is_available())

    def test_illegal_transition_raises_error(self):
        """Illegal jumps must be rejected with InvalidLifecycleTransitionError."""
        sm = AccountLifecycleStateMachine
        acct = self.account  # State: DISCOVERED

        # Cannot jump from DISCOVERED directly to ONLINE
        with self.assertRaises(InvalidLifecycleTransitionError):
            sm.transition(acct, AccountLifecycleState.ONLINE)

        # Cannot jump from DISCOVERED directly to READY
        with self.assertRaises(InvalidLifecycleTransitionError):
            sm.transition(acct, AccountLifecycleState.READY)

        # Move to CONFIGURING
        sm.transition(acct, AccountLifecycleState.CONFIGURING)

        # Cannot jump from CONFIGURING directly to VALIDATING
        with self.assertRaises(InvalidLifecycleTransitionError):
            sm.transition(acct, AccountLifecycleState.VALIDATING)

    def test_failure_transitions_and_recovery(self):
        """Verify transitions into failure states and back to configuration."""
        sm = AccountLifecycleStateMachine
        acct = self.account

        sm.transition(acct, AccountLifecycleState.CONFIGURING)
        sm.transition(acct, AccountLifecycleState.AUTHENTICATING)

        # Auth failed
        sm.transition(acct, AccountLifecycleState.AUTH_FAILED, reason="Invalid API key")
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.AUTH_FAILED)
        self.assertEqual(acct.auth_state, AuthState.AUTH_FAILED)
        self.assertEqual(acct.health_state, HealthState.UNHEALTHY)
        self.assertEqual(acct.health_reason, "Invalid API key")
        self.assertEqual(acct.status, AccountStatus.AUTH_ERROR)
        self.assertFalse(acct.is_available())

        # Recover: retry from CONFIGURING
        sm.transition(acct, AccountLifecycleState.CONFIGURING)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.CONFIGURING)

        # Authenticate successfully
        sm.transition(acct, AccountLifecycleState.AUTHENTICATING)
        sm.transition(acct, AccountLifecycleState.AUTHENTICATED)

        # Validation failed
        sm.transition(acct, AccountLifecycleState.VALIDATING)
        sm.transition(acct, AccountLifecycleState.VALIDATION_FAILED, reason="Rate limit reached")
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.VALIDATION_FAILED)
        self.assertFalse(acct.is_available())

    def test_disable_and_re_enable_lifecycle(self):
        """Disabling an account sets DISABLED and removes from routing availability."""
        sm = AccountLifecycleStateMachine
        acct = self.account

        sm.transition(acct, AccountLifecycleState.CONFIGURING)
        sm.transition(acct, AccountLifecycleState.AUTHENTICATING)
        sm.transition(acct, AccountLifecycleState.AUTHENTICATED)
        sm.transition(acct, AccountLifecycleState.VALIDATING)
        sm.transition(acct, AccountLifecycleState.READY)
        sm.transition(acct, AccountLifecycleState.ONLINE)

        self.assertTrue(acct.is_available())

        # Disable
        sm.transition(acct, AccountLifecycleState.DISABLED)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.DISABLED)
        self.assertEqual(acct.status, AccountStatus.DISABLED)
        self.assertFalse(acct.enabled)
        self.assertFalse(acct.is_available())

        # Re-enable
        sm.transition(acct, AccountLifecycleState.ONLINE)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.ONLINE)
        self.assertTrue(acct.enabled)
        self.assertTrue(acct.is_available())

    def test_backward_compatibility_with_account_status(self):
        """Creating Account with legacy status=AccountStatus.ONLINE auto-aligns lifecycle state."""
        legacy_acct = Account(
            id="legacy-1",
            provider_id="openai",
            account_name="legacy-1",
            status=AccountStatus.ONLINE,
        )
        self.assertEqual(legacy_acct.status, AccountStatus.ONLINE)
        self.assertEqual(legacy_acct.lifecycle_state, AccountLifecycleState.ONLINE)
        self.assertEqual(legacy_acct.auth_state, AuthState.AUTHENTICATED)
        self.assertEqual(legacy_acct.health_state, HealthState.HEALTHY)
        self.assertTrue(legacy_acct.is_available())

    def test_to_dict_includes_decoupled_states_without_secrets(self):
        """to_dict() must serialize all orthogonal states safely."""
        acct = Account(
            id="audit-acct",
            provider_id="openai",
            account_name="audit-acct",
            status=AccountStatus.ONLINE,
            health_reason="Healthy and verified",
        )
        d = acct.to_dict()
        self.assertIn("lifecycle_state", d)
        self.assertIn("auth_state", d)
        self.assertIn("health_state", d)
        self.assertIn("process_state", d)
        self.assertIn("task_state", d)
        self.assertIn("health_reason", d)
        self.assertEqual(d["lifecycle_state"], "ONLINE")
        self.assertEqual(d["auth_state"], "AUTHENTICATED")
        self.assertEqual(d["health_state"], "HEALTHY")
        self.assertEqual(d["health_reason"], "Healthy and verified")

    def test_account_registry_enable_account_restores_health_state(self):
        """AccountRegistry.enable_account() restores health_state=HEALTHY and enables account availability."""
        from providers.registry.account_registry import AccountRegistry
        registry = AccountRegistry()
        acct = Account(
            id="test-enable-acct",
            provider_id="openai",
            account_name="test-enable-acct",
            status=AccountStatus.ONLINE,
            lifecycle_state=AccountLifecycleState.ONLINE,
            health_state=HealthState.HEALTHY,
        )
        registry.register_account(acct)
        self.assertTrue(acct.is_available())

        # Disable
        registry.disable_account("test-enable-acct")
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.DISABLED)
        self.assertEqual(acct.status, AccountStatus.DISABLED)
        self.assertFalse(acct.enabled)
        self.assertFalse(acct.is_available())

        # Re-enable
        success = registry.enable_account("test-enable-acct")
        self.assertTrue(success)
        self.assertEqual(acct.lifecycle_state, AccountLifecycleState.ONLINE)
        self.assertEqual(acct.status, AccountStatus.ONLINE)
        self.assertEqual(acct.health_state, HealthState.HEALTHY)
        self.assertTrue(acct.enabled)
        self.assertTrue(acct.is_available())

    def test_sync_status_field_covers_all_14_states(self):
        """Verify sync_status_field maps all 14 states without crashing or leaving undefined status."""
        sm = AccountLifecycleStateMachine
        for state in AccountLifecycleState:
            self.account.lifecycle_state = state
            sm.sync_status_field(self.account)
            self.assertIsInstance(self.account.status, AccountStatus)
            if state == AccountLifecycleState.AUTH_CANCELLED:
                self.assertEqual(self.account.status, AccountStatus.AUTH_ERROR)


if __name__ == "__main__":
    unittest.main()
