"""Unit tests for MCP Registry."""
import unittest
from pathlib import Path
import tempfile
import shutil

from providers.registry.mcp_registry import (
    MCPHealthStatus,
    MCPRegistry,
    MCPServer,
    MCPTool,
    MCPTransport,
)


class TestMCPRegistry(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage_file = Path(self.temp_dir) / "registry.json"
        self.registry = MCPRegistry(self.storage_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_register_and_get_server(self):
        server = MCPServer(
            id="azure",
            name="Azure MCP",
            command="npx",
            arguments=["-y", "@azure/mcp"],
            capabilities=["cloud", "azure", "deployment"],
            source="test"
        )
        self.registry.register_server(server)
        retrieved = self.registry.get_server("azure")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "Azure MCP")
        self.assertTrue(retrieved.enabled)

    def test_enable_disable_server(self):
        server = MCPServer(id="test-srv", name="Test")
        self.registry.register_server(server)
        self.assertTrue(self.registry.disable_server("test-srv"))
        self.assertFalse(self.registry.get_server("test-srv").enabled)
        self.assertTrue(self.registry.enable_server("test-srv"))
        self.assertTrue(self.registry.get_server("test-srv").enabled)

    def test_set_health(self):
        server = MCPServer(id="test-srv", name="Test")
        self.registry.register_server(server)
        self.registry.set_health("test-srv", MCPHealthStatus.HEALTHY, "All checks ok")
        retrieved = self.registry.get_server("test-srv")
        self.assertEqual(retrieved.health, MCPHealthStatus.HEALTHY)
        self.assertEqual(retrieved.health_reason, "All checks ok")

    def test_tools_associated_with_server(self):
        tool = MCPTool(name="list_resources", server_id="", description="List azure resources")
        server = MCPServer(id="azure", name="Azure", tools=[tool])
        self.registry.register_server(server)

        retrieved_tool = self.registry.get_tool("azure", "list_resources")
        self.assertIsNotNone(retrieved_tool)
        self.assertEqual(retrieved_tool.server_id, "azure")

    def test_find_servers_by_capability(self):
        s1 = MCPServer(id="azure", name="Azure", capabilities=["cloud", "azure"])
        s2 = MCPServer(id="firecrawl", name="Firecrawl", capabilities=["web", "scrape"])
        self.registry.register_server(s1)
        self.registry.register_server(s2)

        azure_servers = self.registry.find_servers_by_capability("azure")
        self.assertEqual(len(azure_servers), 1)
        self.assertEqual(azure_servers[0].id, "azure")

    def test_persistence(self):
        s1 = MCPServer(id="s1", name="Server 1", command="node")
        self.registry.register_server(s1)

        # New registry instance loading from same file
        reg2 = MCPRegistry(self.storage_file)
        self.assertIsNotNone(reg2.get_server("s1"))
        self.assertEqual(reg2.get_server("s1").command, "node")


if __name__ == "__main__":
    unittest.main()
