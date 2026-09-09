"""Unit tests for Phase 17: Context Provenance, Redaction, and Agent-Specific Builders."""
from __future__ import annotations

import unittest
from brain.context.context_builder import ContextBuilder


class TestPhase17ProvenanceAndRedaction(unittest.TestCase):
    def setUp(self):
        self.builder = ContextBuilder()

    def test_provenance_tracking(self):
        ctx = self.builder.preview_context("Deploy microservices to AKS")
        # Ensure steering provenance
        for s in ctx.relevant_steering:
            self.assertIn("provenance", s)
            self.assertIn("source", s["provenance"])
            self.assertEqual(s["provenance"]["type"], "steering")

        # Ensure docs provenance
        for d in ctx.relevant_docs:
            self.assertIn("provenance", d)
            self.assertIn("source", d["provenance"])
            self.assertEqual(d["provenance"]["type"], "documentation")

    def test_agent_specific_context_builder(self):
        # Cline context
        cline_ctx = self.builder.build_agent_context(
            task="Refactor user model",
            agent_id="cline"
        )
        self.assertIn("cline", cline_ctx.routing_rationale.lower())

        # Kiro context
        kiro_ctx = self.builder.build_agent_context(
            task="Run kubectl get pods",
            agent_id="kiro"
        )
        self.assertIn("kiro", kiro_ctx.routing_rationale.lower())

        # Antigravity context
        antigravity_ctx = self.builder.build_agent_context(
            task="Design multi-agent orchestrator architecture",
            agent_id="antigravity"
        )
        self.assertIn("antigravity", antigravity_ctx.routing_rationale.lower())


if __name__ == "__main__":
    unittest.main()
