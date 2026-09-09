"""Integration tests for TaskContext synthesis and JobManager integration."""
import unittest

from brain.context.context_builder import ContextBuilder, TaskContext
from brain.orchestrator.job_manager import JobManager
from providers.registry.bootstrap import create_default_registry


class TestContextIntegration(unittest.TestCase):

    def setUp(self):
        self.prov_reg = create_default_registry()
        self.job_manager = JobManager(self.prov_reg)
        self.builder = ContextBuilder()

    def test_job_submission_attaches_task_context(self):
        job = self.job_manager.submit_job(
            task="Deploy application to Azure AKS cluster with terraform",
            provider="antigravity",
            account="antigravity-account-1",
            model="gemini-3.8-flash-medium"
        )
        self.assertIsNotNone(job.context)
        self.assertEqual(job.context["task"], "Deploy application to Azure AKS cluster with terraform")
        self.assertEqual(job.context["domain"], "DevOps")

        # Verify CLI tools inferred
        cli_names = [c["name"] for c in job.context.get("cli_tools", [])]
        self.assertIn("az", cli_names)
        self.assertIn("terraform", cli_names)

        # Verify MCP inferred
        mcp_ids = [m["id"] for m in job.context.get("relevant_mcps", [])]
        self.assertIn("azure", mcp_ids)


if __name__ == "__main__":
    unittest.main()
