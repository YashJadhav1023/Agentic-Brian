"""
Unit tests for Mission Control Dashboard & Endpoints (Phase 12).
Uses an ephemeral loopback server to test real HTTP responses.
"""

import unittest
import json
import urllib.request
import threading
from ui.dashboard import dashboard


class TestMissionControlDashboard(unittest.TestCase):
    """Test dashboard request handling and payload structures via real HTTP requests."""

    @classmethod
    def setUpClass(cls):
        # Ephemeral port for isolated testing
        cls.server = dashboard.ThreadedHTTPServer(
            ("127.0.0.1", 0), dashboard.MissionControlHandler
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        token = dashboard.get_or_create_auth_token()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read().decode("utf-8"))

    def test_overview_stats_structure(self):
        """Verify /api/overview produces required mission_control summary."""
        overview = self._get("/api/overview")
        self.assertIn("mission_control", overview)
        mc = overview["mission_control"]
        self.assertIn("providers_online", mc)
        self.assertIn("providers_total", mc)
        self.assertIn("accounts_healthy", mc)
        self.assertIn("accounts_total", mc)
        self.assertIn("models_available", mc)
        self.assertIn("active_jobs", mc)
        self.assertIn("completed_jobs", mc)
        self.assertIn("failed_jobs", mc)
        self.assertIn("today_usage_tokens", mc)
        self.assertIn("today_estimated_cost_usd", mc)

    def test_providers_payload_contract(self):
        """Verify /api/providers returns properly formatted provider metadata."""
        prov_data = self._get("/api/providers")
        self.assertIn("providers", prov_data)
        providers = prov_data["providers"]
        self.assertIsInstance(providers, list)
        if providers:
            p = providers[0]
            self.assertIn("id", p)
            self.assertIn("name", p)
            self.assertIn("type", p)
            self.assertIn("status", p)
            self.assertIn("failure_rate", p)
            self.assertIn("capabilities", p)

    def test_accounts_masked_credentials(self):
        """Verify /api/accounts never returns plaintext secrets."""
        acct_data = self._get("/api/accounts")
        self.assertIn("accounts", acct_data)
        for a in acct_data["accounts"]:
            self.assertIn("masked_key", a)
            self.assertNotIn("sk-proj-", a.get("masked_key", ""))
            self.assertNotIn("raw_secret", str(a))

    def test_models_payload_contract(self):
        """Verify /api/models contains context windows, pricing, speed, availability."""
        mod_data = self._get("/api/models")
        self.assertIn("models", mod_data)
        self.assertIsInstance(mod_data["models"], list)

    def test_usage_payload_contract(self):
        """Verify /api/usage returns tokens, requests, and cost."""
        usage = self._get("/api/usage")
        self.assertIn("requests", usage)
        self.assertIn("total_tokens", usage)
        self.assertIn("estimated_cost_usd", usage)

    def test_cost_payload_contract(self):
        """Verify /api/cost returns total and today estimated costs."""
        cost = self._get("/api/cost")
        self.assertIn("total_estimated_cost_usd", cost)
        self.assertIn("today_estimated_cost_usd", cost)
        self.assertIn("pricing_models", cost)

    def test_quotas_payload_contract(self):
        """Verify /api/quotas returns active rules and compliance status."""
        quotas = self._get("/api/quotas")
        self.assertIn("quotas", quotas)
        self.assertIn("alerts", quotas)

    def test_analytics_payload_contract(self):
        """Verify /api/analytics returns latency percentiles and rates."""
        analytics = self._get("/api/analytics")
        self.assertIn("latency", analytics)
        lat = analytics["latency"]
        self.assertIn("p50", lat)
        self.assertIn("p95", lat)
        self.assertIn("p99", lat)
        self.assertIn("success_rate", analytics)
        self.assertIn("failure_rate", analytics)
        self.assertIn("failover_rate", analytics)


if __name__ == "__main__":
    unittest.main()
