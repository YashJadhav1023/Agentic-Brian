"""Universal Capability Federation Layer — Phase 23 Milestone 3.

Coordinates federation of Everything Claude Code (ECC) capabilities into
Mission Control's unified ResourceRegistry, SkillDiscoveryEngine,
KnowledgeIndex, and SteeringRegistry without creating duplicate registries.

Enforces strict Rule Precedence:
1. Safety & Governance (Priority 100)
2. Project Steering (Priority 60)
3. Mission Control Architecture (Priority 50)
4. Existing Repo Rules (Priority 40)
5. ECC Imported Rules (Priority 15)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from brain.knowledge.knowledge_index import KnowledgeIndex
from brain.knowledge.steering_registry import (
    SCOPE_PRECEDENCE,
    SteeringDocument,
    SteeringRegistry,
    SteeringScope,
)
from brain.resources.ecc_normalizer import (
    ECCCategory,
    ECCComponentNormalizer,
    NormalizedECCComponent,
)
from brain.resources.resource_model import (
    PermissionLevel,
    Resource,
    ResourceLifecycleState,
    ResourceType,
    TrustLevel,
)
from brain.resources.resource_registry import ResourceRegistry
from brain.resources.skill_discovery import DiscoveredSkill, SkillDiscoveryEngine

logger = logging.getLogger("MissionControl.ECCFederation")

# Rule Precedence Constants
RULE_PRECEDENCE_SAFETY_GOVERNANCE = 100
RULE_PRECEDENCE_PROJECT_STEERING = 60
RULE_PRECEDENCE_MISSION_ARCHITECTURE = 50
RULE_PRECEDENCE_EXISTING_REPO_RULES = 40
RULE_PRECEDENCE_ECC_IMPORTED_RULES = 15


_GLOBAL_FEDERATION_MANAGER: Optional["ECCFederationManager"] = None


def get_federation_manager(**kwargs) -> "ECCFederationManager":
    """Retrieve or create the global ECCFederationManager singleton."""
    global _GLOBAL_FEDERATION_MANAGER
    if _GLOBAL_FEDERATION_MANAGER is None:
        _GLOBAL_FEDERATION_MANAGER = ECCFederationManager(**kwargs)
    elif kwargs:
        for k, v in kwargs.items():
            if v is not None and hasattr(_GLOBAL_FEDERATION_MANAGER, k):
                setattr(_GLOBAL_FEDERATION_MANAGER, k, v)
    return _GLOBAL_FEDERATION_MANAGER


class ECCFederationManager:
    """Coordinates federation and lifecycle of external ECC capabilities."""

    DEFAULT_STATE_FILE = Path("runtime/external_capabilities.json")

    def __init__(
        self,
        resource_registry: Optional[ResourceRegistry] = None,
        skill_engine: Optional[SkillDiscoveryEngine] = None,
        knowledge_index: Optional[KnowledgeIndex] = None,
        steering_registry: Optional[SteeringRegistry] = None,
        normalizer: Optional[ECCComponentNormalizer] = None,
        state_file: Optional[Path] = None,
    ) -> None:
        self._lock = threading.RLock()
        self.resource_registry = resource_registry or ResourceRegistry()
        self.skill_engine = skill_engine or SkillDiscoveryEngine()
        self.knowledge_index = knowledge_index
        self.steering_registry = steering_registry
        self.normalizer = normalizer or ECCComponentNormalizer()
        self.state_file = state_file or self.DEFAULT_STATE_FILE

        self._globally_enabled = True
        self._enabled_resources: Set[str] = set()
        self._disabled_resources: Set[str] = set()
        self._load_persisted_state()

        global _GLOBAL_FEDERATION_MANAGER
        _GLOBAL_FEDERATION_MANAGER = self

    def _load_persisted_state(self) -> None:
        """Load enabled/disabled state from disk if exists."""
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self._globally_enabled = data.get("globally_enabled", True)
                self._enabled_resources = set(data.get("enabled_resources", []))
                self._disabled_resources = set(data.get("disabled_resources", []))
        except Exception as e:
            logger.debug("Could not load persisted ECC federation state: %s", e)

    def _persist_state(self) -> None:
        """Persist federation state to disk safely."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "globally_enabled": self._globally_enabled,
                "enabled_resources": sorted(list(self._enabled_resources)),
                "disabled_resources": sorted(list(self._disabled_resources)),
            }
            self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("Could not persist ECC federation state: %s", e)

    # ------------------------------------------------------------------
    # Federation & Lifecycle Operations
    # ------------------------------------------------------------------
    def federate(self) -> Dict[str, int]:
        """Perform full federation across normalizer, registries, and knowledge."""
        with self._lock:
            all_comps = self.normalizer.discover_all()
            counts: Dict[str, int] = {}

            for comp_type, comp_list in all_comps.items():
                registered_count = 0
                for comp in comp_list:
                    res = comp.to_resource()

                    # Tagging and external provenance
                    if "ecc" not in res.tags:
                        res.tags.append("ecc")
                    res.metadata["external"] = True
                    res.metadata["provenance_hash"] = comp.provenance_hash

                    # Determine initial lifecycle state based on persistent state and category
                    if not self._globally_enabled:
                        res.lifecycle_state = ResourceLifecycleState.DISABLED
                        res.availability = False
                    elif comp.category == ECCCategory.D:
                        res.lifecycle_state = ResourceLifecycleState.DISABLED
                        res.availability = False
                        res.health = "rejected"
                    elif res.id in self._disabled_resources:
                        res.lifecycle_state = ResourceLifecycleState.DISABLED
                        res.availability = False
                    elif res.id in self._enabled_resources:
                        res.lifecycle_state = ResourceLifecycleState.ENABLED
                        res.availability = True
                        res.health = "healthy"
                    else:
                        # Unconfigured components default to DISCOVERED (safe, non-auto-activated)
                        res.lifecycle_state = ResourceLifecycleState.DISCOVERED
                        res.availability = False

                    self.resource_registry.register(res)
                    registered_count += 1

                counts[comp_type] = registered_count

            # Index Category A rules into SteeringRegistry with low precedence (15)
            if self.steering_registry:
                rules = all_comps.get("rules", [])
                for rule_comp in rules:
                    if rule_comp.category == ECCCategory.A:
                        sdoc = SteeringDocument(
                            id=rule_comp.id,
                            path=rule_comp.location,
                            scope=SteeringScope.GLOBAL,
                            project="ecc",
                            priority=RULE_PRECEDENCE_ECC_IMPORTED_RULES,
                            tags=["ecc", "rule", "imported"],
                            summary=rule_comp.description,
                            content_hash=rule_comp.provenance_hash,
                            enabled=self._globally_enabled,
                            trust_level="medium",
                            rules=[rule_comp.name],
                        )
                        self.steering_registry.register(sdoc)

            # Index engineering docs and patterns into KnowledgeIndex
            if self.knowledge_index:
                docs = all_comps.get("documentation", [])
                for doc_comp in docs:
                    if doc_comp.category in (ECCCategory.A, ECCCategory.B):
                        self.knowledge_index.index_document(
                            doc_id=doc_comp.id,
                            title=doc_comp.name,
                            path=doc_comp.location,
                            content=doc_comp.description,
                            doc_type="ecc_guidance",
                            summary=doc_comp.description,
                            metadata={"category": doc_comp.category.value, "origin": "ecc"},
                            provenance={"hash": doc_comp.provenance_hash, "source": "ecc"},
                        )

            return counts

    def is_enabled(self) -> bool:
        """Check if external ECC capabilities are globally active."""
        with self._lock:
            return self._globally_enabled

    def enable(self, resource_id: str) -> bool:
        """Enable a single federated capability."""
        with self._lock:
            res = self.resource_registry.get(resource_id)
            if not res:
                return False
            # Never enable Category D / rejected
            if res.trust_level == TrustLevel.UNTRUSTED or res.metadata.get("category") == "D" or res.health == "rejected":
                logger.warning("Rejected ECC resource %s cannot be enabled", resource_id)
                return False

            self.resource_registry.enable_ecc_resource(resource_id)
            self._enabled_resources.add(resource_id)
            self._disabled_resources.discard(resource_id)
            self._persist_state()
            return True

    def disable(self, resource_id: str) -> bool:
        """Disable a single federated capability."""
        with self._lock:
            res = self.resource_registry.get(resource_id)
            if not res:
                return False

            self.resource_registry.disable_ecc_resource(resource_id)
            self._disabled_resources.add(resource_id)
            self._enabled_resources.discard(resource_id)
            self._persist_state()
            return True

    def disable_all(self) -> int:
        """Emergency Kill Switch: instantly disable all external ECC capabilities."""
        with self._lock:
            self._globally_enabled = False
            disabled_count = 0
            for res in self.resource_registry.list():
                if "ecc" in res.tags or res.id.startswith("ecc:"):
                    res.lifecycle_state = ResourceLifecycleState.DISABLED
                    res.availability = False
                    disabled_count += 1

            self._persist_state()
            logger.warning("Emergency disable invoked: %d ECC capabilities disabled", disabled_count)
            return disabled_count

    def enable_all(self) -> int:
        """Enable all non-rejected (Category A & B) ECC capabilities."""
        with self._lock:
            self._globally_enabled = True
            enabled_count = 0
            for res in self.resource_registry.list():
                if "ecc" in res.tags or res.id.startswith("ecc:"):
                    if res.trust_level != TrustLevel.UNTRUSTED and res.metadata.get("category") != "D" and res.health != "rejected":
                        res.lifecycle_state = ResourceLifecycleState.ENABLED
                        res.availability = True
                        res.health = "healthy"
                        self._enabled_resources.add(res.id)
                        self._disabled_resources.discard(res.id)
                        enabled_count += 1

            self._persist_state()
            return enabled_count

    def list_federated(
        self,
        category: Optional[str] = None,
        state: Optional[ResourceLifecycleState | str] = None,
        comp_type: Optional[ResourceType | str] = None,
    ) -> List[Resource]:
        """List all federated ECC resources filtered by criteria."""
        with self._lock:
            if not self.resource_registry.list():
                self.federate()
            results: List[Resource] = []
            for res in self.resource_registry.list():
                if not ("ecc" in res.tags or res.id.startswith("ecc:")):
                    continue

                if category and res.metadata.get("category") != category.upper():
                    continue

                if state:
                    s_val = state.value if isinstance(state, ResourceLifecycleState) else str(state)
                    if (res.lifecycle_state.value if isinstance(res.lifecycle_state, ResourceLifecycleState) else str(res.lifecycle_state)) != s_val:
                        continue

                if comp_type:
                    t_val = comp_type.value if isinstance(comp_type, ResourceType) else str(comp_type)
                    if (res.type.value if isinstance(res.type, ResourceType) else str(res.type)) != t_val:
                        continue

                results.append(res)
            return results

    def get_status(self) -> Dict[str, Any]:
        """Aggregate health, counts, and status of ECC capabilities."""
        with self._lock:
            federated = self.list_federated()
            by_category: Dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
            by_state: Dict[str, int] = {}
            for r in federated:
                cat = r.metadata.get("category", "A")
                by_category[cat] = by_category.get(cat, 0) + 1
                st = r.lifecycle_state.value if isinstance(r.lifecycle_state, ResourceLifecycleState) else str(r.lifecycle_state)
                by_state[st] = by_state.get(st, 0) + 1

            return {
                "globally_enabled": self._globally_enabled,
                "total_federated": len(federated),
                "by_category": by_category,
                "by_state": by_state,
                "enabled_count": by_state.get("ENABLED", 0),
                "discovered_count": by_state.get("DISCOVERED", 0),
                "disabled_count": by_state.get("DISABLED", 0),
                "rule_precedence": {
                    "safety_governance": RULE_PRECEDENCE_SAFETY_GOVERNANCE,
                    "project_steering": RULE_PRECEDENCE_PROJECT_STEERING,
                    "mission_architecture": RULE_PRECEDENCE_MISSION_ARCHITECTURE,
                    "existing_repo_rules": RULE_PRECEDENCE_EXISTING_REPO_RULES,
                    "ecc_imported_rules": RULE_PRECEDENCE_ECC_IMPORTED_RULES,
                },
            }

    # ------------------------------------------------------------------
    # Agent Mapping
    # ------------------------------------------------------------------
    def map_ecc_agent_to_local_provider(self, ecc_agent_name: str) -> Dict[str, Any]:
        """Map an ECC agent role (e.g. architect, code-reviewer) to local execution targets."""
        role_map: Dict[str, Dict[str, Any]] = {
            "architect": {
                "target_providers": ["antigravity", "openai-compatible"],
                "target_model_tier": "frontier",
                "recommended_tools": ["filesystem", "memory"],
            },
            "code-reviewer": {
                "target_providers": ["cline", "antigravity"],
                "target_model_tier": "standard",
                "recommended_tools": ["git", "lint"],
            },
            "threat-modeler": {
                "target_providers": ["antigravity", "openai-compatible"],
                "target_model_tier": "frontier",
                "recommended_tools": ["security-audit"],
            },
            "silent-failure-hunter": {
                "target_providers": ["cline", "kiro-cli"],
                "target_model_tier": "standard",
                "recommended_tools": ["test", "debug"],
            },
        }
        stem = ecc_agent_name.lower().replace(".md", "").replace("ecc:agent:", "")
        return role_map.get(stem, {
            "target_providers": ["antigravity", "cline"],
            "target_model_tier": "standard",
            "recommended_tools": [],
        })
