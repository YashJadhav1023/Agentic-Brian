"""Part 14 — Router/account integration tests (Phase 22, Part 12).

The router is the component that turns account *state* into account *use*. The
property under test is that admission is strictly gated on lifecycle, auth, and
health, and that every rejection is explained with a machine-readable reason —
an account silently receiving no work is the failure mode this prevents.

Admission is asserted through :meth:`SmartRouter.check_account_admissible`,
which is the single gate every routing decision passes through.
"""

import time
import unittest

from brain.router.smart_router import AccountRejectionReason, SmartRouter
from providers.registry.account_registry import (
    Account,
    AccountStatus,
    AuthenticationType,
)
from providers.registry.lifecycle import (
    AccountLifecycleState,
    AuthState,
    HealthState,
)


def _ready_account(**overrides) -> Account:
    """Build an account that is admissible unless a test breaks one property."""
    fields = {
        "id": "router-acct",
        "provider_id": "openai",
        "account_name": "router-acct",
        "authentication_type": AuthenticationType.API_KEY,
        "status": AccountStatus.ONLINE,
        "capabilities": ["build-and-test"],
        "concurrency_limit": 2,
    }
    fields.update(overrides)
    account = Account(**fields)
    # Status alignment already sets these, but make the premise explicit so a
    # future change to alignment cannot silently weaken these tests.
    account.lifecycle_state = AccountLifecycleState.ONLINE
    account.auth_state = AuthState.AUTHENTICATED
    account.health_state = HealthState.HEALTHY
    return account


class TestAccountAdmission(unittest.TestCase):
    """Only a fully READY/ONLINE, authenticated, healthy account may be used."""

    def setUp(self) -> None:
        self.router = SmartRouter()

    def test_a_ready_account_is_admitted(self):
        ok, reason, detail = self.router.check_account_admissible(_ready_account())
        self.assertTrue(ok, f"expected admission, got {reason}: {detail}")
        self.assertIsNone(reason)

    def test_an_online_and_a_ready_account_are_both_admissible(self):
        for state in (AccountLifecycleState.ONLINE, AccountLifecycleState.READY):
            account = _ready_account()
            account.lifecycle_state = state
            ok, reason, _ = self.router.check_account_admissible(account)
            self.assertTrue(ok, f"{state.value} must be admissible, got {reason}")

    def test_a_missing_account_is_rejected_as_not_registered(self):
        ok, reason, _ = self.router.check_account_admissible(None)
        self.assertFalse(ok)
        self.assertEqual(reason, AccountRejectionReason.NOT_REGISTERED)

    def test_a_disabled_account_is_rejected(self):
        account = _ready_account()
        account.enabled = False
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertFalse(ok)
        self.assertEqual(reason, AccountRejectionReason.NOT_ENABLED)

    def test_an_unauthenticated_account_is_rejected(self):
        for auth_state in (
            AuthState.UNAUTHENTICATED,
            AuthState.AUTH_FAILED,
            AuthState.AUTH_CANCELLED,
            AuthState.EXPIRED,
        ):
            account = _ready_account()
            account.auth_state = auth_state
            ok, reason, detail = self.router.check_account_admissible(account)
            self.assertFalse(ok, f"{auth_state.value} must not be admitted")
            self.assertEqual(reason, AccountRejectionReason.NOT_AUTHENTICATED)
            self.assertIn(auth_state.value, detail)

    def test_an_unhealthy_or_unknown_account_is_rejected(self):
        for health in (HealthState.UNHEALTHY, HealthState.UNKNOWN):
            account = _ready_account()
            account.health_state = health
            ok, reason, _ = self.router.check_account_admissible(account)
            self.assertFalse(ok, f"{health.value} must not be admitted")
            self.assertEqual(reason, AccountRejectionReason.UNHEALTHY)

    def test_a_degraded_account_is_still_admitted(self):
        """Degraded is usable: excluding it would collapse capacity on a blip."""
        account = _ready_account()
        account.health_state = HealthState.DEGRADED
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertTrue(ok, f"DEGRADED must remain usable, got {reason}")

    def test_mid_onboarding_states_are_rejected_with_the_lifecycle_reason(self):
        for state in (
            AccountLifecycleState.DISCOVERED,
            AccountLifecycleState.CONFIGURING,
            AccountLifecycleState.AUTHENTICATING,
            AccountLifecycleState.AUTHENTICATED,
            AccountLifecycleState.VALIDATING,
            AccountLifecycleState.OFFLINE,
        ):
            account = _ready_account()
            account.lifecycle_state = state
            ok, reason, detail = self.router.check_account_admissible(account)
            self.assertFalse(ok, f"{state.value} must not receive work")
            self.assertEqual(reason, AccountRejectionReason.WRONG_LIFECYCLE_STATE)
            self.assertIn(state.value, detail)

    def test_authenticated_alone_is_not_enough_to_receive_work(self):
        """AUTHENTICATED precedes validation; work must wait for READY."""
        account = _ready_account()
        account.lifecycle_state = AccountLifecycleState.AUTHENTICATED
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertFalse(ok)
        self.assertEqual(reason, AccountRejectionReason.WRONG_LIFECYCLE_STATE)


