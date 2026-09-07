"""Unit tests for WorktreeManager."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from brain.worktree.worktree_manager import WorktreeManager, WorktreeRecord, WorktreeStatus


class TestWorktreeManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_wt_repo_")
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True)
        self.sandbox_dir = Path(self.temp_dir) / "sandboxes"
        self.sandbox_dir.mkdir(parents=True)

        # Initialize a real git repository
        self._git(["init"], cwd=self.repo_dir)
        self._git(["config", "user.name", "Test User"], cwd=self.repo_dir)
        self._git(["config", "user.email", "test@example.com"], cwd=self.repo_dir)

        # Create an initial commit
        (self.repo_dir / "README.md").write_text("# Test Repo\nInitial content.\n", encoding="utf-8")
        self._git(["add", "README.md"], cwd=self.repo_dir)
        self._git(["commit", "-m", "Initial commit"], cwd=self.repo_dir)

        self.manager = WorktreeManager(
            canonical_repo=self.repo_dir,
            sandbox_root=self.sandbox_dir,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _git(self, args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.repo_dir),
            capture_output=True,
            text=True,
            check=True,
        )

    def test_create_worktree_and_verify_isolation(self):
        record = self.manager.create(
            task_id="task-001",
            agent_id="antigravity-account-2",
            account_id="account-2",
        )
        self.assertIsInstance(record, WorktreeRecord)
        self.assertEqual(record.task_id, "task-001")
        self.assertEqual(record.agent_id, "antigravity-account-2")
        self.assertEqual(record.account_id, "account-2")
        self.assertEqual(record.branch, "agentic/task/task-001")
        self.assertEqual(record.status, WorktreeStatus.ACTIVE)

        wt_path = Path(record.path)
        self.assertTrue(wt_path.exists())
        self.assertTrue((wt_path / "README.md").exists())
        self.assertEqual((wt_path / "README.md").read_text(), "# Test Repo\nInitial content.\n")

    def test_unique_worktrees_for_different_tasks(self):
        r1 = self.manager.create("task-aaa", agent_id="kiro-cli")
        r2 = self.manager.create("task-bbb", agent_id="cline")
        self.assertNotEqual(r1.path, r2.path)
        self.assertNotEqual(r1.branch, r2.branch)
        self.assertTrue(Path(r1.path).exists())
        self.assertTrue(Path(r2.path).exists())

    def test_correct_branch_and_project_state(self):
        record = self.manager.create("task-state-check")
        # Check git branch inside worktree
        branch_proc = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=Path(record.path))
        self.assertEqual(branch_proc.stdout.strip(), "agentic/task/task-state-check")
        # Main repo branch is still master/main
        main_branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo_dir).stdout.strip()
        self.assertNotEqual(main_branch, record.branch)

    def test_agent_working_directory_modification_leaves_canonical_clean(self):
        record = self.manager.create("task-modify")
        wt_path = Path(record.path)

        # Agent modifies a file in the worktree
        (wt_path / "new_file.txt").write_text("Created in sandbox", encoding="utf-8")
        (wt_path / "README.md").write_text("# Modified in sandbox\n", encoding="utf-8")

        # Canonical repo MUST NOT have new_file.txt and README.md must be unmodified
        self.assertFalse((self.repo_dir / "new_file.txt").exists())
        self.assertEqual((self.repo_dir / "README.md").read_text(), "# Test Repo\nInitial content.\n")

        # Canonical git status must be clean
        dirty, count, _ = self.manager.check_canonical_dirty()
        self.assertFalse(dirty)
        self.assertEqual(count, 0)

    def test_diff_detection_and_snapshot(self):
        record = self.manager.create("task-diff")
        wt_path = Path(record.path)
        (wt_path / "code.py").write_text("def hello():\n    return 42\n", encoding="utf-8")

        snapshot = self.manager.snapshot("task-diff")
        self.assertTrue(snapshot["changed"])
        self.assertEqual(snapshot["files"], 1)
        self.assertIn("code.py", snapshot["files_changed"])
        self.assertGreater(snapshot["insertions"], 0)

        diff_info = self.manager.diff("task-diff")
        self.assertIn("def hello():", diff_info["diff"])
        self.assertEqual(diff_info["files_changed"], ["code.py"])

    def test_apply_safeguards_require_confirmation(self):
        record = self.manager.create("task-apply-req")
        (Path(record.path) / "feature.txt").write_text("Feature content\n", encoding="utf-8")
        self.manager.snapshot("task-apply-req")

        # Calling apply without confirm=True raises ValueError
        with self.assertRaises(ValueError):
            self.manager.apply("task-apply-req", confirm=False)

    def test_apply_safeguards_refuse_on_dirty_canonical_tree(self):
        record = self.manager.create("task-dirty-guard")
        (Path(record.path) / "feature.txt").write_text("Feature\n", encoding="utf-8")
        self.manager.snapshot("task-dirty-guard")

        # Dirty the canonical tree
        (self.repo_dir / "uncommitted.txt").write_text("Uncommitted user edit", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            self.manager.apply("task-dirty-guard", confirm=True)
        self.assertIn("canonical working tree has 1 uncommitted user changes", str(ctx.exception))

    def test_apply_merges_changes_cleanly_when_confirmed(self):
        record = self.manager.create("task-merge-clean")
        (Path(record.path) / "feature.txt").write_text("Feature cleanly applied\n", encoding="utf-8")
        self.manager.snapshot("task-merge-clean")

        result = self.manager.apply("task-merge-clean", approver="admin", confirm=True)
        self.assertTrue(result["applied"])
        self.assertEqual(result["approver"], "admin")

        # Now canonical tree has feature.txt!
        self.assertTrue((self.repo_dir / "feature.txt").exists())
        self.assertEqual((self.repo_dir / "feature.txt").read_text(), "Feature cleanly applied\n")

        rec = self.manager.get("task-merge-clean")
        self.assertEqual(rec.status, WorktreeStatus.APPLIED)

    def test_reject_discards_worktree_and_branch(self):
        record = self.manager.create("task-reject")
        wt_path = Path(record.path)
        (wt_path / "bad.txt").write_text("bad", encoding="utf-8")
        self.manager.snapshot("task-reject")

        # Rejection requires confirmation
        with self.assertRaises(ValueError):
            self.manager.reject("task-reject", confirm=False)

        res = self.manager.reject("task-reject", reason="Not up to spec", confirm=True)
        self.assertTrue(res["rejected"])
        self.assertFalse(wt_path.exists())
        rec = self.manager.get("task-reject")
        self.assertEqual(rec.status, WorktreeStatus.REJECTED)

    def test_cleanup_preserves_branch_by_default(self):
        record = self.manager.create("task-cleanup")
        wt_path = Path(record.path)
        (wt_path / "wip.txt").write_text("wip", encoding="utf-8")
        self.manager.snapshot("task-cleanup")

        self.manager.cleanup("task-cleanup")
        self.assertFalse(wt_path.exists())
        # Branch still exists in git for review
        branches = self._git(["branch", "--list", record.branch]).stdout
        self.assertIn("agentic/task/task-cleanup", branches)

    def test_orphan_recovery(self):
        record = self.manager.create("task-recover")
        wt_path = Path(record.path)
        self.manager._records.clear()

        recovered = self.manager.recover()
        self.assertTrue(len(recovered) >= 1)
        found = [r for r in recovered if r.task_id == "task-recover"]
        self.assertTrue(len(found) > 0)


if __name__ == "__main__":
    unittest.main()
