"""Phase 10: account lifecycle management.

Covers requirements 1-4 of the Phase 10 test matrix (add, remove, enable,
disable an account) and asserts that the `Account` record carries every field
the Phase 10 specification requires.
"""
from __future__ import annotations

import unittest

from providers.registry.account_registry import (
    Account,
    AccountPool,
    AccountRegistry,
    AccountStatus,
    AuthenticationType,
)


def _account(
    account_id: str = "openai-personal",
    provider_id: str = "openai",
    **kwargs,
) -> Account:
    defaults = dict(
        id=account_id,
        provider_id=provider_id,
        account_name="personal",
        authentication_type=AuthenticationType.API_KEY,
        credential_reference=f"secret://mission-control/{provider_id}/personal",
        status=AccountStatus.ONLINE,
        enabled=True,
    )
    defaults.update(kwargs)
    return Account(**defaults)


class TestAccountSchema(unittest.TestCase):
    """Every field the Phase 10 account specification enumerates must exist."""

    REQUIRED_FIELDS = (
        "id",
        "provider_id",
        "display_name",
        "authentication_type",
        "credential_reference",
        "status",
        "enabled",
        "priority",
        "cooldown_until",
        "models",
        "metadata",
        "created_at",
        "updated_at",
        "usage",
    )

    def test_account_exposes_every_required_field(self):
        acc = _account()
        for field_name in self.REQUIRED_FIELDS:
            with self.subTest(field=field_name):
                self.assertTrue(
                    hasattr(acc, field_name), f"Account is missing {field_name}"
                )

    def test_timestamps_are_populated_on_creation(self):
        acc = _account()
        self.assertTrue(acc.created_at)
        self.assertTrue(acc.updated_at)

    def test_health_state_is_represented_by_status_enum(self):
        acc = _account(status=AccountStatus.DEGRADED)
        self.assertIs(acc.status, AccountStatus.DEGRADED)

    def test_all_seven_phase10_states_exist(self):
        for name in (
            "ONLINE",
            "DEGRADED",
            "RATE_LIMITED",
            "AUTH_ERROR",
            "OFFLINE",
            "DISABLED",
            "COOLDOWN",
        ):
            with self.subTest(state=name):
                self.assertTrue(hasattr(AccountStatus, name))


class TestAccountLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AccountRegistry()

    def test_add_account(self):
        self.registry.register_account(_account())
        self.assertIsNotNone(self.registry.get_account("openai-personal"))
        self.assertEqual(len(self.registry.list_accounts("openai")), 1)

    def test_account_ids_are_unique_within_the_registry(self):
        self.registry.register_account(_account())
        self.registry.register_account(_account())  # same id again
        self.assertEqual(len(self.registry.list_accounts("openai")), 1)

    def test_disable_account_marks_it_unavailable(self):
        self.registry.register_account(_account())
        self.assertTrue(self.registry.disable_account("openai-personal"))
        acc = self.registry.get_account("openai-personal")
        self.assertFalse(acc.enabled)
        self.assertFalse(acc.is_available())

    def test_enable_account_restores_availability(self):
        self.registry.register_account(_account())
        self.registry.disable_account("openai-personal")
        self.assertTrue(self.registry.enable_account("openai-personal"))
        acc = self.registry.get_account("openai-personal")
        self.assertTrue(acc.enabled)
        self.assertTrue(acc.is_available())

    def test_remove_account(self):
        self.registry.register_account(_account())
        self.assertTrue(self.registry.remove_account("openai-personal"))
        self.assertIsNone(self.registry.get_account("openai-personal"))

    def test_enable_disable_remove_on_unknown_account_returns_false(self):
        self.assertFalse(self.registry.enable_account("ghost"))
        self.assertFalse(self.registry.disable_account("ghost"))
        self.assertFalse(self.registry.remove_account("ghost"))

    def test_update_account_refreshes_updated_at(self):
        self.registry.register_account(_account())
        before = self.registry.get_account("openai-personal").updated_at
        self.registry.update_account("openai-personal", priority=99)
        acc = self.registry.get_account("openai-personal")
        self.assertEqual(acc.priority, 99)
        self.assertGreaterEqual(acc.updated_at, before)

    def test_accounts_are_isolated_per_provider(self):
        self.registry.register_account(_account("openai-personal", "openai"))
        self.registry.register_account(_account("anthropic-work", "anthropic"))
        self.assertEqual(len(self.registry.list_accounts("openai")), 1)
        self.assertEqual(len(self.registry.list_accounts("anthropic")), 1)
        self.assertEqual(len(self.registry.list_accounts()), 2)


class TestAccountPoolSelectionAndRotation(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = AccountPool("openai")
        self.pool.add_account(_account("openai-a", priority=50))
        self.pool.add_account(_account("openai-b", priority=10))

    def test_highest_priority_account_is_selected_deterministically(self):
        for _ in range(5):
            self.assertEqual(self.pool.get_available_account().id, "openai-a")

    def test_explicit_preference_is_honoured_when_eligible(self):
        chosen = self.pool.get_available_account(preferred_account="openai-b")
        self.assertEqual(chosen.id, "openai-b")

    def test_excluded_account_is_skipped(self):
        chosen = self.pool.get_available_account(exclude_accounts={"openai-a"})
        self.assertEqual(chosen.id, "openai-b")

    def test_rotation_is_an_explicit_operation_not_a_side_effect(self):
        """`rotate()` exists so priority changes are deliberate and auditable."""
        order_before = [a.id for a in self.pool.list_accounts()]
        rotated = self.pool.rotate()
        self.assertIsInstance(rotated, list)
        # Selection remains deterministic after an explicit rotate
        first = self.pool.get_available_account().id
        self.assertEqual(first, self.pool.get_available_account().id)
        self.assertEqual(len(order_before), len(self.pool.list_accounts()))

    def test_concurrency_is_bounded_by_the_account_limit(self):
        pool = AccountPool("openai")
        pool.add_account(_account("openai-solo", concurrency_limit=1))
        self.assertTrue(pool.acquire("openai-solo"))
        self.assertFalse(pool.acquire("openai-solo"), "limit was not enforced")
        pool.release("openai-solo")
        self.assertTrue(pool.acquire("openai-solo"))

    def test_no_available_account_returns_none(self):
        pool = AccountPool("openai")
        acc = _account("openai-off", enabled=False)
        pool.add_account(acc)
        self.assertIsNone(pool.get_available_account())


if __name__ == "__main__":
    unittest.main()
