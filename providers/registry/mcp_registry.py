"""Universal MCP Registry for Mission Control.

Provides thread-safe registration, querying, health tracking, and capability mapping
for Model Context Protocol (MCP) servers and tools.
Plaintext secrets are NEVER stored; all authentication uses CredentialManager references
(e.g., secret://mission-control/mcp/...).
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MCPTransport(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"
    WEBSOCKET = "websocket"
    CUSTOM = "custom"


class MCPHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class MCPTool:
    """Represents a tool exposed by an MCP server."""
    name: str
    server_id: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = "UNKNOWN"  # READ_ONLY, LOW_RISK_WRITE, HIGH_RISK_WRITE, DESTRUCTIVE, UNKNOWN
    tags: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    read_only: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCPResource:
    """Represents a resource uri/template exposed by an MCP server."""
    uri: str
    name: str
    server_id: str
    description: str = ""
    mime_type: str = "application/json"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCPPrompt:
    """Represents a pre-defined prompt template exposed by an MCP server."""
    name: str
    server_id: str
    description: str = ""
    arguments: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCPServer:
    """Metadata representing an MCP Server."""
    id: str
    name: str
    command: str = ""
    arguments: List[str] = field(default_factory=list)
    env_keys: List[str] = field(default_factory=list)  # Environment variable names required, NEVER values
    transport: MCPTransport = MCPTransport.STDIO
    enabled: bool = True
    health: MCPHealthStatus = MCPHealthStatus.UNKNOWN
    health_reason: str = ""
    source: str = "manual"  # e.g., cline, kiro, agentic_os, project, manual
    source_path: str = ""
    capabilities: List[str] = field(default_factory=list)
    tools: List[MCPTool] = field(default_factory=list)
    resources: List[MCPResource] = field(default_factory=list)
    prompts: List[MCPPrompt] = field(default_factory=list)
    authentication_reference: str = ""  # e.g., secret://mission-control/mcp/...
    working_directory: str = ""
    isolation_policy: str = "standard"
    tags: List[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["transport"] = self.transport.value if isinstance(self.transport, MCPTransport) else str(self.transport)
        d["health"] = self.health.value if isinstance(self.health, MCPHealthStatus) else str(self.health)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MCPServer:
        tools_data = data.get("tools", [])
        resources_data = data.get("resources", [])
        prompts_data = data.get("prompts", [])

        tools = [MCPTool(**t) if isinstance(t, dict) else t for t in tools_data]
        resources = [MCPResource(**r) if isinstance(r, dict) else r for r in resources_data]
        prompts = [MCPPrompt(**p) if isinstance(p, dict) else p for p in prompts_data]

        transport = data.get("transport", MCPTransport.STDIO)
        if isinstance(transport, str):
            try:
                transport = MCPTransport(transport)
            except ValueError:
                transport = MCPTransport.CUSTOM

        health = data.get("health", MCPHealthStatus.UNKNOWN)
        if isinstance(health, str):
            try:
                health = MCPHealthStatus(health)
            except ValueError:
                health = MCPHealthStatus.UNKNOWN

        clean_data = dict(data)
        clean_data["tools"] = tools
        clean_data["resources"] = resources
        clean_data["prompts"] = prompts
        clean_data["transport"] = transport
        clean_data["health"] = health

        return cls(**clean_data)


class MCPRegistry:
    """Thread-safe registry of all discovered and configured MCP servers."""

    def __init__(self, storage_file: Optional[Path | str] = None) -> None:
        self._lock = threading.RLock()
        self._servers: Dict[str, MCPServer] = {}
        self._storage_file = Path(storage_file) if storage_file else None
        if self._storage_file and self._storage_file.exists():
            self.load()

    def register_server(self, server: MCPServer) -> None:
        """Register or update an MCP server."""
        with self._lock:
            # Ensure tools back-reference this server
            for tool in server.tools:
                tool.server_id = server.id
            self._servers[server.id] = server
            logger.info("Registered MCP server %s (source: %s)", server.id, server.source)
            self._persist()

    def get_server(self, server_id: str) -> Optional[MCPServer]:
        """Retrieve an MCP server by ID."""
        with self._lock:
            return self._servers.get(server_id)

    def list_servers(self, enabled_only: bool = False, tag: Optional[str] = None) -> List[MCPServer]:
        """List registered servers with optional filtering."""
        with self._lock:
            servers = list(self._servers.values())
            if enabled_only:
                servers = [s for s in servers if s.enabled]
            if tag:
                servers = [s for s in servers if tag.lower() in [t.lower() for t in s.tags]]
            return servers

    def enable_server(self, server_id: str) -> bool:
        """Enable an MCP server."""
        with self._lock:
            server = self._servers.get(server_id)
            if not server:
                return False
            server.enabled = True
            self._persist()
            return True

    def disable_server(self, server_id: str) -> bool:
        """Disable an MCP server."""
        with self._lock:
            server = self._servers.get(server_id)
            if not server:
                return False
            server.enabled = False
            self._persist()
            return True

    def set_health(self, server_id: str, health: MCPHealthStatus | str, reason: str = "") -> bool:
        """Update health status of a server."""
        with self._lock:
            server = self._servers.get(server_id)
            if not server:
                return False
            if isinstance(health, str):
                try:
                    health = MCPHealthStatus(health)
                except ValueError:
                    health = MCPHealthStatus.UNKNOWN
            server.health = health
            server.health_reason = reason
            self._persist()
            return True

    def remove_server(self, server_id: str) -> bool:
        """Remove a server from the registry."""
        with self._lock:
            if server_id in self._servers:
                del self._servers[server_id]
                self._persist()
                return True
            return False

    def find_servers_by_capability(self, capability: str) -> List[MCPServer]:
        """Find servers supporting a given capability keyword."""
        cap_clean = capability.lower().strip()
        with self._lock:
            results = []
            for server in self._servers.values():
                if not server.enabled:
                    continue
                # check server capabilities, tags, and tools
                server_caps = [c.lower() for c in server.capabilities]
                server_tags = [t.lower() for t in server.tags]
                tool_caps = [c.lower() for t in server.tools for c in t.capabilities]
                tool_names = [t.name.lower() for t in server.tools]

                if (cap_clean in server_caps or
                    cap_clean in server_tags or
                    cap_clean in tool_caps or
                    any(cap_clean in name for name in tool_names) or
                    cap_clean in server.id.lower() or
                    cap_clean in server.name.lower()):
                    results.append(server)
            return results

    def get_tool(self, server_id: str, tool_name: str) -> Optional[MCPTool]:
        """Get a specific tool by server ID and tool name."""
        with self._lock:
            server = self._servers.get(server_id)
            if not server:
                return None
            for tool in server.tools:
                if tool.name == tool_name:
                    return tool
            return None

    def list_all_tools(self, enabled_only: bool = True) -> List[MCPTool]:
        """List all tools across registered servers."""
        with self._lock:
            tools = []
            for server in self._servers.values():
                if enabled_only and not server.enabled:
                    continue
                tools.extend(server.tools)
            return tools

    def clear(self) -> None:
        """Clear all registered servers."""
        with self._lock:
            self._servers.clear()
            self._persist()

    def _persist(self) -> None:
        """Save registry state to JSON if storage_file is configured."""
        if not self._storage_file:
            return
        try:
            self._storage_file.parent.mkdir(parents=True, exist_ok=True)
            from providers.registry.credential_manager import SecretRedactor
            redactor = SecretRedactor()
            data = {sid: redactor.redact_dict(s.to_dict()) for sid, s in self._servers.items()}
            temp_file = self._storage_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_file.replace(self._storage_file)
        except Exception as e:
            logger.error("Failed to persist MCPRegistry to %s: %s", self._storage_file, e)

    def load(self) -> None:
        """Load registry state from JSON."""
        if not self._storage_file or not self._storage_file.exists():
            return
        try:
            with open(self._storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._servers.clear()
                for sid, sdata in data.items():
                    self._servers[sid] = MCPServer.from_dict(sdata)
            logger.info("Loaded %d MCP servers from %s", len(self._servers), self._storage_file)
        except Exception as e:
            logger.error("Failed to load MCPRegistry from %s: %s", self._storage_file, e)


# Global singleton instance
_default_mcp_registry: Optional[MCPRegistry] = None
_registry_lock = threading.Lock()


def get_mcp_registry(storage_file: Optional[Path | str] = None) -> MCPRegistry:
    """Retrieve or create the global MCPRegistry instance."""
    global _default_mcp_registry
    with _registry_lock:
        if _default_mcp_registry is None:
            default_path = Path("runtime/mcp/registry.json")
            _default_mcp_registry = MCPRegistry(storage_file or default_path)
        return _default_mcp_registry
