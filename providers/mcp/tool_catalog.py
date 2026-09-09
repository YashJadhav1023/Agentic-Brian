"""MCP Tool Catalog and Safety Classification Engine for Mission Control.

Maintains a searchable catalog of MCP tools across registered servers and
enforces strict safety classifications to protect against unintended destructive operations.
"""
from __future__ import annotations

import json
from pathlib import Path

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from providers.registry.mcp_registry import MCPTool

logger = logging.getLogger(__name__)


class ToolSafetyLevel:
    """Standard safety levels for MCP and system tools."""
    READ_ONLY = "READ_ONLY"
    LOW_RISK_WRITE = "LOW_RISK_WRITE"
    HIGH_RISK_WRITE = "HIGH_RISK_WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    UNKNOWN = "UNKNOWN"

    ALL_LEVELS = {READ_ONLY, LOW_RISK_WRITE, HIGH_RISK_WRITE, DESTRUCTIVE, UNKNOWN}


# Deterministic verbs for classification
DESTRUCTIVE_VERBS = {
    "delete", "destroy", "drop", "truncate", "purge", "terminate", "kill",
    "remove", "rm", "erase", "prune", "wipe", "force", "format_disk"
}

HIGH_RISK_WRITE_VERBS = {
    "deploy", "update", "apply", "commit", "push", "patch", "modify",
    "create", "provision", "start", "stop", "restart", "reboot", "isolate",
    "install", "upgrade", "publish", "execute", "run_command", "upload",
    "write", "save", "set", "put", "post"
}

LOW_RISK_WRITE_VERBS = {
    "temp", "scratch", "format", "lint", "validate", "dry_run",
    "preview", "test", "build_preview", "stage", "annotate"
}

READ_ONLY_VERBS = {
    "get", "list", "read", "view", "inspect", "describe", "status", "query",
    "show", "fetch", "search", "check", "cat", "find", "scan", "audit",
    "metrics", "logs", "diff", "history", "help"
}


class ToolSafetyClassifier:
    """Classifies tool safety using deterministic action-verb analysis."""

    @staticmethod
    def classify(name: str, description: str = "") -> str:
        """Determine safety level for a tool based on its identifier and description."""
        combined = f"{name} {description}".lower().replace("_", " ").replace("-", " ")
        tokens = set(re.findall(r"\b[a-z]{2,}\b", combined))

        # Check DESTRUCTIVE first
        if tokens.intersection(DESTRUCTIVE_VERBS):
            return ToolSafetyLevel.DESTRUCTIVE

        # Check HIGH_RISK_WRITE
        if tokens.intersection(HIGH_RISK_WRITE_VERBS):
            return ToolSafetyLevel.HIGH_RISK_WRITE

        # Check LOW_RISK_WRITE
        if tokens.intersection(LOW_RISK_WRITE_VERBS):
            return ToolSafetyLevel.LOW_RISK_WRITE

        # Check READ_ONLY
        if tokens.intersection(READ_ONLY_VERBS):
            return ToolSafetyLevel.READ_ONLY

        # Default fallback is UNKNOWN
        return ToolSafetyLevel.UNKNOWN

    @staticmethod
    def is_safe_for_auto_execution(safety_level: str) -> bool:
        """Returns True if the tool can be safely executed autonomously."""
        return safety_level in (ToolSafetyLevel.READ_ONLY, ToolSafetyLevel.LOW_RISK_WRITE)


