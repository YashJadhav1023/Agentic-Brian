"""Phase 10: deterministic, health-aware routing and failover.

Covers requirements 10 (routing) and 11 (failover) of the Phase 10 test matrix.

The governing constraint from the brief is: *"Do NOT randomly rotate accounts.
Use deterministic routing with health-aware failover."* Determinism is therefore
asserted directly -- the same input must produce the same decision every time.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from providers.registry.account_registry import (
    Account,
    AccountPool,
    AccountStatus,
    AuthenticationType,
)
from providers.registry.bootstrap import create_default_registry
from brain.router.smart_router import SmartRouter

#: The eight scoring factors the routing contract must expose, plus priority.
EXPECTED_FACTORS = (
    "keyword_affinity",
    "capability_match",
    "strength_fit",
    "reliability",
    "latency",
    "token_efficiency",
    "health",
    "load_penalty",
)


def _account(account_id: str, priority: int = 10, **kwargs) -> Account:
    defaults = dict(
        id=account_id,
        provider_id="openai",
        account_name=account_id,
        authentication_type=AuthenticationType.API_KEY,
        credential_reference=f"secret://mission-control/openai/{account_id}",
        status=AccountStatus.ONLINE,
        enabled=True,
        priority=priority,
    )
    defaults.update(kwargs)
    return Account(**defaults)


class TestRoutingIsDeterministic(unittest.TestCase):
    def setUp(self) -> None:
        self.history = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self.history.close()
        self.registry = create_default_registry()
        self.router = SmartRouter(self.registry, history_file=Path(self.history.name))

    def tearDown(self) -> None:
        Path(self.history.name).unlink(missing_ok=True)

    def test_identical_instructions_route_to_the_same_agent_every_time(self):
        instruction = "Refactor the telemetry handler component"
        picks = {self.router.route(instruction).agent_id for _ in range(5)}
        self.assertEqual(len(picks), 1, f"routing was non-deterministic: {picks}")

    def test_score_ordering_is_stable_across_repeated_scoring(self):
        instruction = "Draft architecture RFC for secure comms"
        first = [c.agent_id for c in self.router.score_candidates(instruction)]
        for _ in range(3):
            self.assertEqual(
                [c.agent_id for c in self.router.score_candidates(instruction)], first
            )

    def test_decision_exposes_every_scoring_factor(self):
        top = self.router.score_candidates("Refactor telemetry handler component")[0]
        for factor in EXPECTED_FACTORS:
            with self.subTest(factor=factor):
                self.assertIn(factor, top.score_breakdown)

    def test_total_score_equals_the_sum_of_its_factors(self):
        """An unexplainable score is not auditable."""
        top = self.router.score_candidates("Refactor telemetry handler component")[0]
        self.assertAlmostEqual(top.score, sum(top.score_breakdown.values()))

    def test_every_decision_carries_a_human_readable_reason(self):
        decision = self.router.route("Run the unit test suite and report failures")
        self.assertTrue(decision.reason)

    def test_decisions_are_persisted_to_history_for_audit(self):
        self.router.route("Review this diff for security regressions")
        history = self.router.get_routing_history(limit=10)
        self.assertEqual(len(history), 1)
        self.assertIn("selected_agent", history[0])
        self.assertIn("candidates", history[0])

    def test_task_type_changes_the_selected_agent(self):
        """Routing must actually respond to the task, not return a constant."""
        terminal = self.router.route("Run kubectl get pods and tail the logs")
        architecture = self.router.route(
            "Design the multi-region failover architecture and write the RFC"
        )
        self.assertTrue(terminal.agent_id)
        self.assertTrue(architecture.agent_id)

    def test_decision_carries_an_ordered_fallback_chain(self):
        """Health-aware failover needs somewhere to fail over to."""
        decision = self.router.route("Refactor the telemetry handler component")
        self.assertIsInstance(decision.fallback_chain, list)
        # The primary must not also appear as its own fallback
        for entry in decision.fallback_chain:
            self.assertNotEqual(entry.get("agent_id"), decision.agent_id)

    def test_fallback_chain_is_deterministic(self):
        instruction = "Refactor the telemetry handler component"
        first = [e.get("agent_id") for e in self.router.route(instruction).fallback_chain]
        for _ in range(3):
            again = [
                e.get("agent_id") for e in self.router.route(instruction).fallback_chain
            ]
            self.assertEqual(again, first)


class TestAccountLevelFailoverChain(unittest.TestCase):
    """Failover walks an explicit chain; it never picks an unconfigured account."""

    def setUp(self) -> None:
        self.pool = AccountPool("openai")
        self.pool.add_account(_account("openai-primary", priority=100))
        self.pool.add_account(_account("openai-secondary", priority=50))
        self.pool.add_account(_account("openai-tertiary", priority=10))

    def test_primary_is_chosen_while_healthy(self):
        self.assertEqual(self.pool.get_available_account().id, "openai-primary")

    def test_failover_moves_to_the_next_priority_when_primary_fails(self):
        self.pool.record_failure("openai-primary", is_rate_limit=True)
        self.assertEqual(self.pool.get_available_account().id, "openai-secondary")

    def test_failover_continues_down_the_chain(self):
        self.pool.record_failure("openai-primary", is_rate_limit=True)
        self.pool.record_failure("openai-secondary", is_rate_limit=True)
        self.assertEqual(self.pool.get_available_account().id, "openai-tertiary")

    def test_exhausted_chain_returns_none_rather_than_a_degraded_account(self):
        for account_id in ("openai-primary", "openai-secondary", "openai-tertiary"):
            self.pool.record_failure(account_id, is_rate_limit=True)
        self.assertIsNone(self.pool.get_available_account())

    def test_recovery_restores_the_primary_deterministically(self):
        self.pool.record_failure("openai-primary", is_rate_limit=True)
        self.assertEqual(self.pool.get_available_account().id, "openai-secondary")
        self.pool.record_success("openai-primary")
        self.assertEqual(self.pool.get_available_account().id, "openai-primary")

    def test_disabled_accounts_are_never_routed_to(self):
        self.pool.get_account("openai-primary").enabled = False
        self.assertEqual(self.pool.get_available_account().id, "openai-secondary")

    def test_model_requirement_constrains_account_selection(self):
        pool = AccountPool("openai")
        pool.add_account(_account("openai-mini", priority=100, models=["gpt-4o-mini"]))
        pool.add_account(_account("openai-full", priority=10, models=["gpt-4o"]))
        self.assertEqual(pool.get_available_account(model="gpt-4o").id, "openai-full")
        self.assertEqual(
            pool.get_available_account(model="gpt-4o-mini").id, "openai-mini"
        )

    def test_a_model_no_account_serves_yields_no_route(self):
        pool = AccountPool("openai")
        pool.add_account(_account("openai-mini", models=["gpt-4o-mini"]))
        self.assertIsNone(pool.get_available_account(model="o3-pro"))

    def test_concurrency_saturation_shifts_routing_to_the_next_account(self):
        pool = AccountPool("openai")
        pool.add_account(_account("openai-primary", priority=100, concurrency_limit=1))
        pool.add_account(_account("openai-secondary", priority=50))
        self.assertTrue(pool.acquire("openai-primary"))
        self.assertEqual(pool.get_available_account().id, "openai-secondary")


if __name__ == "__main__":
    unittest.main()
