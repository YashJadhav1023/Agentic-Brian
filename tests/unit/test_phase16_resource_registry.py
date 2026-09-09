"""Unit tests for Phase 16: Universal Resource Registry and Normalized Resource Model."""
from __future__ import annotations

import unittest
from brain.resources.resource_model import (
    PermissionLevel,
    Resource,
    ResourceLifecycleState,
    ResourceType,
    TrustLevel,
)
from brain.resources.resource_registry import ResourceRegistry


class TestPhase16ResourceModel(unittest.TestCase):
    def test_resource_creation_and_fingerprint(self):
        res = Resource(
            id="test:res1",
            type=ResourceType.MCP_TOOL,
            name="Test Tool",
            location="mcp://test/tool",
            capabilities=["test", "azure"],
            tags=["cloud"],
        )
        self.assertEqual(res.id, "test:res1")
        self.assertEqual(res.type, ResourceType.MCP_TOOL)
        self.assertTrue(len(res.fingerprint) > 0)
        d = res.to_dict()
        self.assertEqual(d["id"], "test:res1")
        self.assertEqual(d["type"], "mcp_tool")

        # Roundtrip deserialization
        res2 = Resource.from_dict(d)
        self.assertEqual(res2.id, res.id)
        self.assertEqual(res2.fingerprint, res.fingerprint)


class TestPhase16ResourceRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = ResourceRegistry()

    def test_manual_registration_and_lookup(self):
        res = Resource(
            id="cli:az",
            type=ResourceType.CLI,
            name="az",
            location="/usr/bin/az",
            capabilities=["azure", "cloud"],
            tags=["cli", "azure"],
        )
        self.registry.register(res)

        # Lookup by ID
        fetched = self.registry.get("cli:az")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "az")

        # Lookup by Type
        clis = self.registry.list(resource_type=ResourceType.CLI)
        self.assertEqual(len(clis), 1)
        self.assertEqual(clis[0].id, "cli:az")

        # Lookup by Capability
        azure_res = self.registry.find_by_capability("azure")
        self.assertEqual(len(azure_res), 1)

    def test_search_relevance(self):
        r1 = Resource(
            id="tool:azure.aks",
            type=ResourceType.MCP_TOOL,
            name="aks_get_credentials",
            location="mcp://azure/aks",
            description="Manage and get credentials for AKS Kubernetes clusters",
            capabilities=["azure", "kubernetes", "k8s"],
        )
        r2 = Resource(
            id="tool:render.deploy",
            type=ResourceType.MCP_TOOL,
            name="trigger_deploy",
            location="mcp://render/deploy",
            description="Trigger deployment on Render cloud",
            capabilities=["render", "deploy"],
        )
        self.registry.register(r1)
        self.registry.register(r2)

        results = self.registry.search("kubernetes cluster")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0][0].id, "tool:azure.aks")

    def test_lifecycle_state_transition(self):
        res = Resource(
            id="test:tool",
            type=ResourceType.TOOL,
            name="Sample",
            location="/tmp/sample",
            lifecycle_state=ResourceLifecycleState.DISCOVERED,
        )
        self.registry.register(res)
        self.assertEqual(self.registry.get("test:tool").lifecycle_state, ResourceLifecycleState.DISCOVERED)

        success = self.registry.set_lifecycle_state("test:tool", ResourceLifecycleState.ENABLED)
        self.assertTrue(success)
        self.assertEqual(self.registry.get("test:tool").lifecycle_state, ResourceLifecycleState.ENABLED)

    def test_discover_all_returns_populated_counts(self):
        counts = self.registry.discover_all(force=True)
        self.assertIn("mcp_server", counts)
        self.assertIn("cli", counts)
        self.assertIn("steering", counts)
        self.assertGreaterEqual(counts["mcp_server"], 5)
        self.assertGreaterEqual(counts["cli"], 5)
        self.assertGreaterEqual(len(self.registry.list()), 20)


if __name__ == "__main__":
    unittest.main()
