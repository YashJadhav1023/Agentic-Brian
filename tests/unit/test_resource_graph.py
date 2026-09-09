"""Unit tests for Universal Resource Graph."""
import unittest

from brain.resources.resource_graph import (
    NodeType,
    ResourceEdge,
    ResourceNode,
    UniversalResourceGraph,
)


class TestResourceGraph(unittest.TestCase):

    def setUp(self):
        self.graph = UniversalResourceGraph()

    def test_add_nodes_and_edges(self):
        n1 = ResourceNode(id="repo:test", type=NodeType.REPOSITORY, label="Test Repo")
        n2 = ResourceNode(id="doc:readme", type=NodeType.KNOWLEDGE, label="README")
        self.graph.add_node(n1)
        self.graph.add_node(n2)
        self.graph.add_edge("repo:test", "doc:readme", "contains_doc")

        data = self.graph.to_graph_data()
        self.assertEqual(data["node_count"], 2)
        self.assertEqual(data["edge_count"], 1)

    def test_get_neighbors(self):
        n1 = ResourceNode(id="server:azure", type=NodeType.MCP_SERVER, label="Azure")
        n2 = ResourceNode(id="tool:deploy", type=NodeType.MCP_TOOL, label="Deploy")
        self.graph.add_node(n1)
        self.graph.add_node(n2)
        self.graph.add_edge("server:azure", "tool:deploy", "exposes_tool")

        neighbors = self.graph.get_neighbors("server:azure", direction="out")
        self.assertEqual(len(neighbors), 1)
        target, rel = neighbors[0]
        self.assertEqual(target.id, "tool:deploy")
        self.assertEqual(rel, "exposes_tool")

    def test_find_nodes_by_tag(self):
        n1 = ResourceNode(id="m1", type=NodeType.MODEL, label="Gemini", tags=["gemini", "fast"])
        n2 = ResourceNode(id="m2", type=NodeType.MODEL, label="Claude", tags=["claude", "reasoning"])
        self.graph.add_node(n1)
        self.graph.add_node(n2)

        fast_nodes = self.graph.find_nodes_by_tag("fast")
        self.assertEqual(len(fast_nodes), 1)
        self.assertEqual(fast_nodes[0].id, "m1")


if __name__ == "__main__":
    unittest.main()
