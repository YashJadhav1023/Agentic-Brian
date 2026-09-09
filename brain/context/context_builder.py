"""Task Context Builder and Automatic Resource Selector for Mission Control.

Automatically infers, scores, and attaches relevant MCP servers, tools, steering instructions,
knowledge documentation, and CLI utilities for any user task.
Ensures zero execution during context preview.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.knowledge.document_registry import DocumentRegistry
from brain.knowledge.relevance import KnowledgeRelevanceEngine, RelevantKnowledgeSet
from brain.knowledge.steering_registry import SteeringDocument, SteeringRegistry
from brain.resources.cli_registry import CLICommand, CLIRegistry
from brain.resources.repo_registry import RepositoryMetadata, RepositoryRegistry
from brain.router.classification import TaskClassifier, TaskDomain
from providers.mcp.discovery import MCPDiscoveryEngine
from providers.mcp.tool_catalog import MCPToolCatalog, ToolSafetyLevel
from providers.registry.bootstrap import create_default_registry
from providers.registry.credential_manager import get_credential_manager
from providers.registry.mcp_registry import MCPRegistry, MCPServer, MCPTool

logger = logging.getLogger(__name__)


@dataclass
class TaskContext:
    """Complete contextual execution bundle assembled by Brain for a task."""
    task: str
    domain: str
    selected_provider: str = "auto"
    selected_account: str = "auto"
    selected_model: str = "auto"
    routing_rationale: str = ""
    relevant_steering: List[Dict[str, Any]] = field(default_factory=list)
    relevant_docs: List[Dict[str, Any]] = field(default_factory=list)
    relevant_mcps: List[Dict[str, Any]] = field(default_factory=list)
    relevant_tools: List[Dict[str, Any]] = field(default_factory=list)
    cli_tools: List[Dict[str, Any]] = field(default_factory=list)
    repository: Dict[str, Any] = field(default_factory=dict)
    steering_conflicts: List[Dict[str, Any]] = field(default_factory=list)
    safety_constraints: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ResourceSelector:
    """Infers and selects relevant cluster resources for a task."""

    def __init__(
        self,
        mcp_registry: MCPRegistry,
        tool_catalog: MCPToolCatalog,
        steering_registry: SteeringRegistry,
        document_registry: DocumentRegistry,
        cli_registry: CLIRegistry,
        repo_registry: RepositoryRegistry,
        relevance_engine: Optional[KnowledgeRelevanceEngine] = None
    ) -> None:
        self.mcp_registry = mcp_registry
        self.tool_catalog = tool_catalog
        self.steering_registry = steering_registry
        self.document_registry = document_registry
        self.cli_registry = cli_registry
        self.repo_registry = repo_registry
        self.relevance_engine = relevance_engine or KnowledgeRelevanceEngine(min_score_threshold=2.0)

    def select_resources_for_task(
        self,
        task: str,
        domain: TaskDomain,
        target_project: str = ""
    ) -> Dict[str, Any]:
        """Perform multi-dimensional selection of relevant resources."""
        task_lower = task.lower()

        # 1. MCP Servers & Tools
        selected_mcps: List[MCPServer] = []
        # Find servers by capabilities or keyword
        for word in task_lower.split():
            if len(word) > 2:
                matched = self.mcp_registry.find_servers_by_capability(word)
                for m in matched:
                    if m not in selected_mcps and m.enabled:
                        selected_mcps.append(m)

        # Search ranked tools from tool catalog
        ranked_tools = self.tool_catalog.search_tools(task, limit=6)
        selected_tools = [t for t, score in ranked_tools if score >= 4.0]

        # Also ensure tools' servers are marked as selected MCPs
        for t in selected_tools:
            s = self.mcp_registry.get_server(t.server_id)
            if s and s not in selected_mcps and s.enabled:
                selected_mcps.append(s)

        # 2. CLI Tools
        relevant_cmds = self.cli_registry.find_relevant_commands(task)

        # 3. Steering Documents
        relevant_steering = self.steering_registry.find_relevant_steering(task, project=target_project)

        # 4. Knowledge Documents
        all_docs = self.document_registry.list_documents()
        relevant_knowledge = self.relevance_engine.rank_documents(
            task=task,
            documents=all_docs,
            target_project=target_project,
            task_domain=domain.value,
            limit=5
        )

        # 5. Active Repository
        active_repo = None
        repos = self.repo_registry.list_repositories()
        if target_project:
            active_repo = self.repo_registry.get_repo(target_project)
        if not active_repo and repos:
            # default to current repository
            for r in repos:
                if "agentic_shared_memory" in r.name.lower():
                    active_repo = r
                    break
            if not active_repo:
                active_repo = repos[0]

        return {
            "mcps": selected_mcps,
            "tools": selected_tools,
            "cli_tools": relevant_cmds,
            "steering": relevant_steering[:3],  # top 3 relevant steering docs
            "docs": relevant_knowledge.items,
            "repository": active_repo,
            "conflicts": self.steering_registry.get_conflicts()
        }


class ContextBuilder:
    """Assembles rich, safe execution contexts for agents."""

    def __init__(self, selector: Optional[ResourceSelector] = None) -> None:
        self.selector = selector or self._bootstrap_default_selector()

    @classmethod
    def _bootstrap_default_selector(cls) -> ResourceSelector:
        """Create and initialize all registries and selector."""
        # 1. MCP
        mcp_engine = MCPDiscoveryEngine()
        mcp_reg = MCPRegistry()
        catalog = MCPToolCatalog()
        for disc in mcp_engine.discover():
            server = disc.to_mcp_server()
            catalog.populate_from_server(server.id)
            server.tools = catalog.list_tools(server_id=server.id)
            mcp_reg.register_server(server)

        # 2. Steering
        steer_reg = SteeringRegistry()
        steer_reg.discover()

        # 3. Documents
        doc_reg = DocumentRegistry()
        doc_reg.discover()

        # 4. CLI
        cli_reg = CLIRegistry()
        cli_reg.discover()

        # 5. Repo
        repo_reg = RepositoryRegistry()
        repo_reg.discover()

        return ResourceSelector(
            mcp_registry=mcp_reg,
            tool_catalog=catalog,
            steering_registry=steer_reg,
            document_registry=doc_reg,
            cli_registry=cli_reg,
            repo_registry=repo_reg
        )

    def build_context(
        self,
        task: str,
        provider_id: str = "auto",
        account_id: str = "auto",
        model_id: str = "auto",
        routing_rationale: str = "",
        target_project: str = ""
    ) -> TaskContext:
        """Synthesize complete task context for agent dispatch."""
        # Classify task
        classifier = TaskClassifier()
        cls_result = classifier.classify(task)
        domain = cls_result.category

        # Infer relevant resources
        res = self.selector.select_resources_for_task(task, domain, target_project)

        cred_mgr = get_credential_manager()
        # Format steering with provenance
        steering_list = []
        for s in res["steering"]:
            steering_list.append({
                "id": s.id,
                "scope": s.scope.value,
                "project": s.project,
                "priority": s.priority,
                "summary": cred_mgr.redactor.redact_text(s.summary),
                "rules": [cred_mgr.redactor.redact_text(r) for r in s.rules[:8]],
                "path": s.path,
                "provenance": {
                    "source": s.path,
                    "type": "steering",
                    "scope": s.scope.value,
                    "hash": s.content_hash,
                }
            })

        # Format docs with provenance
        docs_list = []
        for rk in res["docs"]:
            docs_list.append({
                "id": rk.document.id,
                "title": cred_mgr.redactor.redact_text(rk.document.title),
                "doc_type": rk.document.doc_type,
                "project": rk.document.project,
                "path": rk.document.path,
                "score": round(rk.score, 2),
                "rationale": cred_mgr.redactor.redact_text(rk.rationale),
                "provenance": {
                    "source": rk.document.path,
                    "type": "documentation",
                    "doc_type": rk.document.doc_type,
                    "hash": rk.document.content_hash,
                }
            })

        # Format MCPs
        mcps_list = []
        for m in res["mcps"]:
            mcps_list.append({
                "id": m.id,
                "name": m.name,
                "command": m.command,
                "source": m.source,
                "capabilities": m.capabilities[:5]
            })

        # Format tools
        tools_list = []
        for t in res["tools"]:
            tools_list.append({
                "name": t.name,
                "server_id": t.server_id,
                "description": t.description,
                "risk_level": t.risk_level,
                "read_only": t.read_only
            })

        # Format CLI
        cli_list = []
        for c in res["cli_tools"]:
            cli_list.append({
                "name": c.name,
                "path": c.path,
                "version": c.version,
                "risk": c.risk
            })

        # Format repo
        repo_data = res["repository"].to_dict() if res["repository"] else {}

        # Conflicts
        conflicts_list = [c.to_dict() for c in res["conflicts"][:3]]

        # Safety constraints
        constraints = [
            "Never restart, kill, or modify running Antigravity GUI session (PID 4904).",
            "Do not modify frozen Phase 8 Antigravity account configurations.",
            "All secrets must use CredentialManager secret:// references; never print or leak API keys.",
            "Do not execute destructive operations without explicit confirmation."
        ]

        return TaskContext(
            task=task,
            domain=domain.value,
            selected_provider=provider_id,
            selected_account=account_id,
            selected_model=model_id,
            routing_rationale=routing_rationale,
            relevant_steering=steering_list,
            relevant_docs=docs_list,
            relevant_mcps=mcps_list,
            relevant_tools=tools_list,
            cli_tools=cli_list,
            repository=repo_data,
            steering_conflicts=conflicts_list,
            safety_constraints=constraints
        )

    def preview_context(self, task: str) -> TaskContext:
        """Safe read-only preview of synthesized context with ZERO tool execution."""
        return self.build_context(task)

    def build_agent_context(
        self,
        task: str,
        agent_id: str = "antigravity",
        model_id: str = "auto",
        provider_id: str = "auto",
        account_id: str = "auto",
        max_budget_chars: int = 8000
    ) -> TaskContext:
        """Build tailored, agent-specific context enforcing context budget and agent specialty."""
        ctx = self.build_context(
            task=task,
            provider_id=provider_id,
            account_id=account_id,
            model_id=model_id,
            routing_rationale=f"Tailored for agent {agent_id} ({model_id})"
        )

        # Apply agent-specific filtering
        if agent_id.lower() == "cline":
            # Cline focuses on local file editing; filter out cluster-wide deployment docs
            ctx.relevant_mcps = [m for m in ctx.relevant_mcps if m.get("id") in ("brain", "filesystem")]
            ctx.relevant_docs = [d for d in ctx.relevant_docs if d.get("doc_type") in ("general", "api_doc")]
        elif agent_id.lower() == "kiro":
            # Kiro focuses on CLI, terminal commands, and cloud scripts
            ctx.relevant_mcps = [m for m in ctx.relevant_mcps if m.get("id") in ("azure", "render", "cloudflare", "brain")]
        elif agent_id.lower() == "antigravity":
            # Antigravity is master architect; include architecture and governance
            pass

        return ctx
