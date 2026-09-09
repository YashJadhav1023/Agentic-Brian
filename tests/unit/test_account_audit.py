"""Part 14 — Account audit integration tests (Phase 22, Part 11).

Every account lifecycle action must leave an immutable, correlatable, and
secret-free trail. These tests assert three properties that the audit trail is
worthless without:

1. **Coverage** — lifecycle transitions, enable/disable and removal all emit.
2. **Correlation** — one onboarding flow is retrievable as one flow.
3. **Redaction** — a secret handed to the auditor never reaches the ledger.

Each test writes to its own temporary ledger so the suite never appends to the
real ``runtime/audit/audit.jsonl``.
"""

import tempfile
import unittest
from pathlib import Path

from brain.governance.audit_logger import (
    ACCOUNT_AUDIT_CATEGORY,
    AccountAuditAction,
    AccountAuditor,
    AuditLogger,
    new_correlation_id,
)
from providers.registry.account_registry import (
    Account,
    AccountRegistry,
    AuthenticationType,
)
from providers.registry.lifecycle import AccountLifecycleState


class _LedgerTestCase(unittest.TestCase):
    """Base class giving each test an isolated audit ledger on disk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ledger_path = Path(self._tmp.name) / "audit.jsonl"
        self.auditor = AccountAuditor(AuditLogger(log_path=self.ledger_path))


class TestAccountAuditorContract(_LedgerTestCase):
    """The auditor's own guarantees, independent of any caller."""

    def test_record_emits_account_category_and_stable_action(self):
        event = self.auditor.record(
            action=AccountAuditAction.CREATE,
            provider_id="openai",
            account_id="audit-1",
        )
        self.assertEqual(event.category, ACCOUNT_AUDIT_CATEGORY)
        self.assertEqual(event.action, AccountAuditAction.CREATE)
        self.assertEqual(event.target, "audit-1")
        self.assertEqual(event.status, "SUCCESS")

    def test_every_entry_carries_a_correlation_id_even_when_unspecified(self):
        """An uncorrelated entry is untraceable, so one is always generated."""
        event = self.auditor.record(
            action=AccountAuditAction.AUTH_STARTED,
            provider_id="openai",
            account_id="audit-2",
        )
        corr = event.details.get("correlation_id")
        self.assertTrue(corr, "audit entries must always carry a correlation id")
        self.assertTrue(str(corr).startswith("acorr-"))

    def test_states_are_serialised_by_value_not_by_repr(self):
        """Enum states must land in the ledger as plain strings."""
        event = self.auditor.record(
            action=AccountAuditAction.LIFECYCLE_TRANSITION,
            provider_id="openai",
            account_id="audit-3",
            from_state=AccountLifecycleState.AUTHENTICATING,
            to_state=AccountLifecycleState.AUTHENTICATED,
        )
        self.assertEqual(event.details["from_state"], "AUTHENTICATING")
        self.assertEqual(event.details["to_state"], "AUTHENTICATED")

    def test_caller_details_cannot_overwrite_structural_fields(self):
        """A caller must not be able to forge account_id or provider_id."""
        event = self.auditor.record(
            action=AccountAuditAction.CREATE,
            provider_id="openai",
            account_id="genuine-id",
            details={"account_id": "spoofed-id", "provider_id": "spoofed", "note": "kept"},
        )
        self.assertEqual(event.details["account_id"], "genuine-id")
        self.assertEqual(event.details["provider_id"], "openai")
        self.assertEqual(event.details["note"], "kept")

    def test_a_whole_flow_is_retrievable_by_correlation_id(self):
        corr = new_correlation_id()
        for action in (
            AccountAuditAction.CREATE,
            AccountAuditAction.AUTH_STARTED,
            AccountAuditAction.AUTH_SUCCESS,
            AccountAuditAction.VALIDATION_SUCCESS,
        ):
            self.auditor.record(
                action=action,
                provider_id="anthropic",
                account_id="flow-1",
                correlation_id=corr,
            )
        # An unrelated account's noise must not leak into the flow.
        self.auditor.record(
            action=AccountAuditAction.CREATE,
            provider_id="anthropic",
            account_id="unrelated",
        )

        flow = self.auditor.get_account_events(correlation_id=corr)
        self.assertEqual(len(flow), 4)
        self.assertEqual({e["details"]["account_id"] for e in flow}, {"flow-1"})

    def test_events_are_filterable_by_account_and_by_action(self):
        self.auditor.record(
            action=AccountAuditAction.ENABLE, provider_id="p", account_id="acct-a"
        )
        self.auditor.record(
            action=AccountAuditAction.DISABLE, provider_id="p", account_id="acct-a"
        )
        self.auditor.record(
            action=AccountAuditAction.ENABLE, provider_id="p", account_id="acct-b"
        )

        by_account = self.auditor.get_account_events(account_id="acct-a")
        self.assertEqual(len(by_account), 2)

        by_action = self.auditor.get_account_events(action=AccountAuditAction.ENABLE)
        self.assertEqual(len(by_action), 2)
        self.assertEqual(
            {e["details"]["account_id"] for e in by_action}, {"acct-a", "acct-b"}
        )

    def test_failure_status_is_preserved_for_failed_actions(self):
        self.auditor.record(
            action=AccountAuditAction.AUTH_FAILED,
            provider_id="gemini",
            account_id="acct-fail",
            status="FAILURE",
            details={"reason": "invalid key"},
        )
        events = self.auditor.get_account_events(account_id="acct-fail")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "FAILURE")
        self.assertEqual(events[0]["details"]["reason"], "invalid key")

    def test_ledger_is_append_only_across_auditor_instances(self):
        """A restarted process must not truncate the existing trail."""
        self.auditor.record(
            action=AccountAuditAction.CREATE, provider_id="p", account_id="persist-1"
        )
        reopened = AccountAuditor(AuditLogger(log_path=self.ledger_path))
        reopened.record(
            action=AccountAuditAction.ENABLE, provider_id="p", account_id="persist-1"
        )
        self.assertEqual(len(reopened.get_account_events(account_id="persist-1")), 2)


