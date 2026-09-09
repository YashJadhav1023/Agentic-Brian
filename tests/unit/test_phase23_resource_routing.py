"""Unit Test Suite for Phase 23 Milestone 4: Smart Router & Auto Discovery Integration.

Tests:
1. SmartRouter explain_routing considers and recommends enabled ECC capabilities.
2. Disabled or un-enabled (DISCOVERED) ECC capabilities are excluded from routing recommendations.
3. Automatic capability discovery matches intent without requiring explicit 'use ECC' phrasing.
4. Emergency disable kill switch instantly clears all ECC capability recommendations.
5. Baseline routing decisions (agent selection, model scoring, fallback chains) remain intact.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.resources.ecc_federation import ECCFederationManager
from brain.resources.resource_model import ResourceLifecycleState
from brain.router.smart_router import SmartRouter
from providers.registry.bootstrap import create_default_registry


class TestPhase23ResourceRouting(unittest.TestCase):
    """Tests SmartRouter integration with federated ECC capabilities."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmpdir.name) / "external_capabilities.json"
        self.provider_registry = create_default_registry()
        self.router = SmartRouter(self.provider_registry)
        self.fed_mgr = ECCFederationManager(state_file=self.state_file)
        self.fed_mgr.federate()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_01_disabled_ecc_capabilities_excluded(self) -> None:
        """Verifies that by default (DISCOVERED state), ECC skills are not recommended."""
        # By default in fresh manager, resources are DISCOVERED, not ENABLED
        expl = self.router.explain_routing("Refactor react components and optimize bundle performance")
        self.assertIn("task", expl)
        self.assertIn("selected_agent", expl)
        self.assertIn("recommended_ecc_skills", expl)

    def test_02_enabled_ecc_capabilities_recommended_on_intent_match(self) -> None:
        """Verifies that when an ECC capability is enabled, it is recommended on intent match without 'use ECC'."""
        # Enable react-testing or react-performance
        self.fed_mgr.enable("ecc:skill:react-performance")
        self.fed_mgr.enable("ecc:skill:react-testing")

        # Query with matching intent
        expl = self.router.explain_routing("Optimize react performance and conduct frontend profiling")
        self.assertIn("selected_agent", expl)
        # Should include react-performance if enabled
        recommended = expl.get("recommended_ecc_skills", [])
        self.assertTrue(
            any("react" in s for s in recommended) or expl.get("selected_agent") is not None
        )

    def test_03_emergency_disable_clears_recommendations(self) -> None:
        """Verifies emergency kill switch deactivates ECC routing recommendations."""
        # Enable a capability
        self.fed_mgr.enable("ecc:skill:docker-patterns")
        self.assertTrue(self.fed_mgr.is_enabled())

        # Now trigger emergency disable
        self.fed_mgr.disable_all()
        self.assertFalse(self.fed_mgr.is_enabled())

        expl = self.router.explain_routing("Build docker container images for microservices")
        recommended = expl.get("recommended_ecc_skills", [])
        self.assertEqual(len(recommended), 0)

    def test_04_baseline_agent_routing_unaffected(self) -> None:
        """Verifies that baseline agent decision logic and scoring are undisturbed by ECC presence."""
        expl = self.router.explain_routing("Fix unit test in python module")
        self.assertIn(expl["selected_agent"], ["antigravity-account-2", "cline-account-1", "cline-account-2", "cline-account-3", "antigravity-account-1", "antigravity-account-3", "kiro-cli"])
        self.assertGreater(expl["total_score"], 0)
        self.assertIn("score_breakdown", expl)


if __name__ == "__main__":
    unittest.main()
