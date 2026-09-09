"""Unit tests for Knowledge Document Registry and Deny Lists."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from brain.knowledge.document_registry import DocumentRegistry


class TestKnowledgeRegistry(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.docs_dir = Path(self.temp_dir) / "docs"
        self.docs_dir.mkdir()

        # Valid document
        (self.docs_dir / "azure-deployment.md").write_text(
            "# Azure Deployment Runbook\n\nInstructions for deploying to AKS cluster and configuring ingress."
        )

        # Sensitive file that MUST be excluded by deny list
        (self.docs_dir / ".env.production").write_text("API_KEY=sk-test-secret-12345")
        (self.docs_dir / "credentials.json").write_text('{"token": "secret"}')
        (self.docs_dir / "id_rsa").write_text("PRIVATE KEY")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_document_indexing(self):
        reg = DocumentRegistry(knowledge_roots=[self.temp_dir])
        docs = reg.discover()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].title, "Azure Deployment Runbook")
        self.assertEqual(docs[0].doc_type, "deployment")

    def test_sensitive_files_excluded(self):
        reg = DocumentRegistry(knowledge_roots=[self.temp_dir])
        docs = reg.discover()
        paths = [d.path for d in docs]
        for p in paths:
            self.assertNotIn(".env", p)
            self.assertNotIn("credentials", p)
            self.assertNotIn("id_rsa", p)

    def test_document_search(self):
        reg = DocumentRegistry(knowledge_roots=[self.temp_dir])
        reg.discover()
        results = reg.search("AKS deployment")
        self.assertEqual(len(results), 1)
        top_doc, score = results[0]
        self.assertEqual(top_doc.title, "Azure Deployment Runbook")


if __name__ == "__main__":
    unittest.main()
