"""Unit tests for OpenHandsAdapter and OpenHandsProvider."""
import unittest
from unittest.mock import MagicMock, patch

from agents.base.adapter import ExecutionMode
from agents.openhands.adapter import OpenHandsAdapter, OpenHandsProvider
from brain.orchestrator.job import Job
from providers.base import ProviderType


class TestOpenHandsAdapter(unittest.TestCase):
    def test_openhands_adapter_properties(self):
        adapter = OpenHandsAdapter(
            agent_id="openhands-main",
            account_id="acct-main",
            server_url="http://localhost:3000/api",
        )
        self.assertEqual(adapter.agent_id, "openhands-main")
        self.assertEqual(adapter.account_id, "acct-main")
        self.assertEqual(adapter.provider_id, "openhands")
        self.assertEqual(adapter.execution_mode, ExecutionMode.HEADLESS)

    @patch("urllib.request.urlopen")
    def test_health_check_online(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        adapter = OpenHandsAdapter()
        healthy, reason = adapter.health()
        self.assertTrue(healthy)
        self.assertIn("ONLINE", reason)

    @patch("urllib.request.urlopen")
    def test_invoke_successful(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "completed", "output": "Task completed by OpenHands", "tokens": 150}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        adapter = OpenHandsAdapter()
        result = adapter.execute(task_id="t-1", prompt="Implement feature X")

        self.assertTrue(result.success)
        self.assertEqual(result.output, "Task completed by OpenHands")

    def test_openhands_provider_wrapper(self):
        adapter = OpenHandsAdapter()
        provider = OpenHandsProvider(adapter)
        self.assertEqual(provider.provider_id, "openhands")
        self.assertEqual(provider.provider_type, ProviderType.AGENT)
        self.assertEqual(len(provider.list_models()), 3)


if __name__ == "__main__":
    unittest.main()
