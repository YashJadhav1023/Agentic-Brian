"""Unit tests for ResourceSelector relevance ranking."""
import unittest

from brain.context.context_builder import ResourceSelector
from brain.knowledge.document_registry import DocumentMetadata, DocumentRegistry
from brain.knowledge.relevance import KnowledgeRelevanceEngine
from brain.knowledge.steering_registry import SteeringDocument, SteeringRegistry, SteeringScope
from brain.resources.cli_registry import CLICommand, CLIRegistry
from brain.resources.repo_registry import RepositoryMetadata, RepositoryRegistry
from brain.router.classification import TaskDomain
from providers.mcp.tool_catalog import MCPToolCatalog
from providers.registry.mcp_registry import MCPRegistry, MCPServer, MCPTool


class TestResourceSelector(unittest.TestCase):

    def setUp(self):
        self.mcp_reg = MCPRegistry()
        self.mcp_server = MCPServer(
            id="azure",
            name="Azure",
            capabilities=["azure", "cloud", "deployment"],
            tools=[
                MCPTool(name="deploy_template", server_id="azure", description="Deploy ARM template", risk_level="HIGH_RISK_WRITE"),
                MCPTool(name="list_resources", server_id="azure", description="List Azure resources", risk_level="READ_ONLY")
            ]
        )
        self.mcp_reg.register_server(self.mcp_server)

        self.catalog = MCPToolCatalog()
        self.catalog.populate_from_server("azure", self.mcp_server.tools)

        self.steer_reg = SteeringRegistry(search_roots=[])
        self.doc_reg = DocumentRegistry(knowledge_roots=[])
        self.cli_reg = CLIRegistry()
        self.repo_reg = RepositoryRegistry(search_roots=[])

        self.selector = ResourceSelector(
            mcp_registry=self.mcp_reg,
            tool_catalog=self.catalog,
            steering_registry=self.steer_reg,
            document_registry=self.doc_reg,
            cli_registry=self.cli_reg,
            repo_registry=self.repo_reg
        )

    def test_auto_select_azure_mcp_for_azure_task(self):
        res = self.selector.select_resources_for_task(
            task="Deploy application to Azure AKS cluster",
            domain=TaskDomain.DEVOPS
        )
        selected_mcp_ids = [m.id for m in res["mcps"]]
        self.assertIn("azure", selected_mcp_ids)

        tool_names = [t.name for t in res["tools"]]
        self.assertIn("deploy_template", tool_names)

    def test_irrelevant_task_does_not_select_azure(self):
        res = self.selector.select_resources_for_task(
            task="Write a python unit test for string reversal",
            domain=TaskDomain.CODING
        )
        selected_mcp_ids = [m.id for m in res["mcps"]]
        self.assertNotIn("azure", selected_mcp_ids)


if __name__ == "__main__":
    unittest.main()
