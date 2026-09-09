"""Unit tests for MCP Tool Catalog and Safety Classification."""
import unittest

from providers.mcp.tool_catalog import MCPToolCatalog, ToolSafetyClassifier, ToolSafetyLevel
from providers.registry.mcp_registry import MCPTool


class TestMCPToolCatalog(unittest.TestCase):

    def setUp(self):
        self.catalog = MCPToolCatalog()

    def test_safety_classification_verbs(self):
        self.assertEqual(ToolSafetyClassifier.classify("delete_resource"), ToolSafetyLevel.DESTRUCTIVE)
        self.assertEqual(ToolSafetyClassifier.classify("purge_database"), ToolSafetyLevel.DESTRUCTIVE)
        self.assertEqual(ToolSafetyClassifier.classify("drop_table"), ToolSafetyLevel.DESTRUCTIVE)
        self.assertEqual(ToolSafetyClassifier.classify("deploy_template"), ToolSafetyLevel.HIGH_RISK_WRITE)
        self.assertEqual(ToolSafetyClassifier.classify("update_config"), ToolSafetyLevel.HIGH_RISK_WRITE)
        self.assertEqual(ToolSafetyClassifier.classify("format_code"), ToolSafetyLevel.LOW_RISK_WRITE)
        self.assertEqual(ToolSafetyClassifier.classify("lint_project"), ToolSafetyLevel.LOW_RISK_WRITE)
        self.assertEqual(ToolSafetyClassifier.classify("list_resources"), ToolSafetyLevel.READ_ONLY)
        self.assertEqual(ToolSafetyClassifier.classify("get_status"), ToolSafetyLevel.READ_ONLY)
        self.assertEqual(ToolSafetyClassifier.classify("random_unknown_verb"), ToolSafetyLevel.UNKNOWN)

    def test_is_safe_for_auto_execution(self):
        self.assertTrue(ToolSafetyClassifier.is_safe_for_auto_execution(ToolSafetyLevel.READ_ONLY))
        self.assertTrue(ToolSafetyClassifier.is_safe_for_auto_execution(ToolSafetyLevel.LOW_RISK_WRITE))
        self.assertFalse(ToolSafetyClassifier.is_safe_for_auto_execution(ToolSafetyLevel.HIGH_RISK_WRITE))
        self.assertFalse(ToolSafetyClassifier.is_safe_for_auto_execution(ToolSafetyLevel.DESTRUCTIVE))
        self.assertFalse(ToolSafetyClassifier.is_safe_for_auto_execution(ToolSafetyLevel.UNKNOWN))

    def test_populate_known_tools(self):
        self.catalog.populate_from_server("azure")
        tools = self.catalog.list_tools(server_id="azure")
        self.assertGreater(len(tools), 0)

        # Confirm delete_resource is classified destructive
        del_tool = self.catalog.get_tool("azure", "delete_resource")
        self.assertIsNotNone(del_tool)
        self.assertEqual(del_tool.risk_level, ToolSafetyLevel.DESTRUCTIVE)

    def test_tool_search(self):
        self.catalog.populate_from_server("azure")
        self.catalog.populate_from_server("firecrawl")

        results = self.catalog.search_tools("scrape website markdown")
        self.assertGreater(len(results), 0)
        top_tool, score = results[0]
        self.assertEqual(top_tool.server_id, "firecrawl")
        self.assertEqual(top_tool.name, "scrape")

    def test_filter_safe_tools(self):
        t1 = MCPTool(name="list_items", server_id="s1", risk_level=ToolSafetyLevel.READ_ONLY)
        t2 = MCPTool(name="apply_patch", server_id="s1", risk_level=ToolSafetyLevel.HIGH_RISK_WRITE)
        t3 = MCPTool(name="destroy_all", server_id="s1", risk_level=ToolSafetyLevel.DESTRUCTIVE)

        safe_read = self.catalog.filter_safe_tools([t1, t2, t3], allow_writes=False)
        self.assertEqual(safe_read, [t1])

        safe_write = self.catalog.filter_safe_tools([t1, t2, t3], allow_writes=True)
        self.assertIn(t1, safe_write)
        self.assertIn(t2, safe_write)
        self.assertNotIn(t3, safe_write)


if __name__ == "__main__":
    unittest.main()
