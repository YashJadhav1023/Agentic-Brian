"""Part 14 — Active agent count tests (Phase 22, Part 8).

Part 8 exists because the dashboard reported a misleading "4 online" figure. The
audit traced it to two conflations: a PROVIDER count rendered where operators
read an ACCOUNT count, and a "workers" figure derived from the adapter list,
which omits direct-API accounts entirely.

The fix computes eight metrics, each from exactly one orthogonal field. These
tests pin that separation, because the whole defect was one number standing in
for several different questions. Each metric is therefore asserted to move
*independently* of the others.

``_compute_account_metrics`` is exercised directly rather than over HTTP: it
reads nothing from ``self``, and calling it with synthetic accounts makes the
counts deterministic instead of dependent on whatever is registered locally.
"""

import unittest
from types import SimpleNamespace

from ui.dashboard.dashboard import MissionControlHandler

_compute = MissionControlHandler._compute_account_metrics

#: Every key the UI renders. "signed_in" is the authenticated count under a name
#: that the response-wide secret redactor will not blank out.
EXPECTED_KEYS = frozenset(
    {
        "registered",
        "signed_in",
        "healthy",
        "online",
        "busy",
        "idle",
        "offline",
        "disabled",
    }
)


def _account(
    lifecycle_state: str = "ONLINE",
    auth_state: str = "AUTHENTICATED",
    health_state: str = "HEALTHY",
    task_state: str = "IDLE",
    enabled: bool = True,
) -> SimpleNamespace:
    """A stand-in account exposing only the orthogonal fields metrics may read."""
    return SimpleNamespace(
        lifecycle_state=lifecycle_state,
        auth_state=auth_state,
        health_state=health_state,
        task_state=task_state,
        enabled=enabled,
    )


class TestMetricShape(unittest.TestCase):
    def test_all_eight_metrics_are_present(self):
        metrics = _compute(None, [])
        self.assertEqual(set(metrics), EXPECTED_KEYS)

    def test_every_metric_is_an_integer(self):
        metrics = _compute(None, [_account()])
        for key, value in metrics.items():
            self.assertIsInstance(value, int, f"{key} must be an int, got {type(value)}")

    def test_no_accounts_yields_all_zeros(self):
        metrics = _compute(None, [])
        self.assertEqual(set(metrics.values()), {0})

    def test_no_metric_key_contains_the_substring_auth(self):
        """A key matching /auth/ is blanked by the redactor before reaching the UI."""
        for key in _compute(None, [_account()]):
            self.assertNotIn(
                "auth", key.lower(), f"key '{key}' would be redacted in the response"
            )


