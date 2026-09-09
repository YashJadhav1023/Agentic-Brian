"""Unified Resource Models for Mission Control Phase 16.

Defines the normalized representation for all discovered resources:
MCP Servers, MCP Tools, Steering Documents, Skills, Agents, Repositories,
Documentation, Providers, Models, CLI Utilities, and Workspaces.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ResourceType(str, Enum):
    """Normalized types for all Mission Control resources."""
    MCP_SERVER = "mcp_server"
    MCP_TOOL = "mcp_tool"
    TOOL = "tool"
    STEERING = "steering"
    SKILL = "skill"
    AGENT = "agent"
    REPOSITORY = "repository"
    DOCUMENTATION = "documentation"
    PROVIDER = "provider"
    MODEL = "model"
    CLI = "cli"
    WORKSPACE = "workspace"


class ResourceLifecycleState(str, Enum):
    """Lifecycle state of a resource in the cluster."""
    DISCOVERED = "DISCOVERED"
    REVIEWED = "REVIEWED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    DEPRECATED = "DEPRECATED"


class TrustLevel(str, Enum):
    """Trust hierarchy for resources."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    EXTERNAL = "EXTERNAL"
    UNTRUSTED = "UNTRUSTED"


class PermissionLevel(str, Enum):
    """Execution permission requirement."""
    READ_ONLY = "READ_ONLY"
    LOW_RISK_WRITE = "LOW_RISK_WRITE"
    HIGH_RISK_WRITE = "HIGH_RISK_WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    ADMIN = "ADMIN"


@dataclass
class Resource:
    """Normalized internal representation of any capability in Mission Control."""
    id: str
    type: ResourceType
    name: str
    location: str
    description: str = ""
    capabilities: List[str] = field(default_factory=list)
    source: str = "auto_discovery"
    trust_level: TrustLevel = TrustLevel.HIGH
    permission_level: PermissionLevel = PermissionLevel.READ_ONLY
    read_only: bool = True
    lifecycle_state: ResourceLifecycleState = ResourceLifecycleState.ENABLED
    availability: bool = True
    health: str = "healthy"
    last_seen: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()

    def compute_fingerprint(self) -> str:
        """Generate a deterministic fingerprint for change detection."""
        data = f"{self.id}:{self.type.value if isinstance(self.type, ResourceType) else self.type}:{self.location}:{self.name}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """Convert resource to JSON-serializable dictionary."""
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, ResourceType) else str(self.type),
            "name": self.name,
            "location": self.location,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "source": self.source,
            "trust_level": self.trust_level.value if isinstance(self.trust_level, TrustLevel) else str(self.trust_level),
            "permission_level": self.permission_level.value if isinstance(self.permission_level, PermissionLevel) else str(self.permission_level),
            "read_only": self.read_only,
            "lifecycle_state": self.lifecycle_state.value if isinstance(self.lifecycle_state, ResourceLifecycleState) else str(self.lifecycle_state),
            "availability": self.availability,
            "health": self.health,
            "last_seen": self.last_seen,
            "metadata": self.metadata,
            "tags": list(self.tags),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Resource:
        """Instantiate Resource from a dictionary."""
        return cls(
            id=data["id"],
            type=ResourceType(data["type"]),
            name=data["name"],
            location=data.get("location", ""),
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
            source=data.get("source", "auto_discovery"),
            trust_level=TrustLevel(data.get("trust_level", "HIGH")),
            permission_level=PermissionLevel(data.get("permission_level", "READ_ONLY")),
            read_only=data.get("read_only", True),
            lifecycle_state=ResourceLifecycleState(data.get("lifecycle_state", "ENABLED")),
            availability=data.get("availability", True),
            health=data.get("health", "healthy"),
            last_seen=data.get("last_seen", time.time()),
            metadata=data.get("metadata", {}),
            tags=data.get("tags", []),
            fingerprint=data.get("fingerprint", ""),
        )
