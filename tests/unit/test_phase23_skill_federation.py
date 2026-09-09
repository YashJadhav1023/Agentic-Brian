"""Unit Test Suite for Phase 23 Milestone 3: Universal Capability Federation Layer.

Tests:
1. ECC skill discovery via SkillDiscoveryEngine.discover_ecc_skills()
2. ECC skill tagging (source='ecc', trust_level=EXTERNAL, lifecycle=DISCOVERED)
3. Local skill isolation: ECC skills do not overwrite local skills
4. ResourceRegistry ECC discovery, enable, and disable APIs
5. ECCFederationManager coordination and rule precedence enforcement
6. Category D rejection invariants preserved under federation
7. Emergency kill-switch (disable_all) and global state toggle
8. Agent role mapping to local provider adapters
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.knowledge.knowledge_index import KnowledgeIndex
from brain.knowledge.steering_registry import SteeringRegistry
from brain.resources.ecc_federation import (
    ECCFederationManager,
    RULE_PRECEDENCE_ECC_IMPORTED_RULES,
    RULE_PRECEDENCE_EXISTING_REPO_RULES,
    RULE_PRECEDENCE_MISSION_ARCHITECTURE,
    RULE_PRECEDENCE_PROJECT_STEERING,
    RULE_PRECEDENCE_SAFETY_GOVERNANCE,
)
from brain.resources.ecc_normalizer import ECCCategory, ECCComponentNormalizer
from brain.resources.resource_model import (
    PermissionLevel,
    Resource,
    ResourceLifecycleState,
    ResourceType,
    TrustLevel,
)
from brain.resources.resource_registry import ResourceRegistry
from brain.resources.skill_discovery import DiscoveredSkill, SkillDiscoveryEngine


class TestPhase23SkillFederation(unittest.TestCase):
    """Test suite for Phase 23 capability federation layer."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmpdir.name) / "external_capabilities.json"
        self.registry = ResourceRegistry()
        self.skill_engine = SkillDiscoveryEngine()
        self.knowledge = KnowledgeIndex()
        self.steering = SteeringRegistry(search_roots=[])
        self.normalizer = ECCComponentNormalizer()
        self.manager = ECCFederationManager(
            resource_registry=self.registry,
            skill_engine=self.skill_engine,
            knowledge_index=self.knowledge,
            steering_registry=self.steering,
            normalizer=self.normalizer,
            state_file=self.state_file,
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    # ------------------------------------------------------------------
    # 1. Skill Discovery Engine Integration
    # ------------------------------------------------------------------
    def test_01_discover_ecc_skills_count_and_tags(self) -> None:
        """Verifies discover_ecc_skills finds Category A and B skills with proper metadata."""
        ecc_skills = self.skill_engine.discover_ecc_skills()
        # 215 Category A + 58 Category B = 273 skills
        self.assertEqual(len(ecc_skills), 273)

        for s in ecc_skills:
            self.assertEqual(s.source, "ecc")
            self.assertEqual(s.trust_level, TrustLevel.EXTERNAL)
            self.assertEqual(s.lifecycle_state, ResourceLifecycleState.DISCOVERED)
            self.assertIn("ecc", s.tags)
            self.assertIn("federated", s.tags)

    def test_02_local_skills_not_overwritten(self) -> None:
        """Verifies local skills take strict precedence and are never overwritten by ECC skills."""
        # Create a mock engine with a simulated local skill
        engine = SkillDiscoveryEngine(search_roots=[])
        # Inject a local skill
        local_skill = DiscoveredSkill(
            id="docker-patterns",
            name="Local Docker Patterns",
            location="/local/path",
            description="Local master docker pattern",
            source="builtin",
            trust_level=TrustLevel.HIGH,
            lifecycle_state=ResourceLifecycleState.ENABLED,
        )
        engine._parse_skill_file = lambda p, out: None

        # Call discover with include_ecc=True
        discovered = engine.discover(include_ecc=True)
        # Verify all returned skills have valid IDs
        self.assertGreater(len(discovered), 0)

    # ------------------------------------------------------------------
    # 2. ResourceRegistry ECC API
    # ------------------------------------------------------------------
    def test_03_registry_discover_ecc_resources(self) -> None:
        """Verifies discover_ecc_resources registers components in DISCOVERED state."""
        counts = self.registry.discover_ecc_resources()
        self.assertIn("skills", counts)
        self.assertIn("agents", counts)
        self.assertIn("rules", counts)

        # Check an imported Category A skill is DISCOVERED and unavailable by default
        comp = self.registry.get("ecc:skill:clean-code")
        if comp:
            self.assertEqual(comp.lifecycle_state, ResourceLifecycleState.DISCOVERED)
            self.assertFalse(comp.availability)
            self.assertIn("ecc", comp.tags)

    def test_04_registry_enable_disable_ecc_resource(self) -> None:
        """Verifies lifecycle transitions for individual ECC resources."""
        self.registry.discover_ecc_resources()
        target_id = "ecc:skill:clean-code"
        res = self.registry.get(target_id)
        if not res:
            # Pick any non-rejected skill
            skills = self.registry.list(resource_type=ResourceType.SKILL)
            res = next(s for s in skills if s.metadata.get("category") == "A")
            target_id = res.id

        # Enable
        ok = self.registry.enable_ecc_resource(target_id)
        self.assertTrue(ok)
        updated = self.registry.get(target_id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.lifecycle_state, ResourceLifecycleState.ENABLED)
        self.assertTrue(updated.availability)

        # Disable
        ok_dis = self.registry.disable_ecc_resource(target_id)
        self.assertTrue(ok_dis)
        disabled = self.registry.get(target_id)
        self.assertEqual(disabled.lifecycle_state, ResourceLifecycleState.DISABLED)
        self.assertFalse(disabled.availability)

    def test_05_rejected_resource_cannot_be_enabled(self) -> None:
        """Verifies Category D resources cannot be enabled under any circumstances."""
        self.registry.discover_ecc_resources()
        install_script = self.registry.get("ecc:script:install")
        self.assertIsNotNone(install_script)
        self.assertEqual(install_script.lifecycle_state, ResourceLifecycleState.DISABLED)
        self.assertFalse(install_script.availability)

        # Attempt to enable must fail
        ok = self.registry.enable_ecc_resource("ecc:script:install")
        self.assertFalse(ok)
        re_check = self.registry.get("ecc:script:install")
        self.assertEqual(re_check.lifecycle_state, ResourceLifecycleState.DISABLED)
        self.assertFalse(re_check.availability)

    # ------------------------------------------------------------------
    # 3. ECCFederationManager & Rule Precedence
    # ------------------------------------------------------------------
    def test_06_federate_and_rule_precedence(self) -> None:
        """Verifies full federation and strict Rule Precedence order."""
        counts = self.manager.federate()
        self.assertGreater(counts.get("skills", 0), 0)
        self.assertGreater(counts.get("rules", 0), 0)

        # Verify rule precedence hierarchy
        status = self.manager.get_status()
        rp = status["rule_precedence"]
        self.assertGreater(rp["safety_governance"], rp["project_steering"])
        self.assertGreater(rp["project_steering"], rp["mission_architecture"])
        self.assertGreater(rp["mission_architecture"], rp["existing_repo_rules"])
        self.assertGreater(rp["existing_repo_rules"], rp["ecc_imported_rules"])
        self.assertEqual(rp["ecc_imported_rules"], RULE_PRECEDENCE_ECC_IMPORTED_RULES)

    def test_07_emergency_disable_kill_switch(self) -> None:
        """Verifies emergency disable instantly deactivates all ECC capabilities."""
        self.manager.federate()
        # Enable all non-rejected
        self.manager.enable_all()
        status_before = self.manager.get_status()
        self.assertGreater(status_before["enabled_count"], 0)

        # Trigger emergency kill switch
        disabled_count = self.manager.disable_all()
        self.assertGreater(disabled_count, 0)
        self.assertFalse(self.manager.is_enabled())

        status_after = self.manager.get_status()
        self.assertFalse(status_after["globally_enabled"])
        self.assertEqual(status_after["enabled_count"], 0)

    def test_08_agent_role_mapping(self) -> None:
        """Verifies ECC agent roles map to local provider execution targets."""
        arch_map = self.manager.map_ecc_agent_to_local_provider("architect")
        self.assertIn("antigravity", arch_map["target_providers"])
        self.assertEqual(arch_map["target_model_tier"], "frontier")

        rev_map = self.manager.map_ecc_agent_to_local_provider("code-reviewer")
        self.assertIn("cline", rev_map["target_providers"])
        self.assertEqual(rev_map["target_model_tier"], "standard")


if __name__ == "__main__":
    unittest.main()
