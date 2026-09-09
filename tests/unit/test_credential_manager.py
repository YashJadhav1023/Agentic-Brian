"""Unit tests for CredentialManager, EncryptedFileStore, and SecretRedactor."""
import os
import tempfile
import unittest
from pathlib import Path

from providers.registry.credential_manager import (
    CredentialManager,
    EncryptedFileStore,
    EnvCredentialStore,
    SecretRedactor,
)


class TestCredentialManager(unittest.TestCase):
    def test_encrypted_file_store(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store_path = Path(tmp_dir) / "creds.dat"
            store = EncryptedFileStore(storage_path=store_path)

            ref = "secret://mission-control/openai/test-account"
            secret = "sk-super-secret-key-12345"

            # Store
            self.assertTrue(store.store(ref, secret))
            self.assertTrue(store.exists(ref))
            self.assertTrue(store_path.is_file())

            # Verify file mode is 0600
            file_stat = os.stat(store_path)
            mode = oct(file_stat.st_mode)[-3:]
            self.assertEqual(mode, "600")

            # Retrieve
            retrieved = store.retrieve(ref)
            self.assertEqual(retrieved, secret)

            # Raw file does NOT contain plaintext secret
            raw_bytes = store_path.read_bytes()
            self.assertNotIn(secret.encode("utf-8"), raw_bytes)

            # Delete
            self.assertTrue(store.delete(ref))
            self.assertFalse(store.exists(ref))
            self.assertIsNone(store.retrieve(ref))

    def test_env_credential_store(self):
        store = EnvCredentialStore()
        var_name = "TEST_API_KEY_ENV_VAR"
        ref = f"env://{var_name}"

        os.environ[var_name] = "my-env-secret-999"
        self.assertTrue(store.exists(ref))
        self.assertEqual(store.retrieve(ref), "my-env-secret-999")

        # Delete from env
        self.assertTrue(store.delete(ref))
        self.assertFalse(store.exists(ref))
        self.assertNotIn(var_name, os.environ)

    def test_credential_manager_unified_routing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_store = EncryptedFileStore(storage_path=Path(tmp_dir) / "creds.dat")
            cred_mgr = CredentialManager(primary_store=file_store, fallback_store=file_store)

            secret_ref = "secret://mission-control/anthropic/work"
            secret_val = "sk-ant-api-key-test-abc"
            cred_mgr.store(secret_ref, secret_val)
            self.assertEqual(cred_mgr.retrieve(secret_ref), secret_val)

            env_ref = "env://MY_DYNAMIC_API_KEY"
            os.environ["MY_DYNAMIC_API_KEY"] = "dynamic-val-456"
            self.assertEqual(cred_mgr.retrieve(env_ref), "dynamic-val-456")
            del os.environ["MY_DYNAMIC_API_KEY"]

    def test_secret_redactor(self):
        redactor = SecretRedactor()

        # Text redaction of API key patterns
        text = "Connecting with API key sk-proj-1234567890abcdef12345678 and Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        redacted = redactor.redact_text(text)
        self.assertNotIn("sk-proj-1234567890abcdef12345678", redacted)
        self.assertIn(SecretRedactor.REDACTION_TOKEN, redacted)

        # Dictionary redaction
        sensitive_dict = {
            "account_id": "test",
            "api_key": "sk-secret-value",
            "password": "my-password",
            "safe_field": "hello world",
            "nested": {
                "token": "secret-token",
                "normal": "safe",
            },
        }
        safe_dict = redactor.redact_dict(sensitive_dict)
        self.assertEqual(safe_dict["api_key"], SecretRedactor.REDACTION_TOKEN)
        self.assertEqual(safe_dict["password"], SecretRedactor.REDACTION_TOKEN)
        self.assertEqual(safe_dict["safe_field"], "hello world")
        self.assertEqual(safe_dict["nested"]["token"], SecretRedactor.REDACTION_TOKEN)
        self.assertEqual(safe_dict["nested"]["normal"], "safe")


if __name__ == "__main__":
    unittest.main()
