"""Integration tests for safe sandboxed execution across all 4 agents."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents.base.adapter import UNKNOWN_MODEL, TaskExecutionResult
from brain.orchestrator.orchestrator import Orchestrator
from brain.worktree.worktree_manager import WorktreeManager, WorktreeStatus
from events.bus import EventBus, EventType
from handoffs.handoff_manager import HandoffManager
from memory.store.memory_store import MemoryStore
from providers.registry.bootstrap import create_default_registry
from sessions.session_manager import SessionManager
from tasks.manager import TaskManager, TaskStatus


class MockRunner:
    _real_run = subprocess.run

    def __init__(self, mutate_file=None, file_content=""):
        self.mutate_file = mutate_file
        self.file_content = file_content
        self.invoked_cwd = None
        self.invoked_argv = None

    def __call__(self, argv, **kwargs):
        argv_list = [str(a) for a in argv]
        cwd = kwargs.get("cwd")

        # Let git commands pass through to real git!
        if argv_list and (argv_list[0] == "git" or argv_list[0].endswith("/git")):
            return MockRunner._real_run(argv, **kwargs)

        # Probe calls (e.g. `agy models`)
        if "models" in argv_list:
            return subprocess.CompletedProcess(
                args=argv_list,
                returncode=0,
                stdout="gemini-3.8-flash-medium\tGemini 3.8 Flash Medium\n",
                stderr="",
            )

        # Actual agent CLI execution
        self.invoked_argv = argv_list
        self.invoked_cwd = str(cwd)
        if self.mutate_file and cwd:
            target = Path(cwd) / self.mutate_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.file_content, encoding="utf-8")

        payload = {
            "status": "SUCCESS",
            "response": f"Successfully processed {self.mutate_file or 'task'}",
            "conversation_id": "conv-test-123",
            "usage": {"total_tokens": 42},
        }
        return subprocess.CompletedProcess(
            args=argv_list,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )


class TestAgentSandboxes(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_agent_sandbox_")
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True)
        self.sandbox_dir = Path(self.temp_dir) / "sandboxes"
        self.sandbox_dir.mkdir(parents=True)

        # Initialize real git repository
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True, capture_output=True)

        (self.repo_dir / "README.md").write_text("# Project Root\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_dir, check=True, capture_output=True)

        self.task_manager = TaskManager(root_tasks_dir=self.repo_dir / "tasks")
        self.handoff_manager = HandoffManager(root_dir=self.repo_dir / "handoffs")
        self.event_bus = EventBus(log_path=self.repo_dir / "events.jsonl")
        self.memory_store = MemoryStore(db_path=self.repo_dir / "memory.db")
        self.session_manager = SessionManager(registry_file=self.repo_dir / "sessions.json")
        self.worktree_manager = WorktreeManager(
            canonical_repo=self.repo_dir,
            sandbox_root=self.sandbox_dir,
        )

        self.orchestrator = Orchestrator(
            task_manager=self.task_manager,
            registry=create_default_registry(),
            event_bus=self.event_bus,
            workspace_dir=self.repo_dir,
            handoff_manager=self.handoff_manager,
            session_manager=self.session_manager,
            memory_store=self.memory_store,
            worktree_manager=self.worktree_manager,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_account1_executes_mutating_task_in_isolated_worktree(self):
        runner = MockRunner(mutate_file="acc1_output.txt", file_content="Account 1 Generated")
        task = self.orchestrator.plan_and_dispatch(
            instruction="Create a file named acc1_output.txt with content Account 1 Generated",
            preferred_agent="antigravity-account-1",
        )
        self.assertEqual(task.assigned_agent, "antigravity-account-1")

        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            results = self.orchestrator.execute_next()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

        # 1. Canonical repo MUST NOT have acc1_output.txt
        self.assertFalse((self.repo_dir / "acc1_output.txt").exists())

        # 2. Worktree MUST have acc1_output.txt
        rec = self.worktree_manager.get(task.task_id)
        self.assertIsNotNone(rec)
        wt_file = Path(rec.path) / "acc1_output.txt"
        self.assertTrue(wt_file.exists())
        self.assertEqual(wt_file.read_text(), "Account 1 Generated")

        # 3. Invoked cwd was the worktree path
        self.assertEqual(runner.invoked_cwd, str(rec.path))

        # 4. Diff and files touched detected
        diff_info = self.worktree_manager.diff(task.task_id)
        self.assertIn("acc1_output.txt", diff_info["files_changed"])

    def test_account2_executes_mutating_task_in_isolated_worktree(self):
        runner = MockRunner(mutate_file="acc2_refactor.py", file_content="def refactored(): pass\n")
        task = self.orchestrator.plan_and_dispatch(
            instruction="Refactor module and create acc2_refactor.py",
            preferred_agent="antigravity-account-2",
        )
        self.assertEqual(task.assigned_agent, "antigravity-account-2")

        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            results = self.orchestrator.execute_next()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

        # Canonical repo untouched
        self.assertFalse((self.repo_dir / "acc2_refactor.py").exists())

        # Worktree modified
        rec = self.worktree_manager.get(task.task_id)
        self.assertIsNotNone(rec)
        self.assertTrue((Path(rec.path) / "acc2_refactor.py").exists())

        # Verify Account 2 profile flag preserved
        self.assertIn("--app_data_dir=antigravity-ide", runner.invoked_argv)

    def test_kiro_executes_mutating_task_in_isolated_worktree(self):
        runner = MockRunner(mutate_file="kiro_build.log", file_content="Build completed successfully\n")
        task = self.orchestrator.plan_and_dispatch(
            instruction="Run build and create kiro_build.log",
            preferred_agent="kiro-cli",
            execution_options={"allow_tool_permissions": True},
        )
        self.assertEqual(task.assigned_agent, "kiro-cli")

        with mock.patch("agents.kiro.adapter.subprocess.run", runner):
            with mock.patch.object(self.orchestrator.registry.get_adapter("kiro-cli"), "_resolve_executable", return_value="/mock/kiro-cli"):
                results = self.orchestrator.execute_next()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

        # Canonical repo untouched
        self.assertFalse((self.repo_dir / "kiro_build.log").exists())

        # Worktree modified
        rec = self.worktree_manager.get(task.task_id)
        self.assertIsNotNone(rec)
        self.assertTrue((Path(rec.path) / "kiro_build.log").exists())
        self.assertEqual(runner.invoked_cwd, str(rec.path))

        # Preserved corrected Kiro CLI invocation: chat --no-interactive
        self.assertEqual(runner.invoked_argv[1], "chat")
        self.assertIn("--no-interactive", runner.invoked_argv)
        self.assertNotIn("--prompt", runner.invoked_argv)

    def test_cline_executes_mutating_task_in_isolated_worktree(self):
        runner = MockRunner(mutate_file="style.css", file_content="body { color: red; }\n")
        task = self.orchestrator.plan_and_dispatch(
            instruction="Modify styles and create style.css in frontend",
            preferred_agent="cline",
        )
        self.assertEqual(task.assigned_agent, "cline")

        with mock.patch("agents.cline.adapter.subprocess.run", runner):
            with mock.patch.object(self.orchestrator.registry.get_adapter("cline"), "_resolve_executable", return_value="/mock/cline"):
                results = self.orchestrator.execute_next()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

        # Canonical repo untouched
        self.assertFalse((self.repo_dir / "style.css").exists())

        # Worktree modified
        rec = self.worktree_manager.get(task.task_id)
        self.assertIsNotNone(rec)
        self.assertTrue((Path(rec.path) / "style.css").exists())
        self.assertEqual(runner.invoked_cwd, str(rec.path))

    def test_read_only_task_operates_directly_without_worktree(self):
        runner = MockRunner()
        task = self.orchestrator.plan_and_dispatch(
            instruction="Inspect the repository and explain the architecture",
            preferred_agent="antigravity-account-1",
        )

        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            results = self.orchestrator.execute_next()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

        # No worktree created for read-only query
        rec = self.worktree_manager.get(task.task_id)
        self.assertIsNone(rec)
        # Executed directly against canonical repo
        self.assertEqual(runner.invoked_cwd, str(self.repo_dir))


if __name__ == "__main__":
    unittest.main()
