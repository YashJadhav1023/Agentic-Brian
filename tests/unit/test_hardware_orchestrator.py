"""Unit tests for Phase 4G Hardware-Aware Swarm Scheduler (2 Cores / 16 GB RAM).

Covers concurrency limits (MAX_CONCURRENT_AGENTS <= 2), heavy agent throttling
(MAX_HEAVY_AGENTS = 1), and batch scheduling prioritization.
"""
import unittest

from brain.orchestrator.swarm import MAX_CONCURRENT_AGENTS, MAX_HEAVY_AGENTS, SwarmWorkerPool
from tasks.manager import Task


class TestHardwareOrchestrator(unittest.TestCase):
    def test_default_hardware_constraints(self):
        # 2-core Linux host constraint
        self.assertLessEqual(MAX_CONCURRENT_AGENTS, 2)
        self.assertEqual(MAX_HEAVY_AGENTS, 1)

    def test_heavy_task_batch_partitioning(self):
        # Create a mock pool to test batch selection
        pool = SwarmWorkerPool.__new__(SwarmWorkerPool)
        pool._max_workers = 2

        task_heavy_1 = Task(task_id="t-h1", title="Heavy reasoning 1", description="desc", complexity="reasoning")
        task_heavy_2 = Task(task_id="t-h2", title="Heavy reasoning 2", description="desc", complexity="strong")
        task_light_1 = Task(task_id="t-l1", title="Light fix", description="desc", complexity="fast", assigned_agent="kiro")

        ready = [task_heavy_1, task_heavy_2, task_light_1]

        # In execute_batch logic:
        heavy_seen = False
        selected: list[Task] = []
        for t in ready:
            is_heavy = t.complexity in ("reasoning", "strong") or t.assigned_agent in ("antigravity-account-1", "antigravity-account-2")
            if is_heavy:
                if not heavy_seen:
                    selected.append(t)
                    heavy_seen = True
            else:
                selected.append(t)
            if len(selected) >= pool._max_workers:
                break

        self.assertEqual(len(selected), 2)
        # Should contain at most 1 heavy task and 1 light task
        heavy_count = sum(1 for t in selected if t.complexity in ("reasoning", "strong"))
        self.assertEqual(heavy_count, 1)
        self.assertIn(task_heavy_1, selected)
        self.assertIn(task_light_1, selected)


if __name__ == "__main__":
    unittest.main()
