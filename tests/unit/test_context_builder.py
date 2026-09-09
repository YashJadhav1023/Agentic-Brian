"""Unit tests for ContextBuilder and TaskContext synthesis."""
import unittest

from brain.context.context_builder import ContextBuilder, TaskContext


class TestContextBuilder(unittest.TestCase):

    def setUp(self):
        self.builder = ContextBuilder()

    def test_build_context_structure(self):
        ctx = self.builder.build_context("Deploy to Azure")
        self.assertIsInstance(ctx, TaskContext)
        self.assertEqual(ctx.task, "Deploy to Azure")
        self.assertTrue(hasattr(ctx, "relevant_mcps"))
        self.assertTrue(hasattr(ctx, "relevant_tools"))
        self.assertTrue(hasattr(ctx, "relevant_steering"))
        self.assertTrue(hasattr(ctx, "relevant_docs"))
        self.assertTrue(hasattr(ctx, "safety_constraints"))

    def test_preview_context_does_not_execute_tools(self):
        ctx = self.builder.preview_context("Remove cluster and delete all resources")
        # Ensure context was formed without executing destructive actions
        self.assertIsNotNone(ctx)
        self.assertIn("Never restart, kill, or modify running Antigravity GUI session", ctx.safety_constraints[0])

    def test_context_dictionary_serialization(self):
        ctx = self.builder.build_context("Test task")
        d = ctx.to_dict()
        self.assertEqual(d["task"], "Test task")
        self.assertIn("domain", d)
        self.assertIn("safety_constraints", d)


if __name__ == "__main__":
    unittest.main()