# Pre-defined known tool sets for standard discovered MCP servers
STANDARD_KNOWN_TOOLS: Dict[str, List[Dict[str, Any]]] = {
    "azure": [
        {"name": "list_resources", "desc": "List Azure resources in a subscription or resource group", "caps": ["azure", "read"]},
        {"name": "get_resource", "desc": "Get details of a specific Azure resource", "caps": ["azure", "read"]},
        {"name": "list_resource_groups", "desc": "List resource groups in Azure subscription", "caps": ["azure", "read"]},
        {"name": "deploy_template", "desc": "Deploy an ARM or Bicep template to Azure", "caps": ["azure", "deploy", "write"]},
        {"name": "delete_resource", "desc": "Delete an Azure resource or resource group", "caps": ["azure", "delete"]},
        {"name": "aks_get_credentials", "desc": "Fetch credentials for an AKS Kubernetes cluster", "caps": ["azure", "kubernetes", "read"]},
        {"name": "storage_list_blobs", "desc": "List blobs in an Azure storage container", "caps": ["azure", "storage", "read"]},
        {"name": "storage_upload_blob", "desc": "Upload a blob to Azure storage", "caps": ["azure", "storage", "write"]},
    ],
    "firecrawl": [
        {"name": "scrape", "desc": "Scrape a webpage and convert HTML to markdown or JSON", "caps": ["web", "scrape", "read"]},
        {"name": "crawl", "desc": "Crawl a website starting from a seed URL", "caps": ["web", "crawl", "read"]},
        {"name": "search", "desc": "Search the web and extract results", "caps": ["web", "search", "read"]},
    ],
    "render": [
        {"name": "list_services", "desc": "List web services and worker instances on Render", "caps": ["render", "cloud", "read"]},
        {"name": "get_service", "desc": "Inspect a Render web service status and config", "caps": ["render", "cloud", "read"]},
        {"name": "trigger_deploy", "desc": "Trigger a deployment for a Render service", "caps": ["render", "deploy", "write"]},
        {"name": "list_logs", "desc": "Fetch runtime execution logs for a Render service", "caps": ["render", "logs", "read"]},
    ],
    "cloudflare": [
        {"name": "list_zones", "desc": "List Cloudflare DNS zones", "caps": ["cloudflare", "dns", "read"]},
        {"name": "list_dns_records", "desc": "List DNS records for a zone", "caps": ["cloudflare", "dns", "read"]},
        {"name": "create_dns_record", "desc": "Create a new DNS record", "caps": ["cloudflare", "dns", "write"]},
        {"name": "delete_dns_record", "desc": "Delete a DNS record", "caps": ["cloudflare", "dns", "delete"]},
    ],
    "bitdefender": [
        {"name": "get_endpoints", "desc": "List protected endpoints and inventory", "caps": ["security", "audit", "read"]},
        {"name": "get_incidents", "desc": "Retrieve security alerts and threat incidents", "caps": ["security", "audit", "read"]},
        {"name": "create_scan_task", "desc": "Launch an on-demand malware scan on an endpoint", "caps": ["security", "scan", "write"]},
        {"name": "isolate_endpoint", "desc": "Isolate an endpoint from the network", "caps": ["security", "remediation", "write"]},
    ],
    "brain": [
        {"name": "read_note", "desc": "Read a note or document from basic memory", "caps": ["memory", "read"]},
        {"name": "search_notes", "desc": "Search memory notes by keyword or concept", "caps": ["memory", "search", "read"]},
        {"name": "write_note", "desc": "Store or update a note in memory", "caps": ["memory", "write"]},
    ]
}


