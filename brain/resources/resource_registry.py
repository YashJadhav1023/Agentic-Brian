"""Universal Resource Registry for Mission Control Phase 16.

Maintains a unified, capability-indexed catalog of all Mission Control resources:
MCP Servers, Tools, Steering Documents, Skills, Agents, Repositories, Documentation,
Providers, Models, CLI Utilities, and Workspaces.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from collections import defaultdict
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Set, Tuple

from brain.knowledge.document_registry import DocumentRegistry
from brain.knowledge.steering_registry import SteeringRegistry
from brain.resources.cli_registry import CLIRegistry
from brain.resources.repo_registry import RepositoryRegistry
from brain.resources.resource_model import (
    PermissionLevel,
    Resource,
    ResourceLifecycleState,
    ResourceType,
    TrustLevel,
)
from brain.resources.skill_discovery import SkillDiscoveryEngine
from providers.mcp.discovery import MCPDiscoveryEngine
from providers.mcp.tool_catalog import MCPToolCatalog, ToolSafetyLevel
from providers.registry.bootstrap import create_default_registry
from providers.registry.mcp_registry import MCPRegistry

logger = logging.getLogger(__name__)


class ResourceRegistry:
    """Universal registry coordinating all discovered resources and capabilities."""

    def __init__(
        self,
        mcp_registry: Optional[MCPRegistry] = None,
        tool_catalog: Optional[MCPToolCatalog] = None,
        steering_registry: Optional[SteeringRegistry] = None,
        document_registry: Optional[DocumentRegistry] = None,
        cli_registry: Optional[CLIRegistry] = None,
        repo_registry: Optional[RepositoryRegistry] = None,
        skill_engine: Optional[SkillDiscoveryEngine] = None,
        ecc_normalizer: Optional[Any] = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self._lock = threading.RLock()
        self.mcp_registry = mcp_registry or MCPRegistry()
        self.tool_catalog = tool_catalog or MCPToolCatalog(self.mcp_registry)
        self.steering_registry = steering_registry or SteeringRegistry()
        self.document_registry = document_registry or DocumentRegistry()
        self.cli_registry = cli_registry or CLIRegistry()
        self.repo_registry = repo_registry or RepositoryRegistry()
        self.skill_engine = skill_engine or SkillDiscoveryEngine()
        self.ecc_normalizer = ecc_normalizer
        self.cache_ttl = cache_ttl_seconds

        self._resources: Dict[str, Resource] = {}
        self._by_type: Dict[str, Set[str]] = defaultdict(set)
        self._by_capability: Dict[str, Set[str]] = defaultdict(set)
        self._by_tag: Dict[str, Set[str]] = defaultdict(set)
        self._last_discovery_time: float = 0.0

    def register(self, resource: Resource) -> None:
        """Register or update a resource in the registry."""
        with self._lock:
            self._resources[resource.id] = resource
            t_key = resource.type.value if isinstance(resource.type, ResourceType) else str(resource.type)
            self._by_type[t_key].add(resource.id)

            for cap in resource.capabilities:
                self._by_capability[cap.lower()].add(resource.id)

            for tag in resource.tags:
                self._by_tag[tag.lower()].add(resource.id)

    def get(self, resource_id: str) -> Optional[Resource]:
        """Retrieve a resource by ID."""
        with self._lock:
            return self._resources.get(resource_id)

    def list(
        self,
        resource_type: Optional[ResourceType | str] = None,
        capability: Optional[str] = None,
        state: Optional[ResourceLifecycleState | str] = None,
    ) -> List[Resource]:
        """List resources filtered by type, capability, and/or lifecycle state."""
        with self._lock:
            candidates = list(self._resources.values())

            if resource_type:
                t_val = resource_type.value if isinstance(resource_type, ResourceType) else str(resource_type)
                candidates = [r for r in candidates if (r.type.value if isinstance(r.type, ResourceType) else str(r.type)) == t_val]

            if capability:
                cap_lower = capability.lower()
                candidates = [r for r in candidates if any(c.lower() == cap_lower for c in r.capabilities)]

            if state:
                s_val = state.value if isinstance(state, ResourceLifecycleState) else str(state)
                candidates = [r for r in candidates if (r.lifecycle_state.value if isinstance(r.lifecycle_state, ResourceLifecycleState) else str(r.lifecycle_state)) == s_val]

            return candidates

    def find_by_capability(self, capability: str) -> List[Resource]:
        """Quickly look up all resources advertising a specific capability."""
        with self._lock:
            ids = self._by_capability.get(capability.lower(), set())
            return [self._resources[rid] for rid in ids if rid in self._resources]

    def search(self, query: str, limit: int = 20) -> List[Tuple[Resource, float]]:
        """Rank resources relevant to a query using BM25-style keyword matching."""
        tokens = set(re.findall(r"\b[a-zA-Z0-9_-]{2,}\b", query.lower()))
        if not tokens:
            return []

        scored: List[Tuple[Resource, float]] = []
        with self._lock:
            for res in self._resources.values():
                score = 0.0
                name_tokens = set(re.findall(r"\b[a-zA-Z0-9_-]{2,}\b", res.name.lower()))
                desc_tokens = set(re.findall(r"\b[a-zA-Z0-9_-]{2,}\b", res.description.lower()))
                cap_tokens = {c.lower() for c in res.capabilities}
                tag_tokens = {t.lower() for t in res.tags}

                # Weighting: name > capability > tags > description
                score += len(tokens.intersection(name_tokens)) * 5.0
                score += len(tokens.intersection(cap_tokens)) * 4.0
                score += len(tokens.intersection(tag_tokens)) * 2.0
                score += len(tokens.intersection(desc_tokens)) * 1.0

                if score > 0:
                    scored.append((res, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def set_lifecycle_state(self, resource_id: str, state: ResourceLifecycleState) -> bool:
        """Update lifecycle state for a resource."""
        with self._lock:
            res = self._resources.get(resource_id)
            if not res:
                return False
            res.lifecycle_state = state
            return True

    def discover_ecc_resources(self) -> Dict[str, int]:
        """Discover and register normalized ECC resources in DISCOVERED lifecycle state."""
        try:
            from brain.resources.ecc_normalizer import ECCCategory, ECCComponentNormalizer
            normalizer = self.ecc_normalizer or ECCComponentNormalizer()
            all_comps = normalizer.discover_all()
        except Exception as e:
            logger.debug("Failed discovering ECC resources: %s", e)
            return {}

        counts: Dict[str, int] = defaultdict(int)
        for comp_type, comp_list in all_comps.items():
            for comp in comp_list:
                res = comp.to_resource()
                # Category A, B, C start in DISCOVERED and availability=False until enabled
                if comp.category in (ECCCategory.A, ECCCategory.B, ECCCategory.C):
                    res.lifecycle_state = ResourceLifecycleState.DISCOVERED
                    res.availability = False
                else:  # Category D strictly disabled
                    res.lifecycle_state = ResourceLifecycleState.DISABLED
                    res.availability = False
                    res.health = "rejected"

                if "ecc" not in res.tags:
                    res.tags.append("ecc")
                res.metadata["external"] = True
                res.metadata["federated"] = True

                self.register(res)
                counts[comp_type] += 1

        return dict(counts)

    def enable_ecc_resource(self, resource_id: str) -> bool:
        """Enable a federated ECC resource if not strictly rejected."""
        with self._lock:
            res = self._resources.get(resource_id)
            if not res:
                return False
            if (
                res.trust_level == TrustLevel.UNTRUSTED
                or res.metadata.get("category") == "D"
                or res.health == "rejected"
            ):
                logger.warning("Refusing to enable rejected ECC resource %s", resource_id)
                return False
            res.lifecycle_state = ResourceLifecycleState.ENABLED
            res.availability = True
            res.health = "healthy"
            return True

    def disable_ecc_resource(self, resource_id: str) -> bool:
        """Disable a federated ECC resource."""
        with self._lock:
            res = self._resources.get(resource_id)
            if not res:
                return False
            res.lifecycle_state = ResourceLifecycleState.DISABLED
            res.availability = False
            return True

    def discover_all(self, force: bool = False) -> Dict[str, int]:
        """Execute full discovery across all subsystems and normalize into resources."""
        now = time.time()
        if not force and self._resources and (now - self._last_discovery_time) < self.cache_ttl:
            return {t: len(ids) for t, ids in self._by_type.items()}

        counts: Dict[str, int] = defaultdict(int)

        # 1. MCP Servers
        disc_engine = MCPDiscoveryEngine()
        discovered_mcps = disc_engine.discover()
        for dm in discovered_mcps:
            mcp_srv = dm.to_mcp_server()
            self.mcp_registry.register_server(mcp_srv)
            res = Resource(
                id=f"mcp:{mcp_srv.id}",
                type=ResourceType.MCP_SERVER,
                name=mcp_srv.name,
                location=mcp_srv.source_path or mcp_srv.command,
                description=mcp_srv.description,
                capabilities=list(mcp_srv.capabilities),
                source=mcp_srv.source,
                trust_level=TrustLevel.HIGH,
                permission_level=PermissionLevel.READ_ONLY,
                read_only=True,
                lifecycle_state=ResourceLifecycleState.ENABLED if mcp_srv.enabled else ResourceLifecycleState.DISABLED,
                availability=mcp_srv.health.value == "healthy",
                health=mcp_srv.health.value,
                tags=list(mcp_srv.tags),
                metadata={"command": mcp_srv.command, "arguments": mcp_srv.arguments, "transport": mcp_srv.transport.value}
            )
            self.register(res)
            counts["mcp_server"] += 1

        # 2. MCP Tools
        self.tool_catalog.sync_with_registry()
        for tool in self.tool_catalog.list_tools():
            # Classify permission
            p_level = PermissionLevel.READ_ONLY
            if tool.risk_level == ToolSafetyLevel.DESTRUCTIVE:
                p_level = PermissionLevel.DESTRUCTIVE
            elif tool.risk_level == ToolSafetyLevel.HIGH_RISK_WRITE:
                p_level = PermissionLevel.HIGH_RISK_WRITE
            elif tool.risk_level == ToolSafetyLevel.LOW_RISK_WRITE:
                p_level = PermissionLevel.LOW_RISK_WRITE

            res = Resource(
                id=f"tool:{tool.server_id}.{tool.name}",
                type=ResourceType.MCP_TOOL,
                name=tool.name,
                location=f"mcp://{tool.server_id}/{tool.name}",
                description=tool.description,
                capabilities=list(tool.capabilities),
                source=f"mcp:{tool.server_id}",
                trust_level=TrustLevel.HIGH,
                permission_level=p_level,
                read_only=(p_level == PermissionLevel.READ_ONLY),
                lifecycle_state=ResourceLifecycleState.ENABLED,
                availability=True,
                health="healthy",
                tags=["tool", f"mcp:{tool.server_id}", tool.risk_level.lower()],
                metadata={"server_id": tool.server_id, "safety_level": tool.risk_level, "parameters": getattr(tool, "input_schema", {})}
            )
            self.register(res)
            counts["mcp_tool"] += 1

        # 3. CLI Utilities
        for cli in self.cli_registry.discover():
            p_level = PermissionLevel.READ_ONLY
            if cli.risk == "HIGH_RISK_WRITE":
                p_level = PermissionLevel.HIGH_RISK_WRITE
            elif cli.risk == "LOW_RISK_WRITE":
                p_level = PermissionLevel.LOW_RISK_WRITE

            res = Resource(
                id=f"cli:{cli.name}",
                type=ResourceType.CLI,
                name=cli.name,
                location=cli.path or cli.name,
                description=f"Command-line utility: {cli.name}",
                capabilities=list(cli.capabilities),
                source="system_path",
                trust_level=TrustLevel.HIGH,
                permission_level=p_level,
                read_only=(p_level == PermissionLevel.READ_ONLY),
                lifecycle_state=ResourceLifecycleState.ENABLED,
                availability=cli.available,
                health="healthy" if cli.available else "unavailable",
                tags=["cli", cli.risk.lower()],
                metadata={"version_output": cli.version, "path": cli.path}
            )
            self.register(res)
            counts["cli"] += 1

        # 4. Steering Documents
        for sdoc in self.steering_registry.discover():
            res = Resource(
                id=f"steering:{sdoc.id}",
                type=ResourceType.STEERING,
                name=f"Steering: {Path(sdoc.path).name}",
                location=sdoc.path,
                description=sdoc.summary or f"Steering document with scope {sdoc.scope.value}",
                capabilities=["steering", "governance", sdoc.scope.value.lower()],
                source=sdoc.path,
                trust_level=TrustLevel.HIGH if sdoc.trust_level == "high" else TrustLevel.MEDIUM,
                permission_level=PermissionLevel.READ_ONLY,
                read_only=True,
                lifecycle_state=ResourceLifecycleState.ENABLED if sdoc.enabled else ResourceLifecycleState.DISABLED,
                availability=True,
                health="healthy",
                tags=["steering", sdoc.scope.value.lower()] + sdoc.tags,
                metadata={"scope": sdoc.scope.value, "priority": sdoc.priority, "rules_count": len(sdoc.rules)}
            )
            self.register(res)
            counts["steering"] += 1

        # 5. Skills
        for skill in self.skill_engine.discover():
            res = skill.to_resource()
            self.register(res)
            counts["skill"] += 1

        # 6. Documentation
        for doc in self.document_registry.discover():
            res = Resource(
                id=f"doc:{doc.id}",
                type=ResourceType.DOCUMENTATION,
                name=doc.title,
                location=doc.path,
                description=doc.summary or f"Documentation file ({doc.doc_type})",
                capabilities=["docs", doc.doc_type] + doc.tags,
                source=doc.path,
                trust_level=TrustLevel.HIGH,
                permission_level=PermissionLevel.READ_ONLY,
                read_only=True,
                lifecycle_state=ResourceLifecycleState.ENABLED,
                availability=True,
                health="healthy",
                tags=["docs", doc.doc_type] + doc.tags,
                metadata={"doc_type": doc.doc_type, "project": doc.project, "word_count": doc.word_count}
            )
            self.register(res)
            counts["documentation"] += 1

        # 7. Repositories
        for repo in self.repo_registry.discover():
            repo_tags = getattr(repo, "tags", []) or []
            res = Resource(
                id=f"repo:{repo.name}",
                type=ResourceType.REPOSITORY,
                name=repo.name,
                location=repo.path,
                description=f"Git repository: {repo.name}",
                capabilities=["repository", "codebase"] + repo_tags,
                source=repo.path,
                trust_level=TrustLevel.HIGH,
                permission_level=PermissionLevel.READ_ONLY,
                read_only=True,
                lifecycle_state=ResourceLifecycleState.ENABLED,
                availability=True,
                health="healthy",
                tags=["repository"] + repo_tags,
                metadata={"branch": repo.branch, "clean": repo.clean, "modified_count": repo.modified_count, "untracked_count": repo.untracked_count}
            )
            self.register(res)
            counts["repository"] += 1

        # 8. Agents & Providers
        prov_reg = create_default_registry()
        for prov in prov_reg.list_providers():
            res = Resource(
                id=f"provider:{prov.id}",
                type=ResourceType.PROVIDER,
                name=prov.name,
                location=prov.id,
                description=f"AI Provider: {prov.name}",
                capabilities=[prov.id, "provider"],
                source="config",
                trust_level=TrustLevel.HIGH,
                permission_level=PermissionLevel.READ_ONLY,
                read_only=True,
                lifecycle_state=ResourceLifecycleState.ENABLED if prov.enabled else ResourceLifecycleState.DISABLED,
                availability=prov.enabled,
                health="healthy" if prov.enabled else "disabled",
                tags=["provider", prov.id],
                metadata={"provider_id": prov.id, "adapter_count": len(prov.adapters)}
            )
            self.register(res)
            counts["provider"] += 1

            for adapter in prov.adapters.values():
                h_ok, h_msg = adapter.health()
                res_agent = Resource(
                    id=f"agent:{adapter.agent_id}",
                    type=ResourceType.AGENT,
                    name=adapter.agent_id,
                    location=adapter.account_id,
                    description=f"Execution Agent: {adapter.agent_id} ({adapter.account_id})",
                    capabilities=[c.value for c in adapter.capabilities()],
                    source=f"provider:{prov.id}",
                    trust_level=TrustLevel.HIGH,
                    permission_level=PermissionLevel.LOW_RISK_WRITE,
                    read_only=False,
                    lifecycle_state=ResourceLifecycleState.ENABLED,
                    availability=h_ok,
                    health="healthy" if h_ok else h_msg,
                    tags=["agent", adapter.execution_mode.value],
                    metadata={"execution_mode": adapter.execution_mode.value, "models": adapter.available_models()}
                )
                self.register(res_agent)
                counts["agent"] += 1

        self._last_discovery_time = now
        return dict(counts)

    def health(self) -> Dict[str, Any]:
        """Evaluate aggregate health of all discovered resources."""
        with self._lock:
            total = len(self._resources)
            healthy = sum(1 for r in self._resources.values() if r.availability and r.health == "healthy")
            by_type = {t: len(ids) for t, ids in self._by_type.items()}
            unhealthy_items = [
                {"id": r.id, "type": r.type.value if isinstance(r.type, ResourceType) else str(r.type), "health": r.health}
                for r in self._resources.values() if not r.availability or r.health != "healthy"
            ]
            return {
                "total_resources": total,
                "healthy_resources": healthy,
                "unhealthy_resources": total - healthy,
                "by_type": by_type,
                "issues": unhealthy_items[:10],
                "last_discovery": self._last_discovery_time,
            }
