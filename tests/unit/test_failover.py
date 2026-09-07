"""Unit tests for Phase 4C Antigravity Account Failover.

Covers error classification, retryability detection, interchangeable AG-1 <-> AG-2
failover, max failover bounds, and event emission.
"""
import unittest

import tempfile
from pathlib import Path

from brain.orchestrator.failover import FailoverDecision, FailoverManager
from events.bus import EventBus, EventType
from providers.registry.bootstrap import create_default_registry
from tasks.manager import Task


class TestFailoverManager(unittest.TestCase):
    def setUp(self):
        self.tmp_log = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self.tmp_log.close()
        self.bus = EventBus(log_path=Path(self.tmp_log.name))
        self.registry = create_default_registry()
        self.manager = FailoverManager(
            registry=self.registry,
            event_bus=self.bus,
            max_failovers_per_task=2,
        )
        self.task = Task(task_id="t-1", title="Test task", description="Do work")

    def tearDown(self):
        Path(self.tmp_log.name).unlink(missing_ok=True)

    def test_classify_errors(self):
        self.assertEqual(self.manager.classify_error("HTTP 429: Too Many Requests"), "rate_limit")
        self.assertEqual(self.manager.classify_error("Error: RESOURCE_EXHAUSTED"), "rate_limit")
        self.assertEqual(self.manager.classify_error("Execution timed out", exit_code=124), "timeout")
        self.assertEqual(self.manager.classify_error("Fatal: SyntaxError in code"), "permanent_error")

    def test_retryability_check(self):
        self.assertTrue(self.manager.is_retryable("HTTP 429 quota exceeded"))
        self.assertTrue(self.manager.is_retryable("timeout waiting for response"))
        self.assertFalse(self.manager.is_retryable("File not found"))

    def test_antigravity_account_1_fails_over_to_account_2(self):
        decision = self.manager.evaluate_failover(
            task=self.task,
            failed_agent_id="antigravity-account-1",
            error_text="RESOURCE_EXHAUSTED: Quota limit reached",
            exit_code=1,
            attempted_agents=["antigravity-account-1"],
        )
        self.assertTrue(decision.should_failover)
        self.assertEqual(decision.fallback_agent_id, "antigravity-account-2")
        self.assertEqual(decision.failover_count, 1)

    def test_antigravity_account_2_fails_over_to_account_1(self):
        decision = self.manager.evaluate_failover(
            task=self.task,
            failed_agent_id="antigravity-account-2",
            error_text="Rate limit exceeded",
            exit_code=1,
            attempted_agents=["antigravity-account-2"],
        )
        self.assertTrue(decision.should_failover)
        self.assertEqual(decision.fallback_agent_id, "antigravity-account-1")

    def test_max_failovers_enforced(self):
        decision = self.manager.evaluate_failover(
            task=self.task,
            failed_agent_id="kiro",
            error_text="Rate limit exceeded",
            exit_code=1,
            attempted_agents=["antigravity-account-1", "antigravity-account-2", "kiro"],
        )
        self.assertFalse(decision.should_failover)
        self.assertIn("Max failovers (2) reached", decision.reason)

    def test_unhealthy_fallback_is_skipped(self):
        ag2 = self.registry.get_adapter("antigravity-account-2")
        ag2.health = lambda: (False, "Unhealthy test error")
        decision = self.manager.evaluate_failover(
            task=self.task,
            failed_agent_id="antigravity-account-1",
            error_text="Rate limit exceeded",
            exit_code=1,
            attempted_agents=["antigravity-account-1"],
        )
        # Should not select unhealthy AG-2, falls over to next available (kiro-cli or cline)
        self.assertNotEqual(decision.fallback_agent_id, "antigravity-account-2")

    def test_event_emitted_on_failover(self):
        self.manager.evaluate_failover(
            task=self.task,
            failed_agent_id="antigravity-account-1",
            error_text="Rate limit exceeded",
            exit_code=1,
            attempted_agents=["antigravity-account-1"],
        )
        events = [e for e in self.bus.get_recent_events(10) if e.event_type == EventType.AGENT_FAILOVER_TRIGGERED]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].metadata["from_agent"], "antigravity-account-1")
        self.assertEqual(events[0].metadata["to_agent"], "antigravity-account-2")


if __name__ == "__main__":
    unittest.main()
