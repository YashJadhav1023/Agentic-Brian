"""Integration tests for Automatic Relevance-Based Resource Selection."""
import unittest

from brain.context.context_builder import ContextBuilder


class TestAutoResourceSelection(unittest.TestCase):

    def setUp(self):
        self.builder = ContextBuilder()

    def test_azure_deployment_auto_selection(self):
        ctx = self.builder.build_context("Deploy the application to Azure.")

        # Inferred domain
        self.assertEqual(ctx.domain, "DevOps")

        # Inferred MCP servers
        mcp_ids = [m["id"] for m in ctx.relevant_mcps]
        self.assertIn("azure", mcp_ids)

        # Inferred CLI tools
        cli_names = [c["name"] for c in ctx.cli_tools]
        self.assertIn("az", cli_names)

        # Inferred documentation
        doc_titles = [d["title"].lower() for d in ctx.relevant_docs]
        self.assertTrue(any("pipeline" in t or "azure" in t for t in doc_titles))

        # Inferred steering
        self.assertGreater(len(ctx.relevant_steering), 0)

    def test_irrelevant_task_does_not_attach_azure(self):
        ctx = self.builder.build_context("Fix regex parsing in python test suite")
        mcp_ids = [m["id"] for m in ctx.relevant_mcps]
        self.assertNotIn("azure", mcp_ids)

        cli_names = [c["name"] for c in ctx.cli_tools]
        self.assertNotIn("az", cli_names)
        self.assertNotIn("terraform", cli_names)


if __name__ == "__main__":
    unittest.main()
