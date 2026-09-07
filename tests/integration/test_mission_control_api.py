"""Mission Control API and UI display tests.

Starts the real dashboard HTTP server on an ephemeral loopback port and asserts
that Antigravity Account 2 is presented as a real agent with runtime state.
"""
import json
import threading
import unittest
import urllib.request
from http.server import HTTPServer

from ui.dashboard import dashboard


class TestMissionControlApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Port 0 lets the OS pick a free port; bind to loopback only.
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
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=15) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read().decode("utf-8"))

    def test_server_is_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_agents_endpoint_lists_account2_with_runtime_state(self):
        data = self._get("/api/agents")
        self.assertIn("antigravity", data)
        accounts = data["antigravity"]["accounts"]
        self.assertIn("antigravity-account-2", accounts)

        entry = accounts["antigravity-account-2"]
        for key in (
            "agent_id", "account_id", "provider", "execution_mode", "healthy",
            "health_reason", "profile", "display_status", "current_task",
            "session_id", "conversation_id", "last_activity", "counts",
            "recent_tasks", "recent_errors", "models", "capabilities",
        ):
            self.assertIn(key, entry, f"UI is missing field: {key}")

        self.assertEqual(entry["account_id"], "account-2")
        self.assertEqual(entry["execution_mode"], "headless")
        self.assertIn("antigravity-ide", entry["profile"])
        self.assertIn(
            entry["display_status"],
            {"ONLINE", "IDLE", "WORKING", "FAILED", "OFFLINE"},
        )

    def test_account_profiles_are_not_mixed_in_the_ui(self):
        accounts = self._get("/api/agents")["antigravity"]["accounts"]
        p1 = accounts["antigravity-account-1"]["profile"]
        p2 = accounts["antigravity-account-2"]["profile"]
        self.assertNotEqual(p1, p2)
        self.assertIn("antigravity-cli", p1)
        self.assertIn("antigravity-ide", p2)

    def test_status_endpoint_includes_agents_and_counters(self):
        data = self._get("/api/status")
        for key in ("tasks_count", "running_tasks", "completed_tasks", "failed_tasks", "agents"):
            self.assertIn(key, data)
        self.assertIn("antigravity-account-2", data["agents"]["antigravity"]["accounts"])

    def test_health_endpoint_reports_every_agent(self):
        data = self._get("/api/health")
        self.assertIn("antigravity-account-2", data)
        self.assertIn("health", data["antigravity-account-2"])
        self.assertFalse(
            data["antigravity-account-2"]["dangerously_skip_permissions"],
            "the UI must not advertise privilege escalation as the default",
        )

    def test_sessions_endpoint_exposes_the_mapping(self):
        data = self._get("/api/sessions")
        self.assertIn("sessions", data)
        for record in data["sessions"]:
            for key in ("task_id", "agent_id", "session_id", "conversation_id"):
                self.assertIn(key, record)

    def test_tasks_and_events_endpoints_respond(self):
        self.assertIn("tasks", self._get("/api/tasks"))
        self.assertIn("events", self._get("/api/events"))
        self.assertIn("memories", self._get("/api/memory"))
        self.assertIn("markdown", self._get("/api/handoff"))

    def test_no_secret_material_is_served(self):
        """Logs and API output must never carry tokens or key material."""
        blob = json.dumps(self._get("/api/status")).lower()
        for forbidden in ("api_key", "apikey", "bearer ", "authorization", "gemini_api_key", "password"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":
    unittest.main()
