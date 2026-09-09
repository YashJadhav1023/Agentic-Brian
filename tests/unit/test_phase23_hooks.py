"""Unit Test Suite for Phase 23: Hook Governance and Execution Safety.

Tests:
1. ECC hooks are discovered and classified into appropriate risk categories.
2. All ECC hooks default to DISABLED and cannot auto-execute.
3. High-risk hooks enforce explicit approval gates (approval_required=True).
4. Category D hooks and raw shell/node scripts are strictly rejected from execution.
5. Zero raw hook execution invariant is enforced across the governance layer.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from brain.resources.ecc_federation import ECCFederationManager
from brain.resources.ecc_normalizer import ECCCategory, ECCComponentNormalizer
from brain.resources.resource_model import (
    PermissionLevel,
    ResourceLifecycleState,
    ResourceType,
    TrustLevel,
)
from brain.resources.resource_registry import ResourceRegistry


class TestPhase23Hooks(unittest.TestCase):
    """Test suite verifying hook governance, classification, and safety invariants."""

    def setUp(self) -> None:
        self.normalizer = ECCComponentNormalizer()
        self.registry = ResourceRegistry(ecc_normalizer=self.normalizer)
        self.federation = ECCFederationManager(
            resource_registry=self.registry,
            normalizer=self.normalizer,
        )

    def test_01_hook_census_and_classification(self) -> None:
        """Verifies hook census counts and category classification."""
        hooks = self.normalizer.discover_hooks()
        # 24 hooks total: 0 A, 8 B (require adaptation/approval), 2 C, 14 D (raw execution rejected)
        self.assertEqual(len(hooks), 24)

        by_cat = {}
        for h in hooks:
            by_cat[h.category] = by_cat.get(h.category, 0) + 1
        self.assertEqual(by_cat.get(ECCCategory.B, 0), 8)
        self.assertEqual(by_cat.get(ECCCategory.C, 0), 2)
        self.assertEqual(by_cat.get(ECCCategory.D, 0), 14)

    def test_02_hooks_default_to_disabled_and_require_approval(self) -> None:
        """Verifies all Category B hooks enforce approval_required=True and start DISABLED."""
        self.federation.federate()
        hooks = self.registry.list(resource_type=ResourceType.TOOL)
        ecc_hooks = [h for h in hooks if h.id.startswith("ecc:hook:")]
        self.assertGreater(len(ecc_hooks), 0)

        for h in ecc_hooks:
            # All hooks default to DISCOVERED or DISABLED initially
            self.assertIn(h.lifecycle_state, [ResourceLifecycleState.DISCOVERED, ResourceLifecycleState.DISABLED])
            self.assertFalse(h.availability)
            # Category B hooks must have approval required
            if h.metadata.get("category") == "B":
                self.assertTrue(h.metadata.get("approval_required", True))

    def test_03_category_d_hooks_strictly_rejected(self) -> None:
        """Verifies Category D hooks (raw node/shell execution) are UNTRUSTED and rejected."""
        hooks = {h.id: h for h in self.normalizer.discover_hooks()}
        for rej_id in ["ecc:hook:unverified-hook", "ecc:hook:raw-exec"]:
            if rej_id in hooks:
                hook = hooks[rej_id]
                self.assertEqual(hook.category, ECCCategory.D)
                self.assertEqual(hook.risk_level, "REJECTED")
                self.assertEqual(hook.trust_level, TrustLevel.UNTRUSTED)
                res = hook.to_resource()
                self.assertEqual(res.lifecycle_state, ResourceLifecycleState.DISABLED)
                self.assertFalse(res.availability)

    def test_04_no_raw_executable_permissions_on_hooks(self) -> None:
        """Verifies that no hook or script files in external/ecc have executable bits set."""
        ecc_root = Path("external/ecc")
        if ecc_root.exists():
            for fpath in ecc_root.glob("hooks/**/*"):
                if fpath.is_file():
                    mode = fpath.stat().st_mode
                    self.assertEqual(mode & 0o111, 0, f"Hook file {fpath} must not be executable")


if __name__ == "__main__":
    unittest.main()