class MCPToolCatalog:
    """Searchable catalog indexing tools across all registered MCP servers."""

    def __init__(self, mcp_registry: Optional[Any] = None) -> None:
        self.mcp_registry = mcp_registry
        self._tools: Dict[str, MCPTool] = {}  # key: f"{server_id}.{tool_name}"

    def sync_with_registry(self, registry: Optional[Any] = None) -> None:
        """Sync tools with registered MCP servers."""
        reg = registry or self.mcp_registry
        if reg:
            for server in reg.list_servers():
                self.populate_from_server(server.id)

    def register_tool(self, tool: MCPTool) -> None:
        """Register a tool and ensure its safety level is classified."""
        if not tool.risk_level or tool.risk_level == ToolSafetyLevel.UNKNOWN:
            tool.risk_level = ToolSafetyClassifier.classify(tool.name, tool.description)
        tool.read_only = (tool.risk_level == ToolSafetyLevel.READ_ONLY)
        key = f"{tool.server_id}.{tool.name}"
        self._tools[key] = tool

    def populate_from_server(self, server_id: str, tools: Optional[List[MCPTool]] = None) -> None:
        """Register tools from a server, filling in known schemas if available."""
        if tools:
            for t in tools:
                self.register_tool(t)
            return

        # Check if we have dynamic schema JSON files from host MCP definitions
        mcp_dir = Path.home() / ".gemini" / "antigravity-cli" / "mcp" / server_id.lower()
        if mcp_dir.is_dir():
            for schema_file in mcp_dir.glob("*.json"):
                try:
                    with open(schema_file, "r", encoding="utf-8", errors="ignore") as sf:
                        s_data = json.load(sf)
                    t_name = s_data.get("name", schema_file.stem)
                    t_desc = s_data.get("description", "")
                    t_params = s_data.get("parameters", {})
                    t_risk = ToolSafetyClassifier.classify(t_name, t_desc)
                    t_caps = [server_id.lower(), t_name.lower()]
                    t = MCPTool(
                        name=t_name,
                        server_id=server_id,
                        description=t_desc,
                        risk_level=t_risk,
                        capabilities=t_caps,
                        tags=[server_id, "schema_discovered"],
                        parameters=t_params,
                        read_only=(t_risk == ToolSafetyLevel.READ_ONLY)
                    )
                    self.register_tool(t)
                except Exception:
                    pass

        # Check if we have known standard tools for this server
        known = STANDARD_KNOWN_TOOLS.get(server_id.lower(), [])
        for k in known:
            risk = ToolSafetyClassifier.classify(k["name"], k["desc"])
            t = MCPTool(
                name=k["name"],
                server_id=server_id,
                description=k["desc"],
                risk_level=risk,
                capabilities=k.get("caps", []),
                tags=[server_id] + k.get("caps", []),
                read_only=(risk == ToolSafetyLevel.READ_ONLY)
            )
            self.register_tool(t)

    def get_tool(self, server_id: str, tool_name: str) -> Optional[MCPTool]:
        """Get a specific tool."""
        return self._tools.get(f"{server_id}.{tool_name}")

    def list_tools(self, server_id: Optional[str] = None, risk_level: Optional[str] = None) -> List[MCPTool]:
        """List all tools, optionally filtered by server_id and risk_level."""
        res = list(self._tools.values())
        if server_id:
            res = [t for t in res if t.server_id.lower() == server_id.lower()]
        if risk_level:
            res = [t for t in res if t.risk_level == risk_level]
        return res

    def search_tools(self, query: str, limit: int = 10) -> List[Tuple[MCPTool, float]]:
        """Search tools by relevance score to a query."""
        terms = set(re.findall(r"\b[a-z0-9_-]{2,}\b", query.lower()))
        if not terms:
            return [(t, 1.0) for t in list(self._tools.values())[:limit]]

        scored: List[Tuple[MCPTool, float]] = []
        for tool in self._tools.values():
            score = 0.0
            tool_name_terms = set(re.findall(r"\b[a-z0-9_-]{2,}\b", tool.name.lower()))
            tool_desc_terms = set(re.findall(r"\b[a-z0-9_-]{2,}\b", tool.description.lower()))
            caps = set(c.lower() for c in tool.capabilities)
            server_term = tool.server_id.lower()

            # Exact server match
            if server_term in terms:
                score += 5.0

            # Match tool name tokens
            name_matches = terms.intersection(tool_name_terms)
            score += len(name_matches) * 3.0

            # Match capability tokens
            cap_matches = terms.intersection(caps)
            score += len(cap_matches) * 2.5

            # Match description tokens
            desc_matches = terms.intersection(tool_desc_terms)
            score += len(desc_matches) * 1.0

            if score > 0.0:
                scored.append((tool, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def filter_safe_tools(self, tools: List[MCPTool], allow_writes: bool = False) -> List[MCPTool]:
        """Filter tools to ensure no destructive or unknown tools are included."""
        safe = []
        for t in tools:
            if t.risk_level == ToolSafetyLevel.READ_ONLY:
                safe.append(t)
            elif allow_writes and t.risk_level in (ToolSafetyLevel.LOW_RISK_WRITE, ToolSafetyLevel.HIGH_RISK_WRITE):
                safe.append(t)
            # DESTRUCTIVE and UNKNOWN are excluded from autonomous execution
        return safe
