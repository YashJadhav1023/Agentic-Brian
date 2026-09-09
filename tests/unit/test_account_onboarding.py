"""Part 14 — Account onboarding flow tests (Phase 22).

Covers the progressive onboarding path an account walks the first time it is
introduced to Mission Control:

    DISCOVERED -> CONFIGURING -> AUTHENTICATING -> AUTHENTICATED
               -> VALIDATING -> READY -> ONLINE

and the failure branches that must be reachable from each step. These assert the
*contract* of the lifecycle machine rather than any single caller, because the
dashboard wizard, the CLI, and the registry all drive the same transitions.
"""

import unittest

from providers.registry.account_registry import (
    Account,
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
)


class TestAccountOnboardingHappyPath(unittest.TestCase):
    """A newly discovered account must reach ONLINE only through legal steps."""

    def setUp(self) -> None:
        self.registry = AccountRegistry()
        self.account = Account(
            id="onboard-1",
            provider_id="openai",
            account_name="onboard-1",
            authentication_type=AuthenticationType.API_KEY,
        )
        self.registry.register_account(self.account)

    def test_new_account_starts_discovered_and_unauthenticated(self):
        """A freshly created account is DISCOVERED and holds no authentication."""
        self.assertEqual(self.account.lifecycle_state, AccountLifecycleState.DISCOVERED)
        self.assertEqual(self.account.auth_state, AuthState.UNAUTHENTICATED)
        # DISCOVERED is not a usable account: it must not be routable yet.
        self.assertNotEqual(self.account.lifecycle_state, AccountLifecycleState.ONLINE)

    def test_full_onboarding_walk_reaches_online(self):
        """Every step of the documented onboarding order is permitted, in order."""
        walk = [
            AccountLifecycleState.CONFIGURING,
            AccountLifecycleState.AUTHENTICATING,
            AccountLifecycleState.AUTHENTICATED,
            AccountLifecycleState.VALIDATING,
            AccountLifecycleState.READY,
            AccountLifecycleState.ONLINE,
        ]
        current = self.account.lifecycle_state
        for target in walk:
            self.assertTrue(
                AccountLifecycleStateMachine.can_transition(current, target),
                f"{current.value} -> {target.value} must be legal",
            )
            self.registry.transition_account_lifecycle(self.account.id, target)
            self.assertEqual(self.account.lifecycle_state, target)
            current = target

        self.assertEqual(self.account.lifecycle_state, AccountLifecycleState.ONLINE)

    def test_onboarding_cannot_skip_authentication(self):
        """DISCOVERED must not jump straight to ONLINE, READY or AUTHENTICATED."""
        for illegal in (
            AccountLifecycleState.ONLINE,
            AccountLifecycleState.READY,
            AccountLifecycleState.AUTHENTICATED,
            AccountLifecycleState.VALIDATING,
        ):
            self.assertFalse(
                AccountLifecycleStateMachine.can_transition(
                    AccountLifecycleState.DISCOVERED, illegal
                ),
                f"DISCOVERED -> {illegal.value} must be rejected",
            )
            with self.assertRaises(InvalidLifecycleTransitionError):
                AccountLifecycleStateMachine.validate_transition(
                    AccountLifecycleState.DISCOVERED, illegal
                )

    def test_every_state_permits_self_transition_for_idempotency(self):
        """Re-applying the current state is a no-op, so retries are safe."""
        for state in AccountLifecycleState:
            self.assertTrue(
                AccountLifecycleStateMachine.can_transition(state, state),
                f"{state.value} must tolerate an idempotent re-entry",
            )


