"""Integration tests for Phase 23: Cross-Feature Regression Suite.

Verifies that Phases 1-22 subsystems (Routing, Account Registry, Approval Gates,
Task Lifecycle, Memory Store, Steering & Rule Precedence, Knowledge Index)
remain completely functional and unbroken with Phase 23 ECC Federation active.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.knowledge.knowledge_index import KnowledgeIndex
from brain.knowledge.steering_registry import SteeringDocument, SteeringRegistry, SteeringScope
from brain.mission_brain import MissionBrain
from brain.orchestrator.orchestrator import Orchestrator
from brain.resources.ecc_federation import (
    ECCFederationManager,
    RULE_PRECEDENCE_ECC_IMPORTED_RULES,
    RULE_PRECEDENCE_EXISTING_REPO_RULES,
    RULE_PRECEDENCE_MISSION_ARCHITECTURE,
    RULE_PRECEDENCE_PROJECT_STEERING,
    RULE_PRECEDENCE_SAFETY_GOVERNANCE,
)
from brain.resources.resource_model import ResourceLifecycleState
from brain.router.smart_router import SmartRouter
from memory.store.memory_store import MemoryScope, MemoryStore
from providers.registry.bootstrap import create_default_registry
from tasks.manager import Task, TaskManager, TaskStatus


class TestPhase23CrossFeatureRegression(unittest.TestCase):
    """Verify that core subsystems operate correctly alongside Phase 23 capabilities."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.registry = create_default_registry()
        self.router = SmartRouter(self.registry)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_smart_router_baseline_unbroken(self) -> None:
        """SmartRouter must route everyday coding and architecture tasks without crashing."""
        # 1. Simple coding task
        dec1 = self.router.route("Fix python indentation error in parser.py")
        self.assertIsNotNone(dec1.account_id)
        self.assertIsNotNone(dec1.agent_id)
        self.assertTrue(len(dec1.candidates) > 0)

        # 2. Complex architecture task
        dec2 = self.router.route("Design a distributed multi-agent consensus algorithm")
        self.assertIsNotNone(dec2.account_id)
        self.assertIsNotNone(dec2.agent_id)

        # 3. Explain routing works without ECC enabled
        explanation = self.router.explain_routing("Refactor database schema")
        self.assertIn("selected_account", explanation)
        self.assertIn("candidate_scores", explanation)
        self.assertEqual(explanation.get("recommended_ecc_skills"), [])

    def test_account_registry_integrity_and_anomaly_handling(self) -> None:
        """AccountRegistry must list all 8 accounts and handle openai-generic-1 non-enum capabilities."""
        accounts = self.registry.account_registry.list_accounts()
        self.assertEqual(len(accounts), 8, "Expected exactly 8 registered provider accounts")

        # Documented anomaly: openai-generic-1 has non-Capability enum capabilities
        openai_gen = self.registry.account_registry.get_account("openai-generic-1")
        self.assertIsNotNone(openai_gen)
        self.assertIn("chat", openai_gen.capabilities)
        self.assertIn("streaming", openai_gen.capabilities)

        # Ensure active routable accounts are discovered
        active_accounts = [a for a in accounts if a.status.value.lower() in ("active", "healthy", "ready", "online")]
        self.assertGreaterEqual(len(active_accounts), 7)

    def test_rule_precedence_hierarchy_enforcement(self) -> None:
        """Verify strict rule precedence: Safety > Steering > Architecture > Repo Rules > ECC Rules."""
        self.assertGreater(RULE_PRECEDENCE_SAFETY_GOVERNANCE, RULE_PRECEDENCE_PROJECT_STEERING)
        self.assertGreater(RULE_PRECEDENCE_PROJECT_STEERING, RULE_PRECEDENCE_MISSION_ARCHITECTURE)
        self.assertGreater(RULE_PRECEDENCE_MISSION_ARCHITECTURE, RULE_PRECEDENCE_EXISTING_REPO_RULES)
        self.assertGreater(RULE_PRECEDENCE_EXISTING_REPO_RULES, RULE_PRECEDENCE_ECC_IMPORTED_RULES)

        # Verify numerical values
        self.assertEqual(RULE_PRECEDENCE_SAFETY_GOVERNANCE, 100)
        self.assertEqual(RULE_PRECEDENCE_PROJECT_STEERING, 60)
        self.assertEqual(RULE_PRECEDENCE_MISSION_ARCHITECTURE, 50)
        self.assertEqual(RULE_PRECEDENCE_EXISTING_REPO_RULES, 40)
        self.assertEqual(RULE_PRECEDENCE_ECC_IMPORTED_RULES, 15)

        # Verify registration in SteeringRegistry keeps priority intact
        mb = MissionBrain()
        fed = mb.ecc_federation
        fed.federate()

        # Local safety rule outranks ECC imported rule
        rules = mb.steering_registry.list_documents()
        # Any ECC rule registered has priority 15
        for r in rules:
            if "ecc" in r.tags:
                self.assertEqual(r.priority, 15)

    def test_approval_gate_unbroken(self) -> None:
        """Approval gate must evaluate risks and refuse gated destructive tasks without execution."""
        orch = Orchestrator(workspace_dir=self.tmp_path / "orch")

        # Destructive instruction: gated at dispatch
        dest_task = orch.plan_and_dispatch(
            "Wipe the cache directory completely and purge stale logs"
        )
        self.assertTrue(dest_task.requires_approval)
        self.assertIsNotNone(dest_task.approval_reason)

        # Swarm execution refuses gated task without calling any adapter
        results = orch.execute_next()
        self.assertEqual(results, [])
        fresh = orch.tasks.get_task(dest_task.task_id)
        self.assertEqual(fresh.status, TaskStatus.BLOCKED)
        self.assertEqual(fresh.stage, "BLOCKED_ON_APPROVAL")

    def test_task_manager_lifecycle_unbroken(self) -> None:
        """TaskManager must handle task creation, status progression, and retrieval."""
        tm = TaskManager(root_tasks_dir=self.tmp_path / "tasks")
        task = tm.create_task(
            title="Regression Task",
            description="Regression test task for Phase 23",
            assigned_agent="test-agent",
        )
        self.assertEqual(task.status, TaskStatus.READY)

        # Move to RUNNING
        tm.update_status(task.task_id, TaskStatus.RUNNING)
        t_running = tm.get_task(task.task_id)
        self.assertIsNotNone(t_running)
        self.assertEqual(t_running.status, TaskStatus.RUNNING)

        # Complete task
        tm.update_status(task.task_id, TaskStatus.COMPLETED)
        t_completed = tm.get_task(task.task_id)
        self.assertIsNotNone(t_completed)
        self.assertEqual(t_completed.status, TaskStatus.COMPLETED)

    def test_memory_store_unbroken(self) -> None:
        """MemoryStore operations (store, query by scope/importance) must function cleanly."""
        store = MemoryStore(db_path=self.tmp_path / "test_memory.db")
        entry = store.add(
            content="Important architecture decision for Phase 23",
            scope=MemoryScope.GLOBAL,
            importance=9,
            tags=["p23", "arch"],
        )
        self.assertIsNotNone(entry.memory_id)

        # Query back
        res = store.query_memories(search="architecture", scope=MemoryScope.GLOBAL, min_importance=5)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].memory_id, entry.memory_id)
        self.assertEqual(res[0].importance, 9)

    def test_ecc_federation_does_not_pollute_clean_slate(self) -> None:
        """When ECC is disabled or reset, baseline registries must remain completely operational."""
        mb = MissionBrain()
        fed = mb.ecc_federation
        fed.federate()

        # Emergency disable all ECC
        disabled_count = fed.disable_all()
        self.assertGreater(disabled_count, 0)

        # Baseline router still functions
        router = SmartRouter(mb.provider_registry)
        dec = router.route("Verify system sanity after emergency disable")
        self.assertIsNotNone(dec.account_id)
        self.assertIsNotNone(dec.agent_id)
