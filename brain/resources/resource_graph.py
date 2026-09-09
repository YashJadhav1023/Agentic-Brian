"""Universal Resource Graph for Mission Control.

Maintains a relational, queryable graph linking Agents, Providers, Accounts, Models,
MCP Servers, MCP Tools, Steering Documents, Knowledge Documents, CLI Commands, and Repositories.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class NodeType(str, Enum):
    AGENT = "agent"
    PROVIDER = "provider"
    ACCOUNT = "account"
    MODEL = "model"
    MCP_SERVER = "mcp_server"
    MCP_TOOL = "mcp_tool"
    STEERING = "steering"
    KNOWLEDGE = "knowledge"
    CLI_TOOL = "cli_tool"
    REPOSITORY = "repository"


@dataclass
class ResourceNode:
    """A node in the Universal Resource Graph."""
    id: str
    type: NodeType
    label: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, NodeType) else str(self.type),
            "label": self.label,
            "metadata": self.metadata,
            "tags": self.tags
        }


@dataclass
class ResourceEdge:
    """A directed edge expressing a relationship between two resource nodes."""
    source_id: str
    target_id: str
    relation: str  # e.g., "uses", "provides", "governs", "documents", "contains"
    weight: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UniversalResourceGraph:
    """Unified relational graph modeling all Mission Control resources."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._nodes: Dict[str, ResourceNode] = {}
        self._edges: List[ResourceEdge] = []
        self._adjacency: Dict[str, List[ResourceEdge]] = {}
        self._reverse_adjacency: Dict[str, List[ResourceEdge]] = {}

    def add_node(self, node: ResourceNode) -> None:
        """Add or update a node in the graph."""
        with self._lock:
            self._nodes[node.id] = node
            if node.id not in self._adjacency:
                self._adjacency[node.id] = []
            if node.id not in self._reverse_adjacency:
                self._reverse_adjacency[node.id] = []

    def add_edge(self, source_id: str, target_id: str, relation: str, weight: float = 1.0) -> None:
        """Add a directed relationship between two nodes."""
        with self._lock:
            edge = ResourceEdge(source_id=source_id, target_id=target_id, relation=relation, weight=weight)
            self._edges.append(edge)
            self._adjacency.setdefault(source_id, []).append(edge)
            self._reverse_adjacency.setdefault(target_id, []).append(edge)

    def get_node(self, node_id: str) -> Optional[ResourceNode]:
        """Get a node by ID."""
        with self._lock:
            return self._nodes.get(node_id)

    def list_nodes(self, node_type: Optional[NodeType | str] = None) -> List[ResourceNode]:
        """List nodes, optionally filtered by type."""
        with self._lock:
            nodes = list(self._nodes.values())
            if node_type:
                t_val = node_type.value if isinstance(node_type, NodeType) else str(node_type)
                nodes = [n for n in nodes if n.type == t_val or (isinstance(n.type, NodeType) and n.type.value == t_val)]
            return nodes

    def get_neighbors(self, node_id: str, direction: str = "out") -> List[Tuple[ResourceNode, str]]:
        """Get connected neighbor nodes with their relationship labels."""
        with self._lock:
            neighbors = []
            if direction in ("out", "both"):
                for edge in self._adjacency.get(node_id, []):
                    target = self._nodes.get(edge.target_id)
                    if target:
                        neighbors.append((target, edge.relation))
            if direction in ("in", "both"):
                for edge in self._reverse_adjacency.get(node_id, []):
                    source = self._nodes.get(edge.source_id)
                    if source:
                        neighbors.append((source, edge.relation))
            return neighbors

    def find_nodes_by_tag(self, tag: str) -> List[ResourceNode]:
        """Find all nodes possessing a specific tag."""
        t_clean = tag.lower().strip()
        with self._lock:
            return [n for n in self._nodes.values() if t_clean in [t.lower() for t in n.tags]]

    def to_graph_data(self) -> Dict[str, Any]:
        """Export nodes and edges in D3 / Cytoscape graph format."""
        with self._lock:
            return {
                "nodes": [n.to_dict() for n in self._nodes.values()],
                "edges": [e.to_dict() for e in self._edges],
                "node_count": len(self._nodes),
                "edge_count": len(self._edges)
            }


