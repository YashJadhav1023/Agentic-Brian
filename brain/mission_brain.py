"""Universal MissionBrain Facade — Mission Control Phases 16–20 Master Control Plane.

Unifies and coordinates:
- Universal Resource & Tool Intelligence (Phase 16)
- Unified Context & Knowledge Federation (Phase 17)
- Autonomous Task Planning & Execution Graphs (Phase 18)
- Dynamic Self-Optimization & Agent Execution Fabric (Phase 19)
- Production Mission Control Governance & Immutable Audit Logging (Phase 20)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from brain.analytics.cost_tracker import CostTracker, get_cost_tracker
from brain.analytics.performance_registry import PerformanceRegistry
from brain.analytics.quota_manager import QuotaManager, get_quota_manager
from brain.context.context_builder import ContextBuilder, TaskContext
from brain.context.context_registry import ContextRegistry
from brain.governance.audit_logger import AuditLogger
from brain.knowledge.document_registry import DocumentRegistry
from brain.knowledge.knowledge_index import KnowledgeIndex
from brain.knowledge.steering_registry import SteeringRegistry
from brain.orchestrator.execution_fabric import ExecutionFabric, ExecutionResult
from brain.planner.plan_model import Plan, PlanStep, StepStatus
from brain.planner.planner import Planner
from brain.planner.verification_engine import VerificationEngine
from brain.resources.cli_registry import CLIRegistry
from brain.resources.repo_registry import RepositoryRegistry
from brain.resources.resource_model import Resource, ResourceLifecycleState, ResourceType
from brain.resources.resource_registry import ResourceRegistry
from brain.resources.skill_discovery import SkillDiscoveryEngine
from brain.router.classification import TaskClassifier
from brain.router.models import RoutingDecision
from brain.router.smart_router import SmartRouter
from providers.mcp.discovery import MCPDiscoveryEngine
from providers.mcp.tool_catalog import MCPToolCatalog
from providers.registry.bootstrap import create_default_registry
from providers.registry.credential_manager import get_credential_manager
from providers.registry.mcp_registry import MCPRegistry
from providers.registry.provider_registry import ProviderRegistry

logger = logging.getLogger(__name__)


class MissionBrain:
    """Master controller coordinating all resource, planning, execution, and governance layers."""

    _instance: Optional["MissionBrain"] = None
    _singleton_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "MissionBrain":
        """Thread-safe singleton accessor."""
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(
        self,
        workspace_dir: Optional[Path] = None,
        provider_registry: Optional[ProviderRegistry] = None,
    ) -> None:
        self.workspace_dir = workspace_dir or Path.cwd()
        self._lock = threading.RLock()

        # 1. Foundation & Providers
        self.provider_registry = provider_registry or create_default_registry()
        self.cred_manager = get_credential_manager()
        self.cost_tracker = get_cost_tracker()
        self.quota_manager = get_quota_manager()

        # 2. Phase 20 Governance & Audit
        self.audit = AuditLogger(log_path=self.workspace_dir / "runtime" / "audit" / "audit.jsonl")

        # 3. Phase 16 Resources & Tool Intelligence
        self.mcp_registry = MCPRegistry()
        self.tool_catalog = MCPToolCatalog(self.mcp_registry)
        self.steering_registry = SteeringRegistry()
        self.document_registry = DocumentRegistry()
        self.cli_registry = CLIRegistry()
        self.repo_registry = RepositoryRegistry()
        self.skill_engine = SkillDiscoveryEngine()

        self.resource_registry = ResourceRegistry(
            mcp_registry=self.mcp_registry,
            tool_catalog=self.tool_catalog,
            steering_registry=self.steering_registry,
            document_registry=self.document_registry,
            cli_registry=self.cli_registry,
            repo_registry=self.repo_registry,
            skill_engine=self.skill_engine,
        )

        # 4. Phase 17 Context & Knowledge Federation
        self.context_registry = ContextRegistry()
        self.context_builder = ContextBuilder()
        self.knowledge_index = KnowledgeIndex()

        # 5. Phase 19 Analytics, Telemetry & Routing
        self.performance_registry = PerformanceRegistry(
            log_path=self.workspace_dir / "runtime" / "analytics" / "performance.jsonl"
        )
        self.smart_router = SmartRouter(
            self.provider_registry,
            performance_registry=self.performance_registry,
            cost_tracker=self.cost_tracker,
            quota_manager=self.quota_manager,
        )

        # 6. Phase 18 Planning
        self.planner = Planner(
            storage_dir=self.workspace_dir / "runtime" / "plans",
            router=self.smart_router,
        )

        # 7. Phase 19 Universal Execution Fabric
        self.fabric = ExecutionFabric(
            registry=self.provider_registry,
            smart_router=self.smart_router,
            context_builder=self.context_builder,
            performance_registry=self.performance_registry,
            cost_tracker=self.cost_tracker,
            quota_manager=self.quota_manager,
        )

        self.classifier = TaskClassifier()

        # 8. Phase 23 Universal ECC Capability Federation
        from brain.resources.ecc_federation import ECCFederationManager
        self.ecc_federation = ECCFederationManager(
            resource_registry=self.resource_registry,
            skill_engine=self.skill_engine,
            knowledge_index=self.knowledge_index,
            steering_registry=self.steering_registry,
            state_file=self.workspace_dir / "runtime" / "external_capabilities.json",
        )

    # ------------------------------------------------------------------
    # Discovery & Resources (Phase 16 & 23)
    # ------------------------------------------------------------------
    def discover_resources(self, force: bool = False) -> Dict[str, int]:
        """Perform comprehensive resource discovery across all subsystems."""
        counts = self.resource_registry.discover_all(force=force)
        self.audit.log(
            category="DISCOVERY",
            action="discover_all",
            details={"counts": counts},
        )
        return counts

    def get_resource(self, resource_id: str) -> Optional[Resource]:
        """Look up a specific resource in the catalog."""
        return self.resource_registry.get(resource_id)

    def list_resources(
        self,
        resource_type: Optional[ResourceType | str] = None,
        capability: Optional[str] = None,
        state: Optional[ResourceLifecycleState | str] = None,
    ) -> List[Resource]:
        """List resources filtered by criteria."""
        return self.resource_registry.list(
            resource_type=resource_type,
            capability=capability,
            state=state,
        )

    def search_resources(self, query: str, limit: int = 20) -> List[Tuple[Resource, float]]:
        """Rank resources matching a search query."""
        return self.resource_registry.search(query, limit=limit)

    def federate_ecc(self) -> Dict[str, int]:
        """Federate ECC capabilities into MissionBrain registries."""
        counts = self.ecc_federation.federate()
        self.audit.log(
            category="FEDERATION",
            action="federate_ecc",
            details={"counts": counts},
        )
        return counts

    def enable_ecc_capability(self, resource_id: str) -> bool:
        """Enable a single federated ECC capability."""
        ok = self.ecc_federation.enable(resource_id)
        self.audit.log(
            category="FEDERATION",
            action="enable_ecc_capability",
            target=resource_id,
            details={"success": ok},
        )
        return ok

    def disable_ecc_capability(self, resource_id: str) -> bool:
        """Disable a single federated ECC capability."""
        ok = self.ecc_federation.disable(resource_id)
        self.audit.log(
            category="FEDERATION",
            action="disable_ecc_capability",
            target=resource_id,
            details={"success": ok},
        )
        return ok

    def emergency_disable_ecc(self) -> int:
        """Emergency kill-switch: instantly disable all ECC capabilities."""
        count = self.ecc_federation.disable_all()
        self.audit.log(
            category="GOVERNANCE",
            action="emergency_disable_ecc",
            details={"disabled_count": count},
        )
        return count

    def enable_all_ecc(self) -> int:
        """Enable all safe Category A and B ECC capabilities."""
        count = self.ecc_federation.enable_all()
        self.audit.log(
            category="FEDERATION",
            action="enable_all_ecc",
            details={"enabled_count": count},
        )
        return count

    def get_ecc_status(self) -> Dict[str, Any]:
        """Return status and health of federated ECC capabilities."""
        return self.ecc_federation.get_status()

    # ------------------------------------------------------------------
    # Context & Knowledge (Phase 17)
    # ------------------------------------------------------------------
    def preview_context(self, task: str) -> TaskContext:
        """Preview synthesized task context with zero execution side-effects."""
        ctx = self.context_builder.preview_context(task)
        self.audit.log(
            category="ROUTING",
            action="preview_context",
            target=task[:80],
            details={"domain": ctx.domain},
        )
        return ctx

    def store_context(self, task: str) -> str:
        """Synthesize and persist an execution context bundle."""
        ctx = self.context_builder.build_context(task)
        bundle_id = self.context_registry.store(ctx)
        return bundle_id

    def search_knowledge(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """BM25 search over documentation and steering guidelines."""
        results = self.knowledge_index.search(query, limit=limit)
        if not results and self.document_registry.list_documents():
            for doc in self.document_registry.list_documents():
                content = ""
                try:
                    p = Path(doc.path)
                    if p.is_file():
                        content = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
                self.knowledge_index.index_document(
                    doc_id=doc.id,
                    title=doc.title,
                    path=doc.path,
                    doc_type=doc.doc_type,
                    summary=doc.summary,
                    content=content,
                )
            results = self.knowledge_index.search(query, limit=limit)
        return [r.to_dict() for r in results]

    # ------------------------------------------------------------------
    # Routing & Explainability (Phase 19)
    # ------------------------------------------------------------------
    def route_task(self, task: str, **kwargs: Any) -> RoutingDecision:
        """Route task to optimal agent, model, and provider."""
        decision = self.smart_router.route(task, **kwargs)
        self.audit.log(
            category="ROUTING",
            action="route_task",
            target=task[:80],
            details={
                "selected_agent": decision.agent_id,
                "model": decision.model,
                "score": decision.total_score,
            },
        )
        return decision

    def explain_routing(self, task: str, **kwargs: Any) -> Dict[str, Any]:
        """Generate full explainability report for task routing."""
        return self.smart_router.explain_routing(task, **kwargs)

    # ------------------------------------------------------------------
    # Planning & Graph Execution (Phase 18)
    # ------------------------------------------------------------------
    def create_plan(self, task: str, dry_run: bool = False) -> Plan:
        """Decompose a task into a dependency-linked Plan."""
        plan = self.planner.create_plan(task, dry_run=dry_run)
        self.audit.log(
            category="PLANNING",
            action="create_plan",
            target=plan.plan_id,
            details={
                "task": task[:80],
                "steps": len(plan.steps),
                "requires_approval": plan.requires_approval,
            },
        )
        return plan

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        """Retrieve a stored plan by ID."""
        return self.planner.get_plan(plan_id)

    def list_plans(self) -> List[Plan]:
        """List all known plans."""
        return self.planner.list_plans()

    def approve_plan(self, plan_id: str, approver: str = "user") -> bool:
        """Grant human approval for high-risk steps in a plan."""
        ok = self.planner.approve_plan(plan_id)
        if ok:
            self.audit.log(
                category="APPROVAL",
                action="approve_plan",
                actor=approver,
                target=plan_id,
                status="SUCCESS",
            )
        return ok

    def execute_plan(self, plan_id: str, auto_approve_safe: bool = True) -> Dict[str, Any]:
        """Execute a plan following its DAG dependencies."""
        res = self.planner.execute_plan(plan_id, auto_approve_safe=auto_approve_safe)
        self.audit.log(
            category="EXECUTION",
            action="execute_plan",
            target=plan_id,
            status=res.get("status", "UNKNOWN"),
            details=res,
        )
        return res

    # ------------------------------------------------------------------
    # Autonomous Execution Fabric (Phase 19)
    # ------------------------------------------------------------------
    def execute_task(
        self,
        task: str,
        preferred_agent: Optional[str] = None,
        dry_run: bool = False,
        requires_approval: bool = False,
        approved: bool = False,
        command: Optional[str] = None,
        rollback_action: Optional[str] = None,
        verification_rules: Optional[List[str]] = None,
    ) -> ExecutionResult:
        """End-to-end task execution through the universal execution fabric."""
        res = self.fabric.execute_task(
            task=task,
            preferred_agent=preferred_agent,
            dry_run=dry_run,
            requires_approval=requires_approval,
            approved=approved,
            command=command,
            rollback_action=rollback_action,
            verification_rules=verification_rules,
        )

        self.audit.log(
            category="EXECUTION",
            action="execute_task",
            target=task[:80],
            status=res.status,
            details={
                "execution_id": res.execution_id,
                "agent": res.agent_id,
                "model": res.model,
                "duration": res.latency_seconds,
                "cost": res.estimated_cost,
                "rollback": res.rollback_applied,
            },
        )
        return res

    # ------------------------------------------------------------------
    # Telemetry, Health & Governance (Phase 20)
    # ------------------------------------------------------------------
    def get_metrics(self) -> Dict[str, Any]:
        """Retrieve aggregated empirical performance telemetry."""
        return self.performance_registry.get_summary()

    def get_audit_trail(
        self,
        limit: int = 50,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve audit log entries."""
        return self.audit.get_events(limit=limit, category=category)

    def health(self, deep: bool = False) -> Dict[str, Any]:
        """Comprehensive cluster health diagnostics."""
        import shutil
        mcp_servers = self.mcp_registry.list_servers()
        if not mcp_servers:
            engine = MCPDiscoveryEngine()
            for disc in engine.discover():
                self.mcp_registry.register_server(disc.to_mcp_server())
            mcp_servers = self.mcp_registry.list_servers()

        mcp_health = {}
        for s in mcp_servers:
            cmd = s.command.split()[0] if s.command else ""
            found = bool(shutil.which(cmd) or Path(cmd).exists())
            status = "healthy" if found else "offline"
            mcp_health[s.id] = {
                "status": status,
                "command": s.command,
                "found": found
            }

        providers = {}
        for p in self.provider_registry.list_providers():
            providers[p.id] = p.health()

        active_adapters = len(self.provider_registry.list_active_adapters())
        mcp_total = len(mcp_servers)
        mcp_healthy = sum(1 for h in mcp_health.values() if h.get("status") == "healthy")

        overall_healthy = active_adapters > 0 and (mcp_healthy >= mcp_total * 0.5)

        return {
            "status": "HEALTHY" if overall_healthy else "DEGRADED",
            "active_adapters": active_adapters,
            "mcp_servers": {
                "total": mcp_total,
                "healthy": mcp_healthy,
                "details": mcp_health if deep else None,
            },
            "providers": providers,
            "performance": self.get_metrics(),
            "timestamp": time.time(),
        }
