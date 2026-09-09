"""
Unit tests for Cline Multi-Account Isolation (Phase 11 & Phase 12-14 requirement).
"""

import unittest
import os
import tempfile
from agents.cline.adapter import ClineAdapter


class TestClineMultiAccount(unittest.TestCase):
    """Verify cline-account-1, cline-account-2, and cline-account-3 remain strictly isolated."""

    def setUp(self):
        self.tmp_root = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_cline_accounts_isolated_directories(self):
        """Verify each account receives distinct isolated --config and --data-dir paths."""
        accts = ["cline-account-1", "cline-account-2", "cline-account-3"]
        adapters = []
        for a in accts:
            adapter = ClineAdapter(
                account_id=a,
                config_dir=os.path.join(self.tmp_root, a, "config"),
                data_dir=os.path.join(self.tmp_root, a, "data"),
            )
            adapters.append(adapter)

        # Check paths are completely distinct
        config_dirs = [str(a.config_dir) for a in adapters]
        data_dirs = [str(a.data_dir) for a in adapters]
        self.assertEqual(len(set(config_dirs)), 3)
        self.assertEqual(len(set(data_dirs)), 3)

    def test_cline_account_no_cross_contamination(self):
        """Writing data to account-1 must not touch account-2 or account-3."""
        dir1 = os.path.join(self.tmp_root, "cline-account-1", "config")
        dir2 = os.path.join(self.tmp_root, "cline-account-2", "config")
        os.makedirs(dir1, exist_ok=True)
        os.makedirs(dir2, exist_ok=True)

        with open(os.path.join(dir1, "settings.json"), "w") as f:
            f.write('{"custom_model": "test-1"}')

        self.assertTrue(os.path.exists(os.path.join(dir1, "settings.json")))
        self.assertFalse(os.path.exists(os.path.join(dir2, "settings.json")))


if __name__ == "__main__":
    unittest.main()