class ResourceGraphBuilder:
    """Populates UniversalResourceGraph from registries."""

    @staticmethod
    def build_graph(
        provider_registry: Any,
        mcp_registry: Any,
        steering_registry: Any,
        doc_registry: Any,
        cli_registry: Any,
        repo_registry: Any
    ) -> UniversalResourceGraph:
        """Construct full resource graph by linking entities across registries."""
        graph = UniversalResourceGraph()

        # 1. Repositories
        if repo_registry:
            for repo in repo_registry.list_repositories():
                r_id = f"repo:{repo.name}"
                graph.add_node(ResourceNode(
                    id=r_id,
                    type=NodeType.REPOSITORY,
                    label=repo.name,
                    metadata={"path": repo.path, "branch": repo.branch, "clean": repo.clean},
                    tags=["repo", repo.name.lower()]
                ))

        # 2. Providers, Accounts & Models
        if provider_registry:
            for prov in provider_registry.list_providers():
                p_id = f"provider:{prov.id}"
                graph.add_node(ResourceNode(
                    id=p_id,
                    type=NodeType.PROVIDER,
                    label=prov.name,
                    metadata={"category": getattr(prov, "category", "API"), "enabled": getattr(prov, "enabled", True)},
                    tags=["provider", prov.id.lower()]
                ))

            if hasattr(provider_registry, "account_registry"):
                for acc in provider_registry.account_registry.list_accounts():
                    a_id = f"account:{acc.id}"
                    graph.add_node(ResourceNode(
                        id=a_id,
                        type=NodeType.ACCOUNT,
                        label=acc.id,
                        metadata={"provider_id": acc.provider_id, "status": getattr(acc, "status", "healthy")},
                        tags=["account", acc.provider_id.lower()]
                    ))
                    # Edge: Provider -> Account
                    graph.add_edge(f"provider:{acc.provider_id}", a_id, "provides_account")

                    # Models
                    for m in getattr(acc, "models", []):
                        m_id = f"model:{acc.provider_id}:{m}"
                        graph.add_node(ResourceNode(
                            id=m_id,
                            type=NodeType.MODEL,
                            label=m,
                            metadata={"provider_id": acc.provider_id},
                            tags=["model", m.lower()]
                        ))
                        # Edge: Account -> Model
                        graph.add_edge(a_id, m_id, "supports_model")

        # 3. MCP Servers & Tools
        if mcp_registry:
            for s in mcp_registry.list_servers():
                s_id = f"mcp:{s.id}"
                graph.add_node(ResourceNode(
                    id=s_id,
                    type=NodeType.MCP_SERVER,
                    label=s.name,
                    metadata={"command": s.command, "source": s.source, "health": str(s.health)},
                    tags=["mcp", s.id.lower()] + s.tags
                ))
                for t in s.tools:
                    t_id = f"tool:{s.id}:{t.name}"
                    graph.add_node(ResourceNode(
                        id=t_id,
                        type=NodeType.MCP_TOOL,
                        label=f"{s.id}.{t.name}",
                        metadata={"risk": t.risk_level, "read_only": t.read_only, "desc": t.description},
                        tags=["mcp-tool", s.id.lower(), t.risk_level.lower()] + t.tags
                    ))
                    # Edge: Server -> Tool
                    graph.add_edge(s_id, t_id, "exposes_tool")

        # 4. CLI Tools
        if cli_registry:
            for cmd in cli_registry.list_commands():
                c_id = f"cli:{cmd.name}"
                graph.add_node(ResourceNode(
                    id=c_id,
                    type=NodeType.CLI_TOOL,
                    label=cmd.name,
                    metadata={"path": cmd.path, "version": cmd.version, "risk": cmd.risk},
                    tags=["cli", cmd.name.lower()] + cmd.capabilities
                ))

        # 5. Steering Documents
        if steering_registry:
            for sdoc in steering_registry.list_documents():
                st_id = f"steering:{sdoc.id}"
                graph.add_node(ResourceNode(
                    id=st_id,
                    type=NodeType.STEERING,
                    label=sdoc.summary[:40],
                    metadata={"scope": sdoc.scope.value, "priority": sdoc.priority, "project": sdoc.project},
                    tags=["steering", sdoc.scope.value.lower(), sdoc.project.lower()] + sdoc.tags
                ))
                # Link steering to repo if project matches
                if sdoc.project:
                    r_id = f"repo:{sdoc.project}"
                    graph.add_edge(r_id, st_id, "governed_by")

        # 6. Knowledge Documents
        if doc_registry:
            for kdoc in doc_registry.list_documents():
                k_id = f"doc:{kdoc.id}"
                graph.add_node(ResourceNode(
                    id=k_id,
                    type=NodeType.KNOWLEDGE,
                    label=kdoc.title,
                    metadata={"doc_type": kdoc.doc_type, "project": kdoc.project, "path": kdoc.path},
                    tags=["knowledge", kdoc.doc_type.lower(), kdoc.project.lower()] + kdoc.tags
                ))
                if kdoc.project:
                    r_id = f"repo:{kdoc.project}"
                    graph.add_edge(r_id, k_id, "documented_by")

        return graph
