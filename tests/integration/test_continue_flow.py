"""Integration test verifying end-to-end Universal Continue workflow."""
import unittest
from pathlib import Path

from brain.context.continuator import UniversalContinuator
from handoffs.handoff_manager import HandoffManager, HandoffRecord
from tasks.manager import TaskManager, TaskStatus


class TestContinueFlow(unittest.TestCase):

    def test_continue_synthesizes_context(self):
        tm = TaskManager()
        hm = HandoffManager()

        rec = HandoffRecord(
            task="Optimize memory index",
            objective="Add SQLite FTS index",
            completed=["Created migration script"],
            next_action="Run benchmark test on retrieval",
            recommended_agent="antigravity-account-1",
            recommended_model="gemini-3.8-flash-medium",
        )
        hm.write_handoff(rec)

        task = tm.create_task(
            title="Benchmark memory index",
            description="Run benchmark test on retrieval",
            assigned_agent="antigravity-account-1",
            assigned_model="gemini-3.8-flash-medium",
        )
        tm.update_status(task.task_id, TaskStatus.RUNNING)

        uc = UniversalContinuator(tm, hm)
        ctx = uc.build_continue_context()

        self.assertEqual(ctx.target_agent, "antigravity-account-1")
        self.assertEqual(ctx.target_model, "gemini-3.8-flash-medium")
        self.assertIn("Benchmark memory index", ctx.prompt)


if __name__ == "__main__":
    unittest.main()
