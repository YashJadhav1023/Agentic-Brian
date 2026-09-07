"""Universal Continue context synthesis.

Uses an isolated temporary workspace: tests must never write into the real
tasks/, handoffs/ or memory/ directories.
"""
import tempfile
import unittest
from pathlib import Path

from brain.context.continuator import UniversalContinuator
from handoffs.handoff_manager import HandoffManager, HandoffRecord
from memory.store.memory_store import MemoryStore
from sessions.session_manager import SessionManager
from tasks.manager import TaskManager, TaskStatus


class TestContinueFlow(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.tm = TaskManager(root_tasks_dir=root / "tasks")
        self.hm = HandoffManager(root_dir=root / "handoffs")
        self.sm = SessionManager(registry_file=root / "sessions.json")
        self.store = MemoryStore(db_path=root / "memory.db")
        self.root = root

    def tearDown(self):
        self._tmp.cleanup()

    def _continuator(self):
        return UniversalContinuator(
            self.tm,
            self.hm,
            workspace_dir=self.root,
            session_manager=self.sm,
            memory_store=self.store,
        )

    def test_continue_synthesizes_context(self):
        self.hm.write_handoff(
            HandoffRecord(
                task="Optimize memory index",
                objective="Add SQLite FTS index",
                completed=["Created migration script"],
                remaining_work=["Run benchmark test on retrieval"],
                next_action="Run benchmark test on retrieval",
                recommended_agent="antigravity-account-1",
                recommended_model="gemini-3.8-flash-medium",
            )
        )
        task = self.tm.create_task(
            title="Benchmark memory index",
            description="Run benchmark test on retrieval",
            assigned_agent="antigravity-account-1",
            assigned_model="gemini-3.8-flash-medium",
        )
        self.tm.update_status(task.task_id, TaskStatus.RUNNING)

        ctx = self._continuator().build_continue_context()

        self.assertEqual(ctx.target_agent, "antigravity-account-1")
        self.assertEqual(ctx.target_model, "gemini-3.8-flash-medium")
        self.assertIn("Benchmark memory index", ctx.prompt)
        self.assertEqual(ctx.next_recommended_action, "Run benchmark test on retrieval")
        self.assertEqual(ctx.remaining_work, ["Run benchmark test on retrieval"])

    def test_continue_with_empty_state_is_safe(self):
        ctx = self._continuator().build_continue_context()
        self.assertIsNone(ctx.active_task)
        self.assertEqual(ctx.source, "none")
        self.assertTrue(ctx.next_recommended_action)

    def test_continue_prefers_handoff_over_task_assignment(self):
        task = self.tm.create_task(
            title="Old task",
            description="stale",
            assigned_agent="cline",
            assigned_model="auto",
        )
        self.tm.update_status(task.task_id, TaskStatus.RUNNING)
        self.hm.write_handoff(
            HandoffRecord(
                task="Newer work",
                objective="pick up from account 2",
                completed=["did a thing"],
                next_action="Verify the refactor",
                recommended_agent="kiro-cli",
                recommended_model="auto",
                agent_id="antigravity-account-2",
                account_id="account-2",
                conversation_id="conv-abc",
            )
        )
        ctx = self._continuator().build_continue_context()
        self.assertEqual(ctx.target_agent, "kiro-cli")
        self.assertEqual(ctx.next_recommended_action, "Verify the refactor")
        # conv-abc belongs to account 2, so it must not be handed to kiro.
        self.assertIsNone(ctx.resume_conversation_id)

    def test_failed_task_recovery_takes_precedence_over_unrelated_handoff(self):
        """A failed task must be recovered, not silently skipped."""
        self.hm.write_handoff(
            HandoffRecord(
                task="Some earlier work",
                objective="unrelated",
                completed=["done"],
                next_action="Verify the earlier refactor",
                recommended_agent="kiro-cli",
                task_id="task-earlier",
            )
        )
        failed = self.tm.create_task(
            title="Read every file",
            description="Read every file",
            assigned_agent="antigravity-account-2",
            assigned_model="gemini-3.8-flash-low",
        )
        self.tm.update_status(
            failed.task_id, TaskStatus.FAILED, error="no output produced — permission denied"
        )

        ctx = self._continuator().build_continue_context()
        self.assertEqual(ctx.active_task.task_id, failed.task_id)
        self.assertEqual(ctx.target_agent, "antigravity-account-2")
        self.assertIn("recovery", ctx.source)
        self.assertIn(failed.task_id, ctx.next_recommended_action)
        self.assertIn("permission denied", ctx.next_recommended_action)

    def test_handoff_for_the_same_task_wins(self):
        task = self.tm.create_task(
            title="Refactor telemetry",
            description="Refactor telemetry",
            assigned_agent="antigravity-account-2",
        )
        self.tm.update_status(task.task_id, TaskStatus.COMPLETED)
        self.hm.write_handoff(
            HandoffRecord(
                task="Refactor telemetry",
                objective="Refactor telemetry",
                completed=["done"],
                next_action="Verify the telemetry refactor",
                recommended_agent="kiro-cli",
                task_id=task.task_id,
                agent_id="antigravity-account-2",
                account_id="account-2",
                conversation_id="conv-xyz",
            )
        )
        ctx = self._continuator().build_continue_context()
        self.assertEqual(ctx.target_agent, "kiro-cli")
        self.assertEqual(ctx.next_recommended_action, "Verify the telemetry refactor")

    def test_cancelled_tasks_are_not_continued(self):
        """A cancelled task is a decision, not unfinished work."""
        cancelled = self.tm.create_task(title="Abandoned", description="Abandoned")
        self.tm.update_status(cancelled.task_id, TaskStatus.CANCELLED)
        done = self.tm.create_task(title="Finished", description="Finished")
        self.tm.update_status(done.task_id, TaskStatus.COMPLETED)

        # BLOCKED, FAILED and CANCELLED share a directory; the filter must use
        # the record's status.
        self.assertEqual(self.tm.list_tasks(status=TaskStatus.BLOCKED), [])
        self.assertEqual(self.tm.list_tasks(status=TaskStatus.FAILED), [])
        self.assertEqual(len(self.tm.list_tasks(status=TaskStatus.CANCELLED)), 1)

        ctx = self._continuator().build_continue_context()
        self.assertIsNotNone(ctx.active_task)
        self.assertEqual(ctx.active_task.task_id, done.task_id)

    def test_relevant_memory_is_attached(self):
        self.store.add(
            content="The retrieval benchmark script lives in scripts/bench.py",
            source_agent="antigravity-account-2",
            importance=5,
        )
        self.hm.write_handoff(
            HandoffRecord(
                task="Benchmark",
                objective="run the retrieval benchmark",
                completed=["nothing yet"],
                next_action="Run the retrieval benchmark script",
                recommended_agent="kiro-cli",
            )
        )
        ctx = self._continuator().build_continue_context()
        self.assertTrue(ctx.relevant_memory)
        self.assertIn("scripts/bench.py", ctx.prompt)


if __name__ == "__main__":
    unittest.main()