class TestAccountOnboardingFailureBranches(unittest.TestCase):
    """Each onboarding step must be able to fail into a distinct, recoverable state."""

    def setUp(self) -> None:
        self.registry = AccountRegistry()
        self.account = Account(
            id="onboard-fail-1",
            provider_id="anthropic",
            account_name="onboard-fail-1",
        )
        self.registry.register_account(self.account)

    def _drive(self, *states: AccountLifecycleState) -> None:
        for state in states:
            self.registry.transition_account_lifecycle(self.account.id, state)

    def test_configuring_can_fail_into_config_error_and_recover(self):
        self._drive(AccountLifecycleState.CONFIGURING, AccountLifecycleState.CONFIG_ERROR)
        self.assertEqual(self.account.lifecycle_state, AccountLifecycleState.CONFIG_ERROR)
        self.assertFalse(self.account.is_available())
        # Recovery path back into configuration must exist.
        self._drive(AccountLifecycleState.CONFIGURING)
        self.assertEqual(self.account.lifecycle_state, AccountLifecycleState.CONFIGURING)

    def test_authentication_can_fail_and_be_retried(self):
        self._drive(
            AccountLifecycleState.CONFIGURING,
            AccountLifecycleState.AUTHENTICATING,
            AccountLifecycleState.AUTH_FAILED,
        )
        self.assertEqual(self.account.lifecycle_state, AccountLifecycleState.AUTH_FAILED)
        self.assertFalse(self.account.is_available())
        # A failed auth must be retryable without re-discovering the account.
        self._drive(AccountLifecycleState.AUTHENTICATING)
        self.assertEqual(self.account.lifecycle_state, AccountLifecycleState.AUTHENTICATING)

    def test_authentication_can_be_cancelled_by_the_user(self):
        self._drive(
            AccountLifecycleState.CONFIGURING,
            AccountLifecycleState.AUTHENTICATING,
            AccountLifecycleState.AUTH_CANCELLED,
        )
        self.assertEqual(self.account.lifecycle_state, AccountLifecycleState.AUTH_CANCELLED)
        self.assertFalse(self.account.is_available())
        # Cancelling is not an error: the user may abandon back to DISCOVERED.
        self.assertTrue(
            AccountLifecycleStateMachine.can_transition(
                AccountLifecycleState.AUTH_CANCELLED, AccountLifecycleState.DISCOVERED
            )
        )

    def test_validation_can_fail_and_provider_can_be_unavailable(self):
        self._drive(
            AccountLifecycleState.CONFIGURING,
            AccountLifecycleState.AUTHENTICATING,
            AccountLifecycleState.AUTHENTICATED,
            AccountLifecycleState.VALIDATING,
            AccountLifecycleState.VALIDATION_FAILED,
        )
        self.assertEqual(
            self.account.lifecycle_state, AccountLifecycleState.VALIDATION_FAILED
        )
        self.assertFalse(self.account.is_available())

        # PROVIDER_UNAVAILABLE is distinct from VALIDATION_FAILED: the
        # credentials are fine, the upstream is not.
        self.assertTrue(
            AccountLifecycleStateMachine.can_transition(
                AccountLifecycleState.VALIDATING,
                AccountLifecycleState.PROVIDER_UNAVAILABLE,
            )
        )

    def test_provider_unavailable_recovers_without_reauthentication(self):
        """An upstream outage must not force the user to authenticate again."""
        self._drive(
            AccountLifecycleState.CONFIGURING,
            AccountLifecycleState.AUTHENTICATING,
            AccountLifecycleState.AUTHENTICATED,
            AccountLifecycleState.VALIDATING,
            AccountLifecycleState.PROVIDER_UNAVAILABLE,
        )
        self.assertTrue(
            AccountLifecycleStateMachine.can_transition(
                AccountLifecycleState.PROVIDER_UNAVAILABLE,
                AccountLifecycleState.ONLINE,
            ),
            "recovery from an upstream outage must not require re-auth",
        )


class TestOnboardedAccountStatusAlignment(unittest.TestCase):
    """Constructing an account with a status must derive consistent sub-states."""

    def test_online_status_implies_authenticated_and_healthy(self):
        account = Account(
            id="aligned-online",
            provider_id="gemini",
            account_name="aligned-online",
            status=AccountStatus.ONLINE,
        )
        self.assertEqual(account.lifecycle_state, AccountLifecycleState.ONLINE)
        self.assertEqual(account.auth_state, AuthState.AUTHENTICATED)
        self.assertEqual(account.health_state, HealthState.HEALTHY)
        self.assertTrue(account.is_available())

    def test_auth_error_status_implies_auth_failed_and_unavailable(self):
        account = Account(
            id="aligned-autherr",
            provider_id="gemini",
            account_name="aligned-autherr",
            status=AccountStatus.AUTH_ERROR,
        )
        self.assertEqual(account.lifecycle_state, AccountLifecycleState.AUTH_FAILED)
        self.assertEqual(account.auth_state, AuthState.AUTH_FAILED)
        self.assertEqual(account.health_state, HealthState.UNHEALTHY)
        self.assertFalse(account.is_available())

    def test_disabled_status_clears_enabled_flag(self):
        account = Account(
            id="aligned-disabled",
            provider_id="gemini",
            account_name="aligned-disabled",
            status=AccountStatus.DISABLED,
        )
        self.assertEqual(account.lifecycle_state, AccountLifecycleState.DISABLED)
        self.assertFalse(account.enabled)
        self.assertFalse(account.is_available())


if __name__ == "__main__":
    unittest.main()
