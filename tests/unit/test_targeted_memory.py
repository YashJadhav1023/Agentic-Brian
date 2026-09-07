"""Unit tests for Phase 4E Targeted Memory Scoping & Handoff Compression.

Covers scope hierarchy weighting, recency decay multipliers, and compressed handoff bounds.
"""
import datetime
import tempfile
import unittest
from pathlib import Path

from handoffs.handoff_manager import HandoffManager, HandoffRecord
from memory.retrieval.retriever import MemoryRetriever
from memory.store.memory_store import MemoryEntry, MemoryScope, MemoryStore


class TestTargetedMemory(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(db_path=Path(self.tmp_dir.name) / "test_memory.db")
        self.retriever = MemoryRetriever(self.store)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_scope_hierarchy_weighting(self):
        # Create identical memories with different scopes
        m_task = self.store.add(
            content="Authentication token verification procedure",
            scope=MemoryScope.TASK,
            task_id="t-123",
            importance=3,
        )
        m_global = self.store.add(
            content="Authentication token verification procedure",
            scope=MemoryScope.GLOBAL,
            importance=3,
        )

        results = self.retriever.retrieve_context(
            query="Authentication token verification procedure",
            task_id="t-123",
            max_items=5,
        )
        self.assertTrue(len(results) >= 2)
        # Task scope should rank higher than global scope
        self.assertEqual(results[0].memory_id, m_task.memory_id)

    def test_recency_decay(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        old_time = (now - datetime.timedelta(days=10)).isoformat()

        m_old = self.store.add(
            content="Database connection retry logic",
            scope=MemoryScope.PROJECT,
            importance=3,
        )
        with self.store._connect() as conn:
            conn.execute("UPDATE memories SET created_at = ? WHERE memory_id = ?", (old_time, m_old.memory_id))

        m_new = self.store.add(
            content="Database connection retry logic",
            scope=MemoryScope.PROJECT,
            importance=3,
        )

        results = self.retriever.retrieve_context(
            query="Database connection retry logic",
            max_items=5,
        )
        self.assertTrue(len(results) >= 2)
        # Newer memory should rank higher due to recency multiplier
        self.assertEqual(results[0].memory_id, m_new.memory_id)


class TestCompressedHandoff(unittest.TestCase):
    def test_handoff_compression_bounds(self):
        record = HandoffRecord(
            task="Refactor telemetry",
            objective="Modernize handlers",
            completed=["Done 1"],
            next_action="Run test suite",
        )
        compressed = record.compress_for_prompt(max_chars=1000)
        self.assertLessEqual(len(compressed), 1000)
        self.assertIn("Prior Task Handoff", compressed)
        self.assertIn("Next Action", compressed)

    def test_manager_get_compressed_handoff(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = HandoffManager(root_dir=Path(tmpdir))
            record = HandoffRecord(
                task="Test task",
                objective="Objective info " * 20,
                completed=["Done 1"],
            )
            mgr.write_handoff(record)
            compressed = mgr.get_compressed_handoff(max_chars=200)
            self.assertLessEqual(len(compressed), 205)


if __name__ == "__main__":
    unittest.main()
