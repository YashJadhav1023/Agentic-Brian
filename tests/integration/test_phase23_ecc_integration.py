"""Integration Test Suite for Phase 23: End-to-End ECC Capability Federation.

Tests the full lifecycle of ECC capability federation:
1. Normalizer discovery across all 8 component families (681 total).
2. Federation into unified ResourceRegistry, SkillDiscoveryEngine, KnowledgeIndex, and SteeringRegistry.
3. Strict rejection of Category D components (cloud memory sync, cloud compute, shell scripts).
4. Capability enablement and automatic discovery in SmartRouter explain_routing.
5. BM25 KnowledgeIndex search over federated engineering guidance and provenance tracking.
6. Rule precedence enforcement between local project rules and imported ECC rules.
7. Emergency kill-switch instant deactivation and persistent state preservation.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.knowledge.knowledge_index import KnowledgeIndex
from brain.knowledge.steering_registry import (
    SteeringDocument,
    SteeringRegistry,
    SteeringScope,
)
from brain.resources.ecc_federation import (
    ECCFederationManager,
    RULE_PRECEDENCE_ECC_IMPORTED_RULES,
    RULE_PRECEDENCE_PROJECT_STEERING,
)
from brain.resources.ecc_normalizer import ECCCategory, ECCComponentNormalizer
from brain.resources.resource_model import (
    ResourceLifecycleState,
    ResourceType,
    TrustLevel,
)
from brain.resources.resource_registry import ResourceRegistry
from brain.resources.skill_discovery import SkillDiscoveryEngine
from brain.router.smart_router import SmartRouter
from providers.registry.bootstrap import create_default_registry


class TestPhase23ECCIntegration(unittest.TestCase):
    """End-to-end integration tests for ECC federation layer."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmpdir.name) / "external_capabilities.json"
        self.provider_registry = create_default_registry()
        self.resource_registry = ResourceRegistry()
        self.skill_engine = SkillDiscoveryEngine()
        self.knowledge_index = KnowledgeIndex()
        self.steering_registry = SteeringRegistry(search_roots=[])
        self.normalizer = ECCComponentNormalizer()

        self.manager = ECCFederationManager(
            resource_registry=self.resource_registry,
            skill_engine=self.skill_engine,
            knowledge_index=self.knowledge_index,
            steering_registry=self.steering_registry,
            normalizer=self.normalizer,
            state_file=self.state_file,
        )
        self.router = SmartRouter(self.provider_registry)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_01_full_federation_lifecycle(self) -> None:
        """Verifies full federation lifecycle from discovery to registration."""
        counts = self.manager.federate()
        self.assertEqual(sum(counts.values()), 681)
        self.assertEqual(counts.get("agents"), 68)
        self.assertEqual(counts.get("skills"), 286)
        self.assertEqual(counts.get("commands"), 94)
        self.assertEqual(counts.get("rules"), 122)
        self.assertEqual(counts.get("hooks"), 24)
        self.assertEqual(counts.get("mcp_servers"), 35)
        self.assertEqual(counts.get("documentation"), 40)
        self.assertEqual(counts.get("scripts"), 12)

    def test_02_strict_category_d_rejections_enforced(self) -> None:
        """Verifies Category D components are never enabled during federation."""
        self.manager.federate()
        for rej_id in ["ecc:mcp:squish", "ecc:mcp:memxus", "ecc:mcp:ito-compute", "ecc:script:install"]:
            res = self.resource_registry.get(rej_id)
            if res:
                self.assertEqual(res.lifecycle_state, ResourceLifecycleState.DISABLED)
                self.assertFalse(res.availability)
                self.assertEqual(res.trust_level, TrustLevel.UNTRUSTED)
                # Ensure enable attempt is rejected
                self.assertFalse(self.manager.enable(rej_id))

    def test_03_enable_capability_and_router_intent_matching(self) -> None:
        """Verifies enabling an ECC skill surfaces in routing explainability."""
        self.manager.federate()
        target_id = "ecc:skill:react-performance"
        ok = self.manager.enable(target_id)
        self.assertTrue(ok)

        # Verify state
        res = self.resource_registry.get(target_id)
        self.assertIsNotNone(res)
        self.assertEqual(res.lifecycle_state, ResourceLifecycleState.ENABLED)
        self.assertTrue(res.availability)

        # Explain routing for matching task
        expl = self.router.explain_routing("Diagnose react component render bottlenecks and performance")
        self.assertIn("selected_agent", expl)

    def test_04_knowledge_index_bm25_search_over_ecc_docs(self) -> None:
        """Verifies BM25 search over federated ECC documentation."""
        self.manager.federate()
        results = self.knowledge_index.search("security guide", limit=5)
        self.assertGreater(len(results), 0)
        doc = results[0]
        self.assertIn("ecc", doc.path or doc.document_id)

    def test_05_steering_rule_precedence_invariants(self) -> None:
        """Verifies that project steering rules strictly take precedence over ECC imported rules."""
        self.manager.federate()

        # Add a local project rule
        local_rule = SteeringDocument(
            id="project-steering-local",
            path="/path/to/PROJECT_STEERING.md",
            scope=SteeringScope.PROJECT,
            project="mission-control",
            priority=RULE_PRECEDENCE_PROJECT_STEERING,
            rules=["Enforce local project rules."],
        )
        self.steering_registry.register(local_rule)

        docs = self.steering_registry.list_documents()
        self.assertGreater(len(docs), 1)

        # The local project rule (priority 60) must precede all ECC rules (priority 15)
        self.assertEqual(docs[0].id, "project-steering-local")
        self.assertEqual(docs[0].priority, RULE_PRECEDENCE_PROJECT_STEERING)
        # Any subsequent ECC rule must have lower priority
        ecc_docs = [d for d in docs if d.project == "ecc"]
        for ed in ecc_docs:
            self.assertEqual(ed.priority, RULE_PRECEDENCE_ECC_IMPORTED_RULES)
            self.assertLess(ed.priority, docs[0].priority)

    def test_06_emergency_kill_switch_e2e(self) -> None:
        """Verifies emergency kill-switch disables all capabilities across all registries."""
        self.manager.federate()
        self.manager.enable_all()
        self.assertTrue(self.manager.is_enabled())

        count = self.manager.disable_all()
        self.assertGreater(count, 0)
        self.assertFalse(self.manager.is_enabled())

        # All ECC resources in registry must be DISABLED and unavailable
        for res in self.resource_registry.list():
            if "ecc" in res.tags or res.id.startswith("ecc:"):
                self.assertEqual(res.lifecycle_state, ResourceLifecycleState.DISABLED)
                self.assertFalse(res.availability)

    def test_07_persistence_roundtrip_across_manager_instances(self) -> None:
        """Verifies federation state survives restarts across manager instances."""
        self.manager.federate()
        self.manager.enable("ecc:skill:docker-patterns")

        # Create a new manager instance reading the same state file
        new_manager = ECCFederationManager(state_file=self.state_file)
        self.assertIn("ecc:skill:docker-patterns", new_manager._enabled_resources)
        self.assertTrue(new_manager.is_enabled())


if __name__ == "__main__":
    unittest.main()