class TestMetricsAreIndependent(unittest.TestCase):
    """Each metric must answer exactly one question about one field."""

    def test_registered_counts_every_account_regardless_of_state(self):
        accounts = [
            _account(lifecycle_state="DISCOVERED", auth_state="UNAUTHENTICATED"),
            _account(lifecycle_state="OFFLINE", health_state="UNHEALTHY"),
            _account(lifecycle_state="DISABLED", enabled=False),
            _account(),
        ]
        self.assertEqual(_compute(None, accounts)["registered"], 4)

    def test_authenticated_is_independent_of_being_online(self):
        """An account can hold valid credentials and still not be online."""
        accounts = [
            _account(lifecycle_state="AUTHENTICATED", auth_state="AUTHENTICATED"),
            _account(lifecycle_state="READY", auth_state="AUTHENTICATED"),
            _account(lifecycle_state="ONLINE", auth_state="AUTHENTICATED"),
        ]
        metrics = _compute(None, accounts)
        self.assertEqual(metrics["signed_in"], 3)
        self.assertEqual(metrics["online"], 1, "only ONLINE lifecycle counts as online")

    def test_healthy_is_independent_of_lifecycle(self):
        accounts = [
            _account(lifecycle_state="READY", health_state="HEALTHY"),
            _account(lifecycle_state="ONLINE", health_state="DEGRADED"),
            _account(lifecycle_state="ONLINE", health_state="HEALTHY"),
        ]
        metrics = _compute(None, accounts)
        self.assertEqual(metrics["healthy"], 2)
        self.assertEqual(metrics["online"], 2)

    def test_degraded_is_online_but_not_healthy(self):
        metrics = _compute(
            None, [_account(lifecycle_state="ONLINE", health_state="DEGRADED")]
        )
        self.assertEqual(metrics["online"], 1)
        self.assertEqual(metrics["healthy"], 0)

    def test_busy_counts_running_and_assigned_work(self):
        accounts = [
            _account(task_state="RUNNING"),
            _account(task_state="ASSIGNED"),
            _account(task_state="IDLE"),
            _account(task_state="COMPLETED"),
        ]
        self.assertEqual(_compute(None, accounts)["busy"], 2)

    def test_busy_and_idle_partition_the_online_accounts(self):
        accounts = [
            _account(lifecycle_state="ONLINE", task_state="RUNNING"),
            _account(lifecycle_state="ONLINE", task_state="IDLE"),
            _account(lifecycle_state="ONLINE", task_state="IDLE"),
        ]
        metrics = _compute(None, accounts)
        self.assertEqual(metrics["online"], 3)
        self.assertEqual(metrics["busy"], 1)
        self.assertEqual(metrics["idle"], 2)
        self.assertEqual(metrics["busy"] + metrics["idle"], metrics["online"])

    def test_idle_requires_being_online_not_merely_unoccupied(self):
        """An OFFLINE account with no task is not idle capacity."""
        metrics = _compute(
            None, [_account(lifecycle_state="OFFLINE", task_state="IDLE")]
        )
        self.assertEqual(metrics["idle"], 0)
        self.assertEqual(metrics["offline"], 1)

    def test_a_task_failure_does_not_reduce_health_or_online_counts(self):
        """The core Part 2 guarantee, observed through the metrics surface."""
        metrics = _compute(
            None,
            [
                _account(
                    lifecycle_state="ONLINE",
                    health_state="HEALTHY",
                    task_state="FAILED",
                )
            ],
        )
        self.assertEqual(metrics["online"], 1)
        self.assertEqual(metrics["healthy"], 1)
        self.assertEqual(metrics["busy"], 0)
        self.assertEqual(metrics["offline"], 0)

    def test_disabled_counts_both_lifecycle_and_the_enabled_flag(self):
        accounts = [
            _account(lifecycle_state="DISABLED", enabled=False),
            _account(lifecycle_state="ONLINE", enabled=False),
            _account(lifecycle_state="DISABLED", enabled=True),
            _account(),
        ]
        self.assertEqual(_compute(None, accounts)["disabled"], 3)

    def test_offline_and_online_are_mutually_exclusive(self):
        accounts = [
            _account(lifecycle_state="ONLINE"),
            _account(lifecycle_state="OFFLINE"),
        ]
        metrics = _compute(None, accounts)
        self.assertEqual(metrics["online"], 1)
        self.assertEqual(metrics["offline"], 1)


class TestCountsAreNotProviderCounts(unittest.TestCase):
    """The original defect: a provider total displayed as an account total."""

    def test_many_accounts_under_few_providers_are_counted_individually(self):
        # 4 providers owning 8 accounts must never report as 4.
        accounts = [_account() for _ in range(8)]
        metrics = _compute(None, accounts)
        self.assertEqual(metrics["registered"], 8)
        self.assertEqual(metrics["online"], 8)
        self.assertNotEqual(
            metrics["online"], 4, "online must count accounts, never providers"
        )

    def test_direct_api_accounts_are_not_omitted(self):
        """The adapter list omits API accounts; the metrics must not."""
        accounts = [
            _account(),  # agent-backed
            _account(),  # agent-backed
            _account(),  # direct API, no adapter
        ]
        self.assertEqual(_compute(None, accounts)["registered"], 3)

    def test_metrics_tolerate_enum_valued_fields(self):
        """Accounts carry enums, not strings; both must count identically."""
        from providers.registry.lifecycle import (
            AccountLifecycleState,
            AuthState,
            HealthState,
        )

        enum_account = SimpleNamespace(
            lifecycle_state=AccountLifecycleState.ONLINE,
            auth_state=AuthState.AUTHENTICATED,
            health_state=HealthState.HEALTHY,
            task_state="IDLE",
            enabled=True,
        )
        metrics = _compute(None, [enum_account])
        self.assertEqual(metrics["online"], 1)
        self.assertEqual(metrics["signed_in"], 1)
        self.assertEqual(metrics["healthy"], 1)


if __name__ == "__main__":
    unittest.main()
