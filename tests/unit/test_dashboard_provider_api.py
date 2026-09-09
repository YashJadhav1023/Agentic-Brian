"""Part 14 — Dashboard provider API tests (Phase 22, Parts 6 and 9).

Exercises the provider-facing HTTP surface that backs the Provider Registry UI
and the Account Add Wizard's provider/login-method discovery, over a real
loopback server. All calls are read-only GETs; nothing here starts an
authentication flow or mutates a provider.

Two contracts matter most here:

* the wizard must be able to discover, per provider, which login methods are
  genuinely supported — otherwise it offers steps that cannot complete;
* no response key may contain the substring ``auth``, because the response-wide
  secret redactor blanks any such key and would silently replace this
  non-secret data with ``***REDACTED***``. That is why the payload says
  ``login_methods``.
"""

import json
import threading
import unittest
import urllib.error
import urllib.request

from ui.dashboard import dashboard


class _DashboardServerTestCase(unittest.TestCase):
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


class TestProviderListEndpoint(_DashboardServerTestCase):
    def test_providers_payload_is_a_list(self):
        data = self._get("/api/providers")
        self.assertIn("providers", data)
        self.assertIsInstance(data["providers"], list)

    def test_each_provider_exposes_identity_and_health(self):
        providers = self._get("/api/providers")["providers"]
        if not providers:
            self.skipTest("no providers registered locally")
        for provider in providers:
            for key in ("id", "name", "type", "status"):
                self.assertIn(key, provider, f"provider missing '{key}'")

    def test_provider_ids_are_unique(self):
        providers = self._get("/api/providers")["providers"]
        ids = [p["id"] for p in providers]
        self.assertEqual(len(ids), len(set(ids)), "provider ids must be unique")

    def test_provider_response_carries_no_secret(self):
        raw = json.dumps(self._get("/api/providers"))
        for prefix in ("sk-live-", "sk-ant-api", "rzp_live_", "AIzaSy"):
            self.assertNotIn(prefix, raw, f"response leaked a '{prefix}' credential")


class TestWizardProviderDiscovery(_DashboardServerTestCase):
    """Part 6/9: what the Provider Registry UI and Add Wizard render."""

    def test_wizard_providers_payload_has_a_list_and_matching_count(self):
        data = self._get("/api/wizard/providers")
        self.assertIn("providers", data)
        self.assertIn("count", data)
        self.assertEqual(data["count"], len(data["providers"]))

    def test_each_entry_reports_identity_type_accounts_and_login_methods(self):
        entries = self._get("/api/wizard/providers")["providers"]
        if not entries:
            self.skipTest("no providers registered locally")
        for entry in entries:
            for key in ("id", "name", "type", "account_count", "login_methods"):
                self.assertIn(key, entry, f"wizard entry missing '{key}'")
            self.assertIsInstance(entry["account_count"], int)
            self.assertGreaterEqual(entry["account_count"], 0)
            self.assertIsInstance(entry["login_methods"], list)

    def test_no_response_key_contains_the_substring_auth(self):
        """Such a key would be blanked by the redactor before reaching the UI."""

        def _assert_keys_clean(node) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertNotIn(
                        "auth",
                        key.lower(),
                        f"key '{key}' would be redacted in the response",
                    )
                    _assert_keys_clean(value)
            elif isinstance(node, list):
                for item in node:
                    _assert_keys_clean(item)

        _assert_keys_clean(self._get("/api/wizard/providers"))

    def test_login_methods_are_never_redacted_placeholders(self):
        """If this regresses, the wizard shows no way to sign in at all."""
        entries = self._get("/api/wizard/providers")["providers"]
        for entry in entries:
            self.assertNotEqual(entry["login_methods"], "***REDACTED***")
            for method in entry["login_methods"]:
                self.assertNotIn("REDACTED", str(method))

    def test_provider_account_counts_agree_with_the_accounts_endpoint(self):
        entries = self._get("/api/wizard/providers")["providers"]
        accounts = self._get("/api/accounts")["accounts"]
        if not entries or not accounts:
            self.skipTest("no providers or accounts registered locally")

        actual: dict[str, int] = {}
        for account in accounts:
            actual[account["provider_id"]] = actual.get(account["provider_id"], 0) + 1

        for entry in entries:
            if entry["id"] in actual:
                self.assertEqual(
                    entry["account_count"],
                    actual[entry["id"]],
                    f"account_count disagrees for provider '{entry['id']}'",
                )


