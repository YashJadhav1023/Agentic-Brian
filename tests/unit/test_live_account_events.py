"""Part 14 — Live account event stream tests (Phase 22, Part 10).

An onboarding flow is only observable if its events are named consistently,
correlated to one flow, ordered, and free of secrets. These tests exercise
:class:`WizardEventStream` directly — the component the SSE endpoint reads from —
so no HTTP server or long-lived stream connection is required.

Each test uses its own stream instance, so nothing is published to the shared
process-wide stream that the running dashboard would surface.
"""

import queue
import unittest

from ui.dashboard.dashboard import WizardEventStream, _new_correlation_id


class TestEventVocabulary(unittest.TestCase):
    """Event names are a contract with the UI and must not drift."""

    def test_the_documented_lifecycle_event_names_are_published(self):
        required = {
            "account.create_started",
            "account.configuring",
            "account.authentication_started",
            "account.authentication_success",
            "account.authentication_failed",
            "account.authentication_cancelled",
            "account.validation_started",
            "account.validation_success",
            "account.validation_failed",
            "account.registered",
            "account.health_check",
            "account.online",
            "account.removed",
        }
        self.assertTrue(
            required.issubset(set(WizardEventStream.NAMES)),
            f"missing: {required - set(WizardEventStream.NAMES)}",
        )

    def test_event_names_are_unique(self):
        names = WizardEventStream.NAMES
        self.assertEqual(len(names), len(set(names)))

    def test_every_name_is_namespaced_under_account(self):
        for name in WizardEventStream.NAMES:
            self.assertTrue(
                name.startswith("account."), f"{name} must be namespaced 'account.'"
            )

    def test_both_failure_and_cancellation_are_representable(self):
        """A user abandoning auth is not the same as auth failing."""
        self.assertIn("account.authentication_failed", WizardEventStream.NAMES)
        self.assertIn("account.authentication_cancelled", WizardEventStream.NAMES)


class TestCorrelationIds(unittest.TestCase):
    def test_correlation_ids_are_prefixed_and_unique(self):
        ids = {_new_correlation_id() for _ in range(50)}
        self.assertEqual(len(ids), 50, "correlation ids must not collide")
        for cid in ids:
            self.assertTrue(cid.startswith("wiz-"))


