"""Integration tests for MCP discovery, tool catalog, and API endpoints."""
import json
import unittest

from providers.mcp.discovery import MCPDiscoveryEngine
from providers.mcp.tool_catalog import MCPToolCatalog, ToolSafetyLevel
from providers.registry.mcp_registry import get_mcp_registry


class TestMCPIntegration(unittest.TestCase):

    def setUp(self):
        self.engine = MCPDiscoveryEngine()
        self.catalog = MCPToolCatalog()
        self.registry = get_mcp_registry()

    def test_end_to_end_discovery_and_registration(self):
        discovered = self.engine.discover()
        self.assertGreater(len(discovered), 0)

        for disc in discovered:
            server = disc.to_mcp_server()
            self.catalog.populate_from_server(server.id)
            server.tools = self.catalog.list_tools(server_id=server.id)
            self.registry.register_server(server)

        # Confirm registry contains discovered servers
        servers = self.registry.list_servers()
        self.assertGreaterEqual(len(servers), len(discovered))

        # Check Azure MCP specifically if present
        azure_srv = self.registry.get_server("azure")
        if azure_srv:
            self.assertIn("azure", azure_srv.capabilities)
            self.assertGreater(len(azure_srv.tools), 0)
            # Confirm destructive tool was identified
            del_tool = [t for t in azure_srv.tools if t.name == "delete_resource"]
            if del_tool:
                self.assertEqual(del_tool[0].risk_level, ToolSafetyLevel.DESTRUCTIVE)


if __name__ == "__main__":
    unittest.main()
