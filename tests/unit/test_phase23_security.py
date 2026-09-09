"""Phase 23 Milestone 1 Security & Hardening Unit Test Suite.

Verifies:
1. All sensitive GET endpoints return 401 Unauthorized without auth.
2. Public allow-list endpoints return 200 without auth.
3. Dual-mode SSE authentication on /api/events/stream (Header & Query parameters).
4. Local loopback network boundary enforcement (rejects 0.0.0.0 and external interfaces).
5. PID file acquisition, stale PID cleanup, and Antigravity IDE GUI PID 3809 non-interference.
6. Graceful shutdown signal handler configuration.
7. Local log rotator size-based rotation, backup cycling, and threshold compliance.
8. Secret redaction in API responses.
9. Startup health check validations.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from brain.analytics.log_rotator import LogRotator
from ui.dashboard import dashboard
from ui.dashboard.dashboard import (
    ALLOWED_LOOPBACK_HOSTS,
    PUBLIC_GET_PATHS,
    SecurityError,
    ThreadedHTTPServer,
    acquire_pid_file,
    is_public_path,
    release_pid_file,
    run_startup_checks,
    setup_signal_handlers,
    validate_host_binding,
)


class TestDashboardSecurityAndHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedHTTPServer(
            ("127.0.0.1", 0), dashboard.MissionControlHandler
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.token = dashboard.get_or_create_auth_token()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _request(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict, dict | str]:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                body_raw = resp.read().decode("utf-8")
                try:
                    body = json.loads(body_raw)
                except Exception:
                    body = body_raw
                return status, resp_headers, body
        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_headers = dict(exc.headers)
            body_raw = exc.read().decode("utf-8")
            try:
                body = json.loads(body_raw)
            except Exception:
                body = body_raw
            return status, resp_headers, body

    # ── 1. Sensitive GET Endpoints (Must Return 401 Unauthenticated) ──────────

    def test_sensitive_get_overview_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/overview")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")
        self.assertIn("WWW-Authenticate", headers)

    def test_sensitive_get_accounts_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/accounts")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_providers_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/providers")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_usage_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/usage")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_cost_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/cost")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_router_history_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/router/history")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_steering_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/steering")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_events_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/events")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_quotas_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/quotas")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_wizard_providers_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/wizard/providers")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_models_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/models")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sensitive_get_authenticated_with_bearer_succeeds(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        status, _, body = self._request("/api/overview", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("mission_control", body)

    # ── 2. Public Allow-List Endpoints (Must Return 200 Unauthenticated) ─────

    def test_public_root_unauthenticated_returns_200(self):
        status, _, body = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn("Mission Control", str(body))

    def test_public_health_unauthenticated_returns_200(self):
        status, _, body = self._request("/api/health")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, dict)

    def test_public_status_unauthenticated_returns_200(self):
        status, _, body = self._request("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("mission_control", body)

    def test_public_token_unauthenticated_returns_200(self):
        status, _, body = self._request("/api/token")
        self.assertEqual(status, 200)
        self.assertIn("token", body)
        self.assertEqual(body["token"], self.token)

    def test_public_static_prefix_is_recognized_as_public(self):
        self.assertTrue(is_public_path("/static/app.css"))
        self.assertTrue(is_public_path("/static/js/main.js"))
        self.assertFalse(is_public_path("/api/accounts"))

    # ── 3. SSE Stream (/api/events/stream) Authentication ────────────────────

    def test_sse_stream_unauthenticated_returns_401(self):
        status, headers, body = self._request("/api/events/stream")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")
        self.assertIn("WWW-Authenticate", headers)
        self.assertIn("realm=\"MissionControl\"", headers["WWW-Authenticate"])

    def test_sse_stream_invalid_token_returns_401(self):
        headers = {"Authorization": "Bearer invalid-token-xyz"}
        status, _, body = self._request("/api/events/stream", headers=headers)
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "Unauthorized")

    def test_sse_stream_accepts_bearer_header(self):
        url = f"http://127.0.0.1:{self.port}/api/events/stream"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream")

    def test_sse_stream_accepts_token_query_parameter(self):
        url = f"http://127.0.0.1:{self.port}/api/events/stream?token={self.token}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream")

    def test_sse_stream_accepts_access_token_query_parameter(self):
        url = f"http://127.0.0.1:{self.port}/api/events/stream?access_token={self.token}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream")

    # ── 4. Local Network Boundary Enforcement ───────────────────────────────

    def test_loopback_validator_accepts_valid_loopback_interfaces(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertEqual(validate_host_binding(host), host)

    def test_loopback_validator_rejects_zero_zero_zero_zero(self):
        with self.assertRaises(SecurityError) as ctx:
            validate_host_binding("0.0.0.0")
        self.assertIn("strictly forbidden", str(ctx.exception))

    def test_loopback_validator_rejects_external_ip(self):
        with self.assertRaises(SecurityError) as ctx:
            validate_host_binding("192.168.1.100")
        self.assertIn("strictly forbidden", str(ctx.exception))

    def test_threaded_http_server_rejects_non_local_host(self):
        with self.assertRaises(SecurityError):
            ThreadedHTTPServer(("0.0.0.0", 0), dashboard.MissionControlHandler, bind_and_activate=False)

    # ── 5. PID File Lifecycle & GUI PID 3809 Non-Interference ────────────────

    def test_pid_file_lifecycle_acquire_and_release(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_pid = Path(tmp_dir) / "test_dashboard.pid"
            acquire_pid_file(pid_file=tmp_pid)
            self.assertTrue(tmp_pid.is_file())
            self.assertEqual(tmp_pid.read_text().strip(), str(os.getpid()))

            release_pid_file(pid_file=tmp_pid)
            self.assertFalse(tmp_pid.is_file())

    def test_pid_file_stale_detection_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_pid = Path(tmp_dir) / "test_dashboard.pid"
            # Write a non-existent PID
            tmp_pid.write_text("999999999", encoding="utf-8")
            acquire_pid_file(pid_file=tmp_pid)
            # Stale PID must be overwritten with current PID
            self.assertTrue(tmp_pid.is_file())
            self.assertEqual(tmp_pid.read_text().strip(), str(os.getpid()))
            release_pid_file(pid_file=tmp_pid)

    def test_pid_file_gui_pid_3809_non_interference(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_pid = Path(tmp_dir) / "test_dashboard.pid"
            # Simulate a stale PID file that happened to contain PID 3809
            tmp_pid.write_text("3809", encoding="utf-8")
            # acquire_pid_file must safely unlink and not signal or abort
            acquire_pid_file(pid_file=tmp_pid)
            self.assertEqual(tmp_pid.read_text().strip(), str(os.getpid()))
            release_pid_file(pid_file=tmp_pid)

    # ── 6. Operational Controls & Graceful Shutdown ──────────────────────────

    def test_setup_signal_handlers_does_not_crash(self):
        # Verify signal handler setup runs cleanly without raising
        setup_signal_handlers(self.server)

    def test_startup_health_checks_pass(self):
        report = run_startup_checks("127.0.0.1")
        self.assertEqual(report["status"], "HEALTHY")
        self.assertTrue(report["token_configured"])
        self.assertTrue(report["runtime_writable"])

    # ── 7. Local Log Rotation & Generational Backups ─────────────────────────

    def test_log_rotator_rotates_oversized_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_file = Path(tmp_dir) / "test_log.jsonl"
            # Write 200 bytes
            log_file.write_text("x" * 200, encoding="utf-8")
            # Set threshold to 100 bytes
            rotator = LogRotator(base_dir=tmp_dir, max_bytes=100, backup_count=3, target_files=[log_file])
            res = rotator.rotate_file(log_file)
            self.assertTrue(res["rotated"])
            self.assertEqual(res["size_bytes"], 200)

            backup1 = Path(tmp_dir) / "test_log.jsonl.1"
            self.assertTrue(backup1.is_file())
            self.assertEqual(backup1.stat().st_size, 200)
            # Original file reset
            self.assertTrue(log_file.is_file())
            self.assertEqual(log_file.stat().st_size, 0)

    def test_log_rotator_skips_file_under_threshold(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_file = Path(tmp_dir) / "test_small.jsonl"
            log_file.write_text("small", encoding="utf-8")
            rotator = LogRotator(base_dir=tmp_dir, max_bytes=1000, backup_count=3, target_files=[log_file])
            res = rotator.rotate_file(log_file)
            self.assertFalse(res["rotated"])
            backup1 = Path(tmp_dir) / "test_small.jsonl.1"
            self.assertFalse(backup1.exists())

    def test_log_rotator_cycles_backups_up_to_count(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_file = Path(tmp_dir) / "cycling.jsonl"
            rotator = LogRotator(base_dir=tmp_dir, max_bytes=50, backup_count=3, target_files=[log_file])

            for cycle in range(1, 5):
                log_file.write_text(f"cycle-{cycle}-" + "x" * 60, encoding="utf-8")
                rotator.rotate_file(log_file)

            # backup_count is 3, so .1, .2, .3 should exist; .4 must not exist
            self.assertTrue((Path(tmp_dir) / "cycling.jsonl.1").is_file())
            self.assertTrue((Path(tmp_dir) / "cycling.jsonl.2").is_file())
            self.assertTrue((Path(tmp_dir) / "cycling.jsonl.3").is_file())
            self.assertFalse((Path(tmp_dir) / "cycling.jsonl.4").exists())

    # ── 8. Secret Redaction In Sensitive Responses ───────────────────────────

    def test_secret_redaction_in_authenticated_accounts_response(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        status, _, body = self._request("/api/accounts", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("accounts", body)
        for acct in body["accounts"]:
            self.assertNotIn("sk-proj-", acct.get("masked_key", ""))
            self.assertNotIn("sk-live-", acct.get("masked_key", ""))
            self.assertNotIn("raw_secret", str(acct))


if __name__ == "__main__":
    unittest.main()
