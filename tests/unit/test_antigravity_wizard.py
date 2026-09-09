"""Unit tests for Antigravity multi-account onboarding wizard and endpoints."""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from ui.dashboard import dashboard
from ui.dashboard.dashboard import WizardManager, WizardSession


class TestAntigravityWizardEndpoints(unittest.TestCase):
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

    def _post(self, path: str, payload: dict):
        url = f"http://127.0.0.1:{self.port}{path}"
        token = dashboard.get_or_create_auth_token()
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        token = dashboard.get_or_create_auth_token()
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read().decode("utf-8"))

    def _delete(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        token = dashboard.get_or_create_auth_token()
        req = urllib.request.Request(url, method="DELETE", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_antigravity_login_methods(self):
        payload = self._get("/api/wizard/login-methods?provider=antigravity")
        self.assertEqual(payload["provider_id"], "antigravity")
        self.assertTrue(payload["supported"])
        method_ids = [m["id"] for m in payload["login_methods"]]
        self.assertIn("oauth", method_ids)
        self.assertIn("api_key", method_ids)

    def test_antigravity_wizard_flow_with_email_and_token(self):
        test_act_id = "test-antigravity-wizard-act"
        # 1. Start wizard
        start_res = self._post("/api/wizard/start", {
            "provider_id": "antigravity",
            "account_id": test_act_id,
        })
        self.assertEqual(start_res["status"], "started")
        wiz_id = start_res["wizard"]["wizard_id"]
        self.assertTrue(wiz_id.startswith("wiz-"))

        # 2. Select auth
        sel_res = self._post("/api/wizard/select-auth", {
            "wizard_id": wiz_id,
            "auth_method": "api_key",
        })
        self.assertEqual(sel_res["wizard"]["step"], "select_auth")

        # 3. Configure email + token
        cfg_res = self._post("/api/wizard/configure", {
            "wizard_id": wiz_id,
            "config": {
                "email": "test-dev@example.com",
                "auth_token": "test-secret-token-12345",
            },
        })
        self.assertEqual(cfg_res["wizard"]["step"], "configure")

        # 4. Check auth status
        chk_res = self._get(f"/api/wizard/check-auth?wizard_id={wiz_id}")
        self.assertIn("token_exists", chk_res)

        # 5. Authenticate step
        auth_res = self._post("/api/wizard/authenticate", {"wizard_id": wiz_id})
        self.assertEqual(auth_res["wizard"]["step"], "authenticate")
        self.assertEqual(auth_res["wizard"]["lifecycle_state"], "AUTHENTICATED")

        # 6. Cancel to clean up
        can_res = self._post("/api/wizard/cancel", {"wizard_id": wiz_id})
        self.assertEqual(can_res["status"], "cancelled")

    def test_antigravity_wizard_launch_login_oauth(self):
        test_act_id = "test-antigravity-oauth-act"
        start_res = self._post("/api/wizard/start", {
            "provider_id": "antigravity",
            "account_id": test_act_id,
        })
        wiz_id = start_res["wizard"]["wizard_id"]

        sel_res = self._post("/api/wizard/select-auth", {
            "wizard_id": wiz_id,
            "auth_method": "oauth",
        })
        self.assertEqual(sel_res["wizard"]["step"], "select_auth")

        # Configure email
        cfg_res = self._post("/api/wizard/configure", {
            "wizard_id": wiz_id,
            "config": {
                "email": "agent-user@gmail.com",
            },
        })
        self.assertEqual(cfg_res["wizard"]["step"], "configure")

        # Launch login
        launch_res = self._post("/api/wizard/launch-login", {"wizard_id": wiz_id})
        self.assertEqual(launch_res["status"], "ok")
        self.assertIn("login", launch_res)
        self.assertTrue(launch_res["login"]["success"])
        self.assertEqual(launch_res["login"]["email"], "agent-user@gmail.com")

        # Cancel to clean up
        self._post("/api/wizard/cancel", {"wizard_id": wiz_id})

    def test_antigravity_complete_registration_and_delete(self):
        import secrets
        test_act_id = f"test-antigravity-reg-{secrets.token_hex(4)}"
        # 1. Start
        start_res = self._post("/api/wizard/start", {
            "provider_id": "antigravity",
            "account_id": test_act_id,
        })
        wiz_id = start_res["wizard"]["wizard_id"]

        # 2. Select auth
        self._post("/api/wizard/select-auth", {
            "wizard_id": wiz_id,
            "auth_method": "api_key",
        })

        # 3. Configure
        self._post("/api/wizard/configure", {
            "wizard_id": wiz_id,
            "config": {
                "email": "reg-test@example.com",
                "auth_token": "token-test-12345",
            },
        })

        # 4. Authenticate
        self._post("/api/wizard/authenticate", {"wizard_id": wiz_id})

        # 5. Validate (live=False for unit test speed)
        val_res = self._post("/api/wizard/validate", {"wizard_id": wiz_id, "live": False})
        self.assertEqual(val_res["wizard"]["step"], "validate")

        # 6. Register
        reg_res = self._post("/api/wizard/register", {"wizard_id": wiz_id})
        self.assertEqual(reg_res["wizard"]["step"], "register")
        self.assertTrue(reg_res["wizard"]["registered"])

        # 7. Complete
        comp_res = self._post("/api/wizard/complete", {"wizard_id": wiz_id})
        self.assertEqual(comp_res["status"], "complete")
        self.assertTrue(comp_res["wizard"]["completed"])

        # 8. Check account is visible via /api/accounts
        accounts = self._get("/api/accounts")["accounts"]
        found = any(a["id"] == test_act_id for a in accounts)
        self.assertTrue(found)

        # 9. Clean up account via DELETE
        del_res = self._delete(f"/api/accounts/{test_act_id}")
        self.assertEqual(del_res.get("status"), "removed")

    def test_next_account_id_endpoint_returns_safe_identifier(self):
        res = self._get("/api/wizard/next-account-id?provider=cline")
        self.assertIn("next_account_id", res)
        self.assertTrue(res["next_account_id"].startswith("cline-"))

        res_kiro = self._get("/api/wizard/next-account-id?provider=kiro")
        self.assertIn("next_account_id", res_kiro)
        self.assertTrue(res_kiro["next_account_id"].startswith("kiro-"))

    def test_wizard_start_sanitizes_account_id_with_spaces_and_symbols(self):
        # User enters "Cline -4" or similar
        start_res = self._post("/api/wizard/start", {
            "provider_id": "cline",
            "account_id": "Cline -4",
        })
        self.assertEqual(start_res["status"], "started")
        wizard = start_res["wizard"]
        self.assertEqual(wizard["account_id"], "cline-4")
        # Clean up session
        self._post("/api/wizard/cancel", {"wizard_id": wizard["wizard_id"]})

    def test_cline_zero_key_onboarding_without_api_key(self):
        start_res = self._post("/api/wizard/start", {
            "provider_id": "cline",
            "account_id": "test-cline-zero-key-unit",
        })
        wiz_id = start_res["wizard"]["wizard_id"]
        # Select local_session auth
        self._post("/api/wizard/select-auth", {"wizard_id": wiz_id, "auth_method": "local_session"})
        # Configure without any api_key
        cfg_res = self._post("/api/wizard/configure", {"wizard_id": wiz_id, "config": {}})
        self.assertEqual(cfg_res["wizard"]["step"], "configure")
        # Authenticate must succeed zero-key without throwing auth rejected
        auth_res = self._post("/api/wizard/authenticate", {"wizard_id": wiz_id})
        self.assertEqual(auth_res["wizard"]["step"], "authenticate")
        # Validate (live=False)
        val_res = self._post("/api/wizard/validate", {"wizard_id": wiz_id, "live": False})
        self.assertEqual(val_res["wizard"]["step"], "validate")
        # Cancel and rollback
        self._post("/api/wizard/cancel", {"wizard_id": wiz_id})

    def test_kiro_zero_key_onboarding_without_api_key(self):
        start_res = self._post("/api/wizard/start", {
            "provider_id": "kiro",
            "account_id": "test-kiro-zero-key-unit",
        })
        wiz_id = start_res["wizard"]["wizard_id"]
        # Select local_session auth
        self._post("/api/wizard/select-auth", {"wizard_id": wiz_id, "auth_method": "local_session"})
        # Configure without any api_key
        cfg_res = self._post("/api/wizard/configure", {"wizard_id": wiz_id, "config": {}})
        self.assertEqual(cfg_res["wizard"]["step"], "configure")
        # Authenticate must succeed zero-key
        auth_res = self._post("/api/wizard/authenticate", {"wizard_id": wiz_id})
        self.assertEqual(auth_res["wizard"]["step"], "authenticate")
        # Validate (live=False)
        val_res = self._post("/api/wizard/validate", {"wizard_id": wiz_id, "live": False})
        self.assertEqual(val_res["wizard"]["step"], "validate")
        # Cancel and rollback
        self._post("/api/wizard/cancel", {"wizard_id": wiz_id})


if __name__ == "__main__":
    unittest.main()

