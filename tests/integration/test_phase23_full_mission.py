"""Integration tests for Phase 23: Full Mission Orchestration with ECC Federation.

Tests end-to-end task workflow through MissionBrain with federated capabilities:
1. Full federation bootstrap & safe DISCOVERED initial states (no auto-activation)
2. Safe capability routing recommendation when ECC capability is enabled
3. Exclusion of disabled or Category D capabilities from routing
4. Mission dispatch and execution respecting approval gates
5. Emergency kill-switch rollback leaving core mission brain healthy
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.mission_brain import MissionBrain
from brain.orchestrator.orchestrator import Orchestrator
from brain.resources.ecc_normalizer import ECCCategory
from brain.resources.resource_model import ResourceLifecycleState
from tasks.manager import TaskStatus


class TestPhase23FullMission(unittest.TestCase):
    """End-to-end test of MissionBrain with ECC federation and governance gates."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.state_file = self.tmp_path / "external_capabilities.json"
        self.brain = MissionBrain()
        self.fed = self.brain.ecc_federation
        self.fed.state_file = self.state_file
        self.fed._globally_enabled = True
        self.fed._enabled_resources.clear()
        self.fed._disabled_resources.clear()
        self.brain.resource_registry._resources.clear()

    def tearDown(self) -> None:
        self.fed.disable_all()
        self.tmpdir.cleanup()

    def test_01_federation_bootstrap_lifecycle_invariants(self) -> None:
        """Federation bootstrap catalogs 681 components in safe DISCOVERED state."""
        report = self.fed.federate()
        self.assertGreater(sum(report.values()), 600)

        # Inspect resources in registry
        ecc_resources = [
            r for r in self.brain.resource_registry.list()
            if "ecc" in r.tags
        ]
        self.assertEqual(len(ecc_resources), 681)

        # Check Category D items are strictly DISABLED
        cat_d_resources = [
            r for r in ecc_resources
            if r.metadata.get("category") == "D"
        ]
        self.assertEqual(len(cat_d_resources), 35)
        for r in cat_d_resources:
            self.assertEqual(r.lifecycle_state, ResourceLifecycleState.DISABLED)
            self.assertFalse(r.availability)

        # Category A and B items default to DISCOVERED (safe, non-active)
        safe_non_d = [r for r in ecc_resources if r.metadata.get("category") in ("A", "B", "C")]
        for r in safe_non_d:
            self.assertEqual(r.lifecycle_state, ResourceLifecycleState.DISCOVERED)
            self.assertFalse(r.availability)

    def test_02_enable_capability_and_route_explanation(self) -> None:
        """Enabling an ECC capability surfaces it in routing explainability."""
        self.fed.federate()
        target_id = "ecc:skill:react-performance"
        ok = self.fed.enable(target_id)
        self.assertTrue(ok)

        # Explain routing via SmartRouter
        explanation = self.brain.smart_router.explain_routing(
            "Profile and optimize React virtual DOM re-renders in component tree"
        )
        matched = explanation.get("recommended_ecc_skills", [])
        self.assertIn(target_id, matched)

        # Disabling capability excludes it immediately
        self.fed.disable(target_id)
        explanation_after = self.brain.smart_router.explain_routing(
            "Profile and optimize React virtual DOM re-renders in component tree"
        )
        self.assertNotIn(target_id, explanation_after.get("recommended_ecc_skills", []))

    def test_03_mission_task_dispatch_with_approval_gates(self) -> None:
        """Mission task dispatching correctly enforces approval gates during orchestration."""
        # 1. Test safe task dispatch
        orch_safe = Orchestrator(workspace_dir=self.tmp_path / "orch_safe")
        safe_task = orch_safe.plan_and_dispatch("Review system architecture and list active services")
        self.assertFalse(safe_task.requires_approval)

        # 2. Test destructive task requiring approval in isolated orchestrator
        orch_dest = Orchestrator(workspace_dir=self.tmp_path / "orch_dest")
        dest_task = orch_dest.plan_and_dispatch("rm -rf /tmp/test_dir and wipe database tables")
        self.assertTrue(dest_task.requires_approval)
        self.assertEqual(dest_task.status, TaskStatus.READY)

        # Swarm execution refuses destructive task without approval
        res = orch_dest.execute_next()
        self.assertEqual(res, [])
        refreshed = orch_dest.tasks.get_task(dest_task.task_id)
        self.assertEqual(refreshed.status, TaskStatus.BLOCKED)

        # Approve task and verify unblocking
        approved_task = orch_dest.approve_task(dest_task.task_id)
        self.assertFalse(approved_task.requires_approval)

    def test_04_emergency_kill_switch_during_mission(self) -> None:
        """Emergency kill-switch immediately disables all ECC capabilities while mission brain remains healthy."""
        self.fed.federate()
        self.fed.enable("ecc:skill:docker-patterns")
        self.fed.enable("ecc:skill:react-performance")

        # Emergency kill switch
        disabled_count = self.fed.disable_all()
        self.assertEqual(disabled_count, 681)
        self.assertFalse(self.fed.is_enabled())

        # All resources now DISABLED
        for r in self.brain.resource_registry.list():
            if "ecc" in r.tags:
                self.assertEqual(r.lifecycle_state, ResourceLifecycleState.DISABLED)

        # Routing still functions flawlessly with local accounts
        dec = self.brain.smart_router.route("Implement unit tests for user authentication")
        self.assertIsNotNone(dec.account_id)
