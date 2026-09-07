"""Unit tests for Phase 4B Context & Token Optimization.

Covers TargetFileDetector, ContextDeduplicator, ContextBudget, ContextOptimizer,
and TokenTelemetryTracker.
"""
import json
import tempfile
import unittest
from pathlib import Path

from brain.context.context_optimizer import (
    ContextBudget,
    ContextDeduplicator,
    ContextOptimizer,
    TargetFileDetector,
    TokenTelemetryTracker,
)
from events.bus import EventBus, EventType


class TestTargetFileDetector(unittest.TestCase):
    def test_extracts_files_from_instruction_text(self):
        text = "Please review auth.py and check tests/test_auth.py for edge cases."
        files = TargetFileDetector.detect(text)
        self.assertIn("auth.py", files)
        self.assertIn("tests/test_auth.py", files)

    def test_combines_explicit_files_with_detected_files(self):
        text = "Fix bug in handler.ts"
        explicit = ["config/settings.json"]
        files = TargetFileDetector.detect(text, declared_files=explicit)
        self.assertIn("handler.ts", files)
        self.assertIn("config/settings.json", files)

    def test_validates_against_workspace_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "existing.py").write_text("# code", encoding="utf-8")
            text = "Edit existing.py and missing_file.py"
            files = TargetFileDetector.detect(text, workspace=ws)
            self.assertIn("existing.py", files)
            self.assertIn("missing_file.py", files)


class TestContextDeduplicator(unittest.TestCase):
    def test_removes_duplicate_lines(self):
        text = (
            "Line 1: Important configuration line that repeats\n"
            "Line 2: Another distinct instruction\n"
            "Line 1: Important configuration line that repeats\n"
            "Line 1: Important configuration line that repeats\n"
        )
        deduped, removed = ContextDeduplicator.deduplicate_lines(text)
        self.assertEqual(removed, 2)
        self.assertEqual(deduped.count("Important configuration line that repeats"), 1)


class TestContextOptimizer(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.budget = ContextBudget(
            max_memory_chars=100,
            max_handoff_chars=100,
            max_file_context_chars=150,
            max_total_prompt_chars=500,
        )
        self.optimizer = ContextOptimizer(budget=self.budget, event_bus=self.bus)

    def test_safe_truncation_adds_notification(self):
        long_text = "x" * 200
        truncated, was_truncated = self.optimizer.truncate_safely(long_text, 50, "Memory")
        self.assertTrue(was_truncated)
        self.assertIn("Memory truncated to 50 chars", truncated)

    def test_lightweight_task_applies_token_saving_options(self):
        prompt, report = self.optimizer.optimize_prompt(
            task_id="test-task-1",
            title="quick fix typo",
            description="Fix typo in README.md",
            complexity="fast",
        )
        self.assertTrue(report.is_lightweight)
        self.assertTrue(report.options_applied.get("disable_slash_commands"))
        self.assertIn("README.md", prompt)

    def test_prompt_budget_capping(self):
        huge_memory = "Memory item: " + ("data " * 100)
        huge_handoff = "Handoff item: " + ("state " * 100)
        prompt, report = self.optimizer.optimize_prompt(
            task_id="test-task-2",
            title="Complex task",
            description="Do deep work",
            memory_block=huge_memory,
            handoff_block=huge_handoff,
        )
        self.assertTrue(report.truncations.get("memory"))
        self.assertTrue(report.truncations.get("handoff"))
        self.assertLessEqual(len(prompt), self.budget.max_total_prompt_chars)


class TestTokenTelemetryTracker(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.tmp_log = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self.tmp_log.close()
        self.tracker = TokenTelemetryTracker(event_bus=self.bus, log_path=Path(self.tmp_log.name))

    def tearDown(self):
        Path(self.tmp_log.name).unlink(missing_ok=True)

    def test_record_known_tokens(self):
        record = self.tracker.record_usage(
            task_id="task-known",
            agent_id="kiro",
            account_id="kiro-cli",
            provider="kiro",
            requested_model="claude-3-7-sonnet",
            actual_model="claude-3-7-sonnet",
            input_tokens=1500,
            output_tokens=350,
            total_tokens=1850,
            duration_seconds=4.2,
            success=True,
        )
        self.assertEqual(record.token_reporting, "known")
        self.assertEqual(record.total_tokens, 1850)

    def test_record_unknown_tokens(self):
        record = self.tracker.record_usage(
            task_id="task-unknown",
            agent_id="antigravity-account-2",
            account_id="account-2",
            provider="antigravity",
            requested_model="gemini-2.5-pro",
            actual_model="unknown",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            duration_seconds=12.0,
            success=True,
        )
        self.assertEqual(record.token_reporting, "unknown")
        self.assertIsNone(record.total_tokens)

    def test_metrics_aggregation(self):
        self.tracker.record_usage(
            task_id="t1",
            agent_id="kiro",
            account_id="kiro-cli",
            provider="kiro",
            requested_model="model-a",
            actual_model="model-a",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            duration_seconds=2.0,
            success=True,
        )
        metrics = self.tracker.get_metrics()
        self.assertEqual(metrics["total_tasks_recorded"], 1)
        self.assertEqual(metrics["total_known_tokens"], 150)
        self.assertEqual(metrics["by_agent"]["kiro"]["known_tokens"], 150)


if __name__ == "__main__":
    unittest.main()
