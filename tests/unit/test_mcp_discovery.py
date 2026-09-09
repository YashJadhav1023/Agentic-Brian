"""Unit tests for MCP Discovery Engine."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from providers.mcp.discovery import DiscoveredMCPServer, MCPDiscoveryEngine


class TestMCPDiscovery(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "cline_mcp_settings.json"
        sample_config = {
            "mcpServers": {
                "mock-azure": {
                    "command": "npx",
                    "args": ["-y", "@azure/mcp@3.0.0"],
                    "env": {
                        "AZURE_AUTH": "some_env_value"
                    }
                },
                "mock-safe": {
                    "command": "python3",
                    "args": ["-m", "server"]
                }
            }
        }
        with open(self.config_path, "w") as f:
            json.dump(sample_config, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_discovery_parser(self):
        engine = MCPDiscoveryEngine(search_paths=[str(self.config_path)])
        discovered = engine.discover()
        self.assertEqual(len(discovered), 2)

        ids = [d.server_id for d in discovered]
        self.assertIn("mock-azure", ids)
        self.assertIn("mock-safe", ids)

    def test_env_keys_extracted_without_values(self):
        engine = MCPDiscoveryEngine(search_paths=[str(self.config_path)])
        discovered = engine.discover()
        azure_s = [d for d in discovered if d.server_id == "mock-azure"][0]
        self.assertIn("AZURE_AUTH", azure_s.env_keys)
        # Verify no secret values stored
        self.assertNotIn("some_env_value", azure_s.raw_config.get("env_keys", []))

    def test_conversion_to_mcp_server(self):
        engine = MCPDiscoveryEngine(search_paths=[str(self.config_path)])
        discovered = engine.discover()
        d = discovered[0]
        server = d.to_mcp_server()
        self.assertEqual(server.id, d.server_id)
        self.assertTrue(server.enabled)

    def test_graceful_handling_missing_file(self):
        engine = MCPDiscoveryEngine(search_paths=["/nonexistent/path/mcp.json"])
        discovered = engine.discover()
        self.assertEqual(len(discovered), 0)


if __name__ == "__main__":
    unittest.main()