class TestEmittedEventShape(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = WizardEventStream()
        self.corr = _new_correlation_id()

    def test_an_emitted_event_carries_its_identity_and_correlation(self):
        event = self.stream.emit(
            "account.create_started",
            correlation_id=self.corr,
            provider_id="openai",
            account_id="acct-1",
            message="starting",
            lifecycle_state="DISCOVERED",
        )
        self.assertEqual(event["event_type"], "account.create_started")
        self.assertEqual(event["correlation_id"], self.corr)
        self.assertEqual(event["provider_id"], "openai")
        self.assertEqual(event["account_id"], "acct-1")
        self.assertEqual(event["lifecycle_state"], "DISCOVERED")
        self.assertTrue(event["timestamp"])
        self.assertTrue(event["event_id"].startswith("wevt-"))

    def test_event_ids_are_unique_within_a_stream(self):
        ids = {
            self.stream.emit("account.configuring", self.corr)["event_id"]
            for _ in range(20)
        }
        self.assertEqual(len(ids), 20)

    def test_payload_and_metadata_both_carry_the_correlation_id(self):
        """The SSE consumer and the audit mirror read different sub-objects."""
        event = self.stream.emit(
            "account.registered", self.corr, provider_id="p", account_id="a"
        )
        self.assertEqual(event["payload"]["correlation_id"], self.corr)
        self.assertEqual(event["metadata"]["correlation_id"], self.corr)

    def test_extra_fields_are_preserved_in_the_payload(self):
        event = self.stream.emit(
            "account.validation_success",
            self.corr,
            extra={"models_discovered": 3, "latency_ms": 42},
        )
        self.assertEqual(event["payload"]["models_discovered"], 3)
        self.assertEqual(event["payload"]["latency_ms"], 42)


class TestEventHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = WizardEventStream(maxlen=5)
        self.corr = _new_correlation_id()

    def test_recent_returns_events_in_emission_order(self):
        for name in (
            "account.create_started",
            "account.configuring",
            "account.authentication_started",
        ):
            self.stream.emit(name, self.corr)

        recent = self.stream.recent()
        self.assertEqual(
            [e["event_type"] for e in recent],
            [
                "account.create_started",
                "account.configuring",
                "account.authentication_started",
            ],
        )

    def test_history_is_bounded_and_drops_the_oldest(self):
        for i in range(8):
            self.stream.emit("account.health_check", self.corr, message=f"n{i}")

        recent = self.stream.recent(limit=100)
        self.assertEqual(len(recent), 5, "history must be capped at maxlen")
        self.assertEqual(recent[-1]["message"], "n7")
        self.assertEqual(recent[0]["message"], "n3")

    def test_recent_respects_an_explicit_limit(self):
        for i in range(4):
            self.stream.emit("account.configuring", self.corr, message=f"m{i}")
        self.assertEqual(len(self.stream.recent(limit=2)), 2)

    def test_a_flow_is_reconstructable_by_filtering_on_correlation_id(self):
        other = _new_correlation_id()
        self.stream.emit("account.create_started", self.corr)
        self.stream.emit("account.create_started", other)
        self.stream.emit("account.online", self.corr)

        mine = [e for e in self.stream.recent() if e["correlation_id"] == self.corr]
        self.assertEqual(
            [e["event_type"] for e in mine],
            ["account.create_started", "account.online"],
        )


class TestSubscriptions(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = WizardEventStream()
        self.corr = _new_correlation_id()

    def test_a_subscriber_receives_subsequently_emitted_events(self):
        q = self.stream.subscribe()
        self.addCleanup(self.stream.unsubscribe, q)

        self.stream.emit("account.online", self.corr, account_id="acct-live")
        received = q.get(timeout=2)
        self.assertEqual(received["event_type"], "account.online")
        self.assertEqual(received["account_id"], "acct-live")

    def test_a_late_subscriber_receives_the_existing_backlog(self):
        """A dashboard opened mid-flow must still see what already happened."""
        self.stream.emit("account.create_started", self.corr)
        self.stream.emit("account.configuring", self.corr)

        q = self.stream.subscribe()
        self.addCleanup(self.stream.unsubscribe, q)

        names = [q.get(timeout=2)["event_type"] for _ in range(2)]
        self.assertEqual(names, ["account.create_started", "account.configuring"])

    def test_multiple_subscribers_each_receive_every_event(self):
        first = self.stream.subscribe()
        second = self.stream.subscribe()
        self.addCleanup(self.stream.unsubscribe, first)
        self.addCleanup(self.stream.unsubscribe, second)

        self.stream.emit("account.removed", self.corr)
        self.assertEqual(first.get(timeout=2)["event_type"], "account.removed")
        self.assertEqual(second.get(timeout=2)["event_type"], "account.removed")

    def test_unsubscribing_stops_delivery(self):
        q = self.stream.subscribe()
        self.stream.unsubscribe(q)
        self.stream.emit("account.online", self.corr)
        with self.assertRaises(queue.Empty):
            q.get(timeout=0.2)

    def test_unsubscribing_twice_is_harmless(self):
        q = self.stream.subscribe()
        self.stream.unsubscribe(q)
        self.stream.unsubscribe(q)  # must not raise

    def test_one_slow_subscriber_does_not_block_another(self):
        """A full queue must be dropped, never allowed to stall the emitter."""
        slow: queue.Queue = queue.Queue(maxsize=1)
        self.stream._subscribers.append(slow)
        self.addCleanup(self.stream.unsubscribe, slow)

        healthy = self.stream.subscribe()
        self.addCleanup(self.stream.unsubscribe, healthy)

        for _ in range(5):
            self.stream.emit("account.health_check", self.corr)

        # The healthy subscriber still received events despite the full queue.
        self.assertEqual(healthy.get(timeout=2)["event_type"], "account.health_check")


class TestEventRedaction(unittest.TestCase):
    """Events are broadcast to browsers, so no secret may survive emission."""

    SECRET = "sk-live-EVENTSHOULDNEVERLEAK0000111122223333"

    def setUp(self) -> None:
        self.stream = WizardEventStream()
        self.corr = _new_correlation_id()

    def test_a_secret_in_extra_is_not_broadcast_verbatim(self):
        event = self.stream.emit(
            "account.authentication_started",
            self.corr,
            extra={"api_key": self.SECRET},
        )
        self.assertNotIn(self.SECRET, str(event))

    def test_a_secret_in_the_message_is_not_broadcast_verbatim(self):
        event = self.stream.emit(
            "account.authentication_failed",
            self.corr,
            message=f"rejected key {self.SECRET}",
        )
        self.assertNotIn(self.SECRET, str(event))

    def test_a_subscriber_never_receives_the_secret(self):
        q = self.stream.subscribe()
        self.addCleanup(self.stream.unsubscribe, q)
        self.stream.emit(
            "account.create_started", self.corr, extra={"token": self.SECRET}
        )
        self.assertNotIn(self.SECRET, str(q.get(timeout=2)))

    def test_history_never_retains_the_secret(self):
        self.stream.emit(
            "account.configuring", self.corr, extra={"password": self.SECRET}
        )
        self.assertNotIn(self.SECRET, str(self.stream.recent()))


if __name__ == "__main__":
    unittest.main()
