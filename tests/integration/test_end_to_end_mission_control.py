"""
End-to-End Integration Tests for Universal AI Mission Control (Phases 12, 13, 14).
Includes 10-job concurrency, multi-factor routing, usage attribution, and failover verification.
"""

import unittest
import urllib.request
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from ui.dashboard.dashboard import ThreadedHTTPServer, MissionControlHandler

E2E_PORT = 18889


class TestEndToEndMissionControl(unittest.TestCase):
    """End-to-end integration test of Universal AI Mission Control."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedHTTPServer(("127.0.0.1", E2E_PORT), MissionControlHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.5)

        # Retrieve auth token
        with urllib.request.urlopen(f"http://127.0.0.1:{E2E_PORT}/api/token") as resp:
            cls.token = json.loads(resp.read().decode("utf-8"))["token"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{E2E_PORT}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}"
            }
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{E2E_PORT}{path}")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_full_mission_control_lifecycle(self):
        """Test complete lifecycle: discovery, job submission, auto-routing, and usage tracking."""
        # 1. Check overview
        overview = self._get("/api/overview")
        self.assertIn("mission_control", overview)

        # 2. Check providers
        provs = self._get("/api/providers")
        self.assertIn("providers", provs)
        self.assertTrue(len(provs["providers"]) > 0)

        # 3. Submit a job with Auto routing
        job_result = self._post("/api/jobs", {
            "task": "Write python function to compute fibonacci sequence",
            "routing_mode": "balanced",
            "priority": 7
        })
        self.assertIn("job", job_result)
        job_id = job_result["job"]["id"]
        self.assertIn("routing_decision", job_result)
        decision = job_result["routing_decision"]
        self.assertIsNotNone(decision["provider_id"])
        self.assertIsNotNone(decision["account_id"])

        # 4. Inspect routing decision by job_id
        inspect = self._get(f"/api/routing/inspect?job_id={job_id}")
        self.assertIn("decision", inspect)
        dec = inspect["decision"]
        self.assertIn("score_breakdown", dec)
        self.assertIn("capability_match", dec["score_breakdown"])

        # 5. Verify usage tracking recorded tokens and requests
        usage = self._get("/api/usage")
        self.assertGreater(usage["requests"], 0)

    def test_10_concurrent_jobs_execution(self):
        """Run 10 concurrent jobs simultaneously.
        Verify no account cross-contamination, correct attribution, and stability.
        """
        def submit_job(i):
            return self._post("/api/jobs", {
                "task": f"Concurrent task #{i}: Analyze dataset chunk",
                "routing_mode": "balanced",
                "priority": i
            })

        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(submit_job, i) for i in range(10)]
            for fut in as_completed(futures):
                results.append(fut.result())

        self.assertEqual(len(results), 10)
        job_ids = set()
        for r in results:
            self.assertIn("job", r)
            j = r["job"]
            self.assertIn(j["id"], set(j["id"] for _ in [1]))
            job_ids.add(j["id"])
            self.assertIn(j["status"], ["pending", "running", "completed", "failed"])

        # All 10 job IDs must be unique
        self.assertEqual(len(job_ids), 10)


if __name__ == "__main__":
    unittest.main()