class TestOperationalRejections(unittest.TestCase):
    """Operational blocks must be reported as the most actionable cause."""

    def setUp(self) -> None:
        self.router = SmartRouter()

    def test_quota_exhaustion_is_reported_ahead_of_generic_health(self):
        account = _ready_account(status=AccountStatus.QUOTA_EXHAUSTED)
        account.lifecycle_state = AccountLifecycleState.ONLINE
        account.auth_state = AuthState.AUTHENTICATED
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertFalse(ok)
        self.assertEqual(reason, AccountRejectionReason.QUOTA_EXHAUSTED)

    def test_rate_limiting_is_reported_distinctly_from_cooldown(self):
        account = _ready_account(status=AccountStatus.RATE_LIMITED)
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertFalse(ok)
        self.assertEqual(reason, AccountRejectionReason.RATE_LIMITED)

    def test_an_active_cooldown_window_blocks_admission(self):
        account = _ready_account()
        account.cooldown_until = time.time() + 60
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertFalse(ok)
        self.assertEqual(reason, AccountRejectionReason.COOLDOWN)

    def test_an_expired_cooldown_window_does_not_block_admission(self):
        account = _ready_account()
        account.cooldown_until = time.time() - 1
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertTrue(ok, f"an elapsed cooldown must not block, got {reason}")

    def test_a_saturated_account_is_rejected_with_its_slot_counts(self):
        account = _ready_account(concurrency_limit=2)
        account.current_concurrency = 2
        ok, reason, detail = self.router.check_account_admissible(account)
        self.assertFalse(ok)
        self.assertEqual(reason, AccountRejectionReason.CONCURRENCY_EXCEEDED)
        self.assertIn("2/2", detail)

    def test_an_account_with_free_capacity_is_admitted(self):
        account = _ready_account(concurrency_limit=2)
        account.current_concurrency = 1
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertTrue(ok, f"spare capacity must be usable, got {reason}")

    def test_an_account_declaring_no_capabilities_is_not_routable(self):
        account = _ready_account(capabilities=[])
        ok, reason, detail = self.router.check_account_admissible(account)
        self.assertFalse(ok)
        self.assertEqual(reason, AccountRejectionReason.CAPABILITY_MISMATCH)
        self.assertIn("no usable capabilities", detail)

    def test_unrecognised_capability_strings_are_ignored_not_fatal(self):
        """A future capability name must not make an account unroutable."""
        account = _ready_account(capabilities=["build-and-test", "not-a-real-capability"])
        ok, reason, _ = self.router.check_account_admissible(account)
        self.assertTrue(ok, f"unknown capabilities must be tolerated, got {reason}")


class TestRejectionReasonsAreEnumerated(unittest.TestCase):
    """The dashboard renders these, so the vocabulary must be stable."""

    def test_all_documented_rejection_reasons_exist(self):
        for name in (
            "NOT_REGISTERED",
            "NOT_ENABLED",
            "NOT_AUTHENTICATED",
            "UNHEALTHY",
            "WRONG_LIFECYCLE_STATE",
            "CAPABILITY_MISMATCH",
            "RATE_LIMITED",
            "COOLDOWN",
            "QUOTA_EXHAUSTED",
            "CONCURRENCY_EXCEEDED",
        ):
            self.assertTrue(
                hasattr(AccountRejectionReason, name),
                f"AccountRejectionReason.{name} is required by the dashboard",
            )

    def test_rejection_reasons_are_unique_strings(self):
        values = [
            getattr(AccountRejectionReason, n)
            for n in dir(AccountRejectionReason)
            if n.isupper()
        ]
        self.assertEqual(len(values), len(set(values)), "reason codes must be unique")

    def test_router_exposes_recorded_rejection_reasons(self):
        router = SmartRouter()
        reasons = router.get_rejection_reasons()
        self.assertIsInstance(reasons, dict)


if __name__ == "__main__":
    unittest.main()
