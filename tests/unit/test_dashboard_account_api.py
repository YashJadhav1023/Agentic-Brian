"""Part 14 — Dashboard account API tests (Phase 22, Parts 7 and 8).

Exercises the account-facing HTTP surface over a real loopback server, matching
the harness used by ``tests/unit/test_dashboard.py``. Only read-only GET
endpoints are called: these tests must never enable, disable, or remove a real
locally registered account.

The properties asserted are the ones an operator depends on:

* ``/api/accounts`` never returns a plaintext secret,
* ``/api/accounts/metrics`` returns the eight independent Part 8 metrics,
* ``metrics`` is routed as a metrics request, not as an account named "metrics",
* an unknown account yields 404 rather than an empty success.
"""

import json
import threading
import unittest
import urllib.error
import urllib.request

from ui.dashboard import dashboard


class _DashboardServerTestCase(unittest.TestCase):
    """Boots one ephemeral dashboard server for the whole class."""

    @classmethod
    def setUpClass(cls):
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

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        token = dashboard.get_or_create_auth_token()
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read().decode("utf-8"))

    def _get_status(self, path: str, token: str | None = None) -> int:
        url = f"http://127.0.0.1:{self.port}{path}"
        tok = token if token is not None else dashboard.get_or_create_auth_token()
        headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code


class TestAccountListEndpoint(_DashboardServerTestCase):
    def test_accounts_payload_has_a_list_and_a_matching_count(self):
        data = self._get("/api/accounts")
        self.assertIn("accounts", data)
        self.assertIn("count", data)
        self.assertIsInstance(data["accounts"], list)
        self.assertEqual(data["count"], len(data["accounts"]))

    def test_each_account_exposes_its_decoupled_states(self):
        """Part 2's orthogonal states must reach the UI separately."""
        accounts = self._get("/api/accounts")["accounts"]
        if not accounts:
            self.skipTest("no accounts registered locally")
        for account in accounts:
            for key in ("id", "provider_id", "lifecycle_state", "status"):
                self.assertIn(key, account, f"account missing '{key}'")

    def test_each_account_carries_usage_and_reliability_figures(self):
        accounts = self._get("/api/accounts")["accounts"]
        if not accounts:
            self.skipTest("no accounts registered locally")
        for account in accounts:
            for key in ("requests", "tokens", "estimated_cost", "failure_rate"):
                self.assertIn(key, account, f"account missing '{key}'")

    def test_no_plaintext_secret_is_ever_returned(self):
        raw = json.dumps(self._get("/api/accounts"))
        # Live key prefixes for the providers this platform onboards.
        for prefix in ("sk-live-", "sk-ant-api", "rzp_live_", "AIzaSy"):
            self.assertNotIn(prefix, raw, f"response leaked a '{prefix}' credential")

    def test_credentials_are_referenced_by_uri_not_by_value(self):
        accounts = self._get("/api/accounts")["accounts"]
        for account in accounts:
            ref = account.get("credential_reference")
            if ref:
                self.assertTrue(
                    str(ref).startswith("secret://") or ref == "***REDACTED***",
                    f"credential_reference must be opaque, got {ref!r}",
                )

    def test_filtering_by_provider_never_widens_the_result(self):
        everything = self._get("/api/accounts")
        filtered = self._get("/api/accounts?provider=definitely-not-a-provider")
        self.assertEqual(filtered["count"], 0)
        self.assertLessEqual(filtered["count"], everything["count"])


class TestAccountMetricsEndpoint(_DashboardServerTestCase):
    def test_metrics_endpoint_returns_the_eight_granular_metrics(self):
        payload = self._get("/api/accounts/metrics")
        self.assertIn("metrics", payload)
        self.assertEqual(
            set(payload["metrics"]),
            {
                "registered",
                "signed_in",
                "healthy",
                "online",
                "busy",
                "idle",
                "offline",
                "disabled",
            },
        )

    def test_every_metric_is_a_non_negative_integer(self):
        for key, value in self._get("/api/accounts/metrics")["metrics"].items():
            self.assertIsInstance(value, int, f"{key} must be an int")
            self.assertGreaterEqual(value, 0, f"{key} must not be negative")

    def test_count_mirrors_the_registered_metric(self):
        payload = self._get("/api/accounts/metrics")
        self.assertEqual(payload["count"], payload["metrics"]["registered"])

    def test_no_metric_exceeds_the_registered_total(self):
        """A subset count larger than the total would mean a conflated source."""
        metrics = self._get("/api/accounts/metrics")["metrics"]
        total = metrics["registered"]
        for key, value in metrics.items():
            if key == "registered":
                continue
            self.assertLessEqual(
                value, total, f"{key}={value} exceeds registered={total}"
            )

    def test_online_and_offline_cannot_both_claim_every_account(self):
        metrics = self._get("/api/accounts/metrics")["metrics"]
        self.assertLessEqual(
            metrics["online"] + metrics["offline"], metrics["registered"]
        )

    def test_registered_total_agrees_with_the_account_list(self):
        """The metrics endpoint and the list endpoint must not disagree."""
        listed = self._get("/api/accounts")["count"]
        registered = self._get("/api/accounts/metrics")["metrics"]["registered"]
        self.assertEqual(listed, registered)

    def test_metrics_is_not_mistaken_for_an_account_id(self):
        """Route ordering: 'metrics' must not fall through to the id lookup."""
        payload = self._get("/api/accounts/metrics")
        self.assertIn("metrics", payload)
        self.assertNotIn("error", payload)

    def test_metrics_response_carries_no_secret(self):
        raw = json.dumps(self._get("/api/accounts/metrics"))
        for prefix in ("sk-live-", "sk-ant-api", "AIzaSy"):
            self.assertNotIn(prefix, raw)


class TestSingleAccountEndpoint(_DashboardServerTestCase):
    def test_an_unknown_account_returns_404(self):
        self.assertEqual(
            self._get_status("/api/accounts/definitely-not-an-account"), 404
        )

    def test_a_known_account_is_retrievable_by_id(self):
        accounts = self._get("/api/accounts")["accounts"]
        if not accounts:
            self.skipTest("no accounts registered locally")
        account_id = accounts[0]["id"]
        detail = self._get(f"/api/accounts/{account_id}")
        self.assertEqual(detail["id"], account_id)

    def test_a_single_account_response_carries_no_secret(self):
        accounts = self._get("/api/accounts")["accounts"]
        if not accounts:
            self.skipTest("no accounts registered locally")
        raw = json.dumps(self._get(f"/api/accounts/{accounts[0]['id']}"))
        for prefix in ("sk-live-", "sk-ant-api", "AIzaSy"):
            self.assertNotIn(prefix, raw)


if __name__ == "__main__":
    unittest.main()