class TestWizardLoginMethodsEndpoint(_DashboardServerTestCase):
    def test_missing_provider_parameter_is_a_400(self):
        self.assertEqual(self._get_status("/api/wizard/login-methods"), 400)

    def test_a_known_provider_reports_its_login_methods(self):
        entries = self._get("/api/wizard/providers")["providers"]
        if not entries:
            self.skipTest("no providers registered locally")
        provider_id = entries[0]["id"]

        payload = self._get(f"/api/wizard/login-methods?provider={provider_id}")
        self.assertEqual(payload["provider_id"], provider_id)
        self.assertIsInstance(payload["login_methods"], list)
        self.assertIsInstance(payload["supported"], bool)

    def test_an_unknown_provider_falls_back_to_a_generic_api_key_method(self):
        """Unknown ids are treated as OpenAI-compatible so the wizard stays usable.

        This is a deliberate design choice in ``auth_methods_for``: refusing an
        unrecognised id would make every new OpenAI-compatible gateway
        un-onboardable until the code shipped a special case for it.
        """
        payload = self._get("/api/wizard/login-methods?provider=not-a-provider")
        self.assertEqual(payload["provider_id"], "not-a-provider")
        self.assertTrue(payload["supported"])
        self.assertEqual([m["id"] for m in payload["login_methods"]], ["api_key"])

    def test_an_uninstalled_agent_cli_offers_no_login_method(self):
        """The fallback must not paper over an agent whose CLI is absent.

        Cline's methods come from live capability discovery, so when the CLI is
        not installed the wizard must be told there is nothing to offer rather
        than being handed a generic API-key step that cannot complete.
        """
        from agents.cline.auth import ClineAuthManager

        installed = ClineAuthManager().resolve_executable() is not None
        payload = self._get("/api/wizard/login-methods?provider=cline")
        if installed:
            self.skipTest("cline CLI is installed locally; absence cannot be observed")
        self.assertFalse(payload["supported"])
        self.assertEqual(payload["login_methods"], [])

    def test_the_provider_id_alias_is_accepted(self):
        payload = self._get("/api/wizard/login-methods?provider_id=not-a-provider")
        self.assertEqual(payload["provider_id"], "not-a-provider")

    def test_the_legacy_auth_methods_path_is_still_served(self):
        """The path may contain 'auth'; only response *keys* must avoid it."""
        payload = self._get("/api/wizard/auth-methods?provider=not-a-provider")
        self.assertIn("login_methods", payload)
        self.assertNotIn("auth_methods", payload)

    def test_supported_agrees_with_whether_methods_were_returned(self):
        payload = self._get("/api/wizard/login-methods?provider=not-a-provider")
        self.assertEqual(payload["supported"], bool(payload["login_methods"]))


class TestWizardEventsEndpoint(_DashboardServerTestCase):
    def test_wizard_events_endpoint_returns_a_list(self):
        payload = self._get("/api/wizard/events")
        self.assertIn("events", payload)
        self.assertIsInstance(payload["events"], list)

    def test_wizard_events_carry_no_secret(self):
        raw = json.dumps(self._get("/api/wizard/events"))
        for prefix in ("sk-live-", "sk-ant-api", "AIzaSy"):
            self.assertNotIn(prefix, raw)


if __name__ == "__main__":
    unittest.main()
