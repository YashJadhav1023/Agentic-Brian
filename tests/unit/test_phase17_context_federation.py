"""Unit tests for Phase 17: Context Registry and Knowledge Federation."""
from __future__ import annotations

import unittest
from brain.context.context_builder import ContextBuilder, TaskContext
from brain.context.context_registry import ContextRegistry
from brain.knowledge.knowledge_index import KnowledgeIndex


class TestPhase17KnowledgeIndex(unittest.TestCase):
    def setUp(self):
        self.index = KnowledgeIndex()

    def test_indexing_and_bm25_search(self):
        self.index.index_document(
            doc_id="doc1",
            title="Azure Kubernetes Deployment Guide",
            path="/docs/azure_k8s.md",
            content="Use kubectl and Helm to deploy microservices to AKS cluster on Azure cloud.",
            doc_type="runbook"
        )
        self.index.index_document(
            doc_id="doc2",
            title="Postgres Database Migration",
            path="/docs/postgres.md",
            content="Run prisma migrate deploy to execute database schema updates.",
            doc_type="database"
        )

        results = self.index.search("AKS Kubernetes Azure", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, "doc1")
        self.assertIn("kubernetes", results[0].matched_terms)
        self.assertGreater(results[0].score, 0.0)

    def test_doc_type_filter(self):
        self.index.index_document("d1", "Title 1", "/p1", "some content", doc_type="runbook")
        self.index.index_document("d2", "Title 2", "/p2", "some content", doc_type="architecture")

        res_runbooks = self.index.search("content", doc_type_filter="runbook")
        self.assertEqual(len(res_runbooks), 1)
        self.assertEqual(res_runbooks[0].document_id, "d1")


class TestPhase17ContextRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = ContextRegistry()
        self.builder = ContextBuilder()

    def test_store_and_get_context(self):
        ctx = self.builder.preview_context("Deploy to Azure")
        cid = self.registry.store(ctx)
        self.assertTrue(cid.startswith("ctx-"))

        entry = self.registry.get(cid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.task, "Deploy to Azure")
        self.assertTrue(entry.redaction_verified)
        self.assertGreater(entry.token_estimate, 0)

    def test_explain_context(self):
        ctx = self.builder.preview_context("Fix kubernetes cluster memory leak")
        cid = self.registry.store(ctx)

        exp = self.registry.explain(cid)
        self.assertEqual(exp["context_id"], cid)
        self.assertIn("provenance", exp)
        self.assertIn("attached_resources", exp)
        self.assertTrue(exp["safety_and_governance"]["redaction_verified"])


if __name__ == "__main__":
    unittest.main()