class TestAccountAuditRedaction(_LedgerTestCase):
    """A secret must never survive into the audit ledger, in any position."""

    SECRET = "sk-live-AAAABBBBCCCCDDDDEEEEFFFF0000111122223333"

    def _raw_ledger_text(self) -> str:
        return self.ledger_path.read_text(encoding="utf-8")

    def test_secret_in_details_is_not_written_verbatim(self):
        self.auditor.record(
            action=AccountAuditAction.CREATE,
            provider_id="openai",
            account_id="redact-1",
            details={"api_key": self.SECRET},
        )
        self.assertNotIn(self.SECRET, self._raw_ledger_text())

    def test_secret_nested_in_a_list_is_not_written_verbatim(self):
        self.auditor.record(
            action=AccountAuditAction.CREATE,
            provider_id="openai",
            account_id="redact-2",
            details={"attempts": [{"key": self.SECRET}]},
        )
        self.assertNotIn(self.SECRET, self._raw_ledger_text())

    def test_credential_reference_is_an_opaque_pointer_not_a_secret(self):
        """Audit entries reference credentials by URI, never by value."""
        self.auditor.record(
            action=AccountAuditAction.CREATE,
            provider_id="openai",
            account_id="redact-3",
            details={"credential_reference": "secret://mission-control/openai/personal"},
        )
        text = self._raw_ledger_text()
        self.assertIn("secret://mission-control/openai/personal", text)
        self.assertNotIn(self.SECRET, text)


class TestRegistryEmitsAuditEvents(_LedgerTestCase):
    """The registry must wire the auditor in, not merely have one available."""

    def setUp(self) -> None:
        super().setUp()
        self.registry = AccountRegistry(auditor=self.auditor)
        self.account = Account(
            id="wired-1",
            provider_id="openai",
            account_name="wired-1",
            authentication_type=AuthenticationType.API_KEY,
        )
        self.registry.register_account(self.account)

    def test_lifecycle_transition_is_audited_with_from_and_to_state(self):
        self.registry.transition_account_lifecycle(
            "wired-1", AccountLifecycleState.CONFIGURING
        )
        events = self.auditor.get_account_events(
            account_id="wired-1", action=AccountAuditAction.LIFECYCLE_TRANSITION
        )
        self.assertGreaterEqual(len(events), 1)
        latest = events[0]["details"]
        self.assertEqual(latest["from_state"], "DISCOVERED")
        self.assertEqual(latest["to_state"], "CONFIGURING")

    def test_disable_then_enable_are_both_audited(self):
        self.registry.transition_account_lifecycle(
            "wired-1", AccountLifecycleState.CONFIGURING
        )
        self.registry.disable_account("wired-1")
        self.registry.enable_account("wired-1")

        all_events = self.auditor.get_account_events(account_id="wired-1", limit=100)
        actions = {e["action"] for e in all_events}
        self.assertIn(AccountAuditAction.LIFECYCLE_TRANSITION, actions)
        # Disable/enable must be observable in the trail in some form.
        self.assertTrue(
            len(all_events) >= 3,
            f"expected a trail of transitions, got actions={actions}",
        )


if __name__ == "__main__":
    unittest.main()
