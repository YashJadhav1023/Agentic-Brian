"""Unit tests for Steering Document Registry and Conflict Detection."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from brain.knowledge.steering_registry import (
    SCOPE_PRECEDENCE,
    SteeringDocument,
    SteeringRegistry,
    SteeringScope,
)


class TestSteeringRegistry(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Create mock directory hierarchy
        self.proj_dir = Path(self.temp_dir) / "Agentic_os"
        self.proj_dir.mkdir()
        self.agents_md = self.proj_dir / "AGENTS.md"
        self.agents_md.write_text(
            "# Agent Directives\n"
            "- You must always read shared memory before executing.\n"
            "- Never commit automatically without user confirmation.\n"
            "- Always use python standard library.\n"
        )

        self.sub_dir = self.proj_dir / ".github"
        self.sub_dir.mkdir()
        self.copilot_md = self.sub_dir / "copilot-instructions.md"
        self.copilot_md.write_text(
            "# Copilot Guidelines\n"
            "- Always write unit tests.\n"
            "- Do not commit automatically.\n"
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_discovery_and_scopes(self):
        reg = SteeringRegistry(search_roots=[self.temp_dir])
        docs = reg.discover()
        self.assertEqual(len(docs), 2)

        agents_doc = [d for d in docs if "agents.md" in d.path.lower()][0]
        self.assertGreater(len(agents_doc.rules), 0)
        self.assertIn("always", agents_doc.rules[0].lower())

    def test_scope_precedence(self):
        self.assertGreater(SCOPE_PRECEDENCE[SteeringScope.TASK], SCOPE_PRECEDENCE[SteeringScope.DIRECTORY])
        self.assertGreater(SCOPE_PRECEDENCE[SteeringScope.DIRECTORY], SCOPE_PRECEDENCE[SteeringScope.REPOSITORY])
        self.assertGreater(SCOPE_PRECEDENCE[SteeringScope.REPOSITORY], SCOPE_PRECEDENCE[SteeringScope.PROJECT])
        self.assertGreater(SCOPE_PRECEDENCE[SteeringScope.PROJECT], SCOPE_PRECEDENCE[SteeringScope.GLOBAL])

    def test_relevant_steering_search(self):
        reg = SteeringRegistry(search_roots=[self.temp_dir])
        reg.discover()
        relevant = reg.find_relevant_steering("read shared memory and execute")
        self.assertGreater(len(relevant), 0)


if __name__ == "__main__":
    unittest.main()
