"""End-to-end Account 2 pipeline: brain -> router -> adapter -> task -> memory
-> session -> handoff -> events -> continue.

The CLI subprocess is patched so the whole chain is exercised deterministically
without spending model quota. The real command line, the real JSON normalizer
and the real persistence layers are all used.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents.base.adapter import UNKNOWN_MODEL
from brain.orchestrator.orchestrator import Orchestrator
from events.bus import EventBus, EventType
from handoffs.handoff_manager import HandoffManager
from memory.store.memory_store import MemoryStore
from providers.registry.bootstrap import create_default_registry
from sessions.session_manager import SessionManager
from tasks.manager import TaskManager, TaskStatus

CONVERSATION_ID = "11111111-2222-3333-4444-555555555555"

SUCCESS_PAYLOAD = {
    "conversation_id": CONVERSATION_ID,
    "status": "SUCCESS",
    "response": "ACCOUNT2_INTEGRATION_TEST_PASSED\n",
    "duration_seconds": 4.2,
    "num_turns": 1,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 20,
        "thinking_tokens": 5,
        "cache_read_tokens": 10,
        "total_tokens": 120,
    },
}


class RecordingRunner:
    """Stand-in for subprocess.run that records the agent CLI argv.

    Patching `module.subprocess.run` replaces the attribute on the shared
    `subprocess` module, so unrelated calls (such as `git status`) would also be
    intercepted. Anything that is not an agent CLI invocation is therefore
    delegated to the real implementation.
    """

    _real_run = staticmethod(subprocess.run)

    def __init__(self, payload=None, returncode=0, stderr=""):
        self.payload = payload if payload is not None else SUCCESS_PAYLOAD
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        argv_list = list(argv)
        if not any(str(a).startswith("--app_data_dir=") for a in argv_list):
            return RecordingRunner._real_run(argv, **kwargs)
        self.calls.append(argv_list)
        stdout = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return subprocess.CompletedProcess(
            args=argv_list, returncode=self.returncode, stdout=stdout, stderr=self.stderr
        )

    @property
    def last(self) -> list[str]:
        self_calls = self.calls
        assert self_calls, "no agent CLI invocation was recorded"
        return self_calls[-1]


class Account2PipelineTestCase(unittest.TestCase):
    """Shared isolated workspace so tests never touch real project state."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "tasks").mkdir()
        (root / "handoffs").mkdir()
        (root / "runtime" / "logs").mkdir(parents=True)

        self.task_manager = TaskManager(root_tasks_dir=root / "tasks")
        self.handoff_manager = HandoffManager(root_dir=root / "handoffs")
        self.event_bus = EventBus(log_path=root / "runtime" / "logs" / "events.jsonl")
        self.memory_store = MemoryStore(db_path=root / "memory.db")
        self.session_manager = SessionManager(registry_file=root / "sessions.json")
        self.orchestrator = Orchestrator(
            task_manager=self.task_manager,
            registry=create_default_registry(),
            event_bus=self.event_bus,
            workspace_dir=root,
            handoff_manager=self.handoff_manager,
            session_manager=self.session_manager,
            memory_store=self.memory_store,
        )
        self.root = root

    def tearDown(self):
        self._tmp.cleanup()

    def event_types(self):
        return [e.event_type for e in self.event_bus.get_recent_events(200)]


class TestAccount2Execution(Account2PipelineTestCase):

    def test_task_assignment_and_completion(self):
        """Requirements: task assignment, execution, task completion."""
        runner = RecordingRunner()
        task = self.orchestrator.plan_and_dispatch(
            "Review and refactor the telemetry component"
        )
        self.assertEqual(task.assigned_agent, "antigravity-account-2")
        self.assertEqual(task.assigned_account, "account-2")
        self.assertEqual(task.status, TaskStatus.READY)

        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            results = self.orchestrator.execute_next()

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.agent_id, "antigravity-account-2")
        self.assertEqual(result.account_id, "account-2")
        self.assertEqual(result.conversation_id, CONVERSATION_ID)

        stored = self.task_manager.get_task(task.task_id)
        self.assertEqual(stored.status, TaskStatus.COMPLETED)
        self.assertEqual(stored.conversation_id, CONVERSATION_ID)
        self.assertIsNotNone(stored.session_id)
        self.assertIsNotNone(stored.completed_at)
        self.assertEqual(stored.actual_model, UNKNOWN_MODEL)
        self.assertTrue(stored.requested_model)

    def test_execution_uses_account2_profile_and_least_privilege(self):
        """Requirements: account isolation and least-privileged execution."""
        runner = RecordingRunner()
        self.orchestrator.plan_and_dispatch("Refactor the auth component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        argv = runner.last
        self.assertIn("--app_data_dir=antigravity-account-3", argv)
        self.assertNotIn("--app_data_dir=antigravity-account-jadhav", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertIn("--output-format", argv)

    def test_privilege_escalation_only_when_explicitly_requested(self):
        runner = RecordingRunner()
        self.orchestrator.plan_and_dispatch(
            "Refactor the auth component",
            execution_options={"dangerously_skip_permissions": True},
        )
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()
        self.assertIn("--dangerously-skip-permissions", runner.last)

    def test_model_from_router_is_passed_to_the_cli(self):
        """Requirement: model selection flows through the router, not a constant."""
        runner = RecordingRunner()
        self.orchestrator.plan_and_dispatch(
            "Refactor the auth component",
            preferred_agent="antigravity-account-2",
            preferred_model="gemini-3.1-pro-high",
        )
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()
        self.assertIn("--model=gemini-3.1-pro-high", runner.last)

    def test_memory_storage_and_scoping(self):
        """Requirement: shared memory, not a separate Account 2 memory."""
        runner = RecordingRunner()
        task = self.orchestrator.plan_and_dispatch("Review the retry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        memories = self.memory_store.list_all(10)
        self.assertTrue(memories)
        entry = memories[0]
        self.assertEqual(entry.source_agent, "antigravity-account-2")
        self.assertEqual(entry.task_id, task.task_id)
        self.assertIn("account-2", entry.tags)
        stored = self.task_manager.get_task(task.task_id)
        self.assertIn(entry.memory_id, stored.memory_refs)

    def test_session_mapping_is_persisted(self):
        """Requirement: task -> session -> conversation_id mapping."""
        runner = RecordingRunner()
        task = self.orchestrator.plan_and_dispatch("Review the retry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        record = self.session_manager.get_by_task(task.task_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.agent_id, "antigravity-account-2")
        self.assertEqual(record.conversation_id, CONVERSATION_ID)
        self.assertEqual(record.metadata.get("account_id"), "account-2")

    def test_structured_handoff_is_created(self):
        """Requirement: structured handoff in the existing handoff system."""
        runner = RecordingRunner()
        task = self.orchestrator.plan_and_dispatch("Review the retry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        record = self.handoff_manager.get_current_record()
        self.assertIsNotNone(record)
        self.assertEqual(record.task_id, task.task_id)
        self.assertEqual(record.agent_id, "antigravity-account-2")
        self.assertEqual(record.account_id, "account-2")
        self.assertEqual(record.conversation_id, CONVERSATION_ID)
        self.assertTrue(record.next_action)
        self.assertTrue(record.completed)
        self.assertTrue(record.recommended_agent)
        self.assertNotEqual(record.recommended_agent, "antigravity-account-2")
        markdown = self.handoff_manager.get_current_handoff()
        self.assertIn("Handoff:", markdown)
        self.assertIn(CONVERSATION_ID, markdown)

    def test_account2_telemetry_events(self):
        """Requirement: dedicated Account 2 events via the existing event bus."""
        runner = RecordingRunner()
        self.orchestrator.plan_and_dispatch("Review the retry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        types = self.event_types()
        for expected in (
            EventType.ANTIGRAVITY_ACCOUNT2_STARTED,
            EventType.ANTIGRAVITY_ACCOUNT2_COMPLETED,
            EventType.ANTIGRAVITY_ACCOUNT2_HEALTH,
            EventType.ANTIGRAVITY_ACCOUNT2_MODEL_SELECTED,
            EventType.ANTIGRAVITY_ACCOUNT2_HANDOFF,
        ):
            self.assertIn(expected, types, f"missing {expected}")
        # Generic events must still be emitted for provider-agnostic consumers.
        self.assertIn(EventType.TASK_CREATED, types)
        self.assertIn(EventType.TASK_COMPLETED, types)
        self.assertIn(EventType.MEMORY_CREATED, types)

    def test_events_record_the_account(self):
        runner = RecordingRunner()
        self.orchestrator.plan_and_dispatch("Review the retry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()
        completed = [
            e for e in self.event_bus.get_recent_events(200)
            if e.event_type == EventType.TASK_COMPLETED
        ]
        self.assertTrue(completed)
        self.assertEqual(completed[0].metadata["account_id"], "account-2")


class TestAccount2FailureHandling(Account2PipelineTestCase):
    """Requirement: failure handling."""

    def test_nonzero_exit_marks_task_failed_and_emits_failure_events(self):
        runner = RecordingRunner(payload="", returncode=1, stderr="RESOURCE_EXHAUSTED")
        task = self.orchestrator.plan_and_dispatch("Review the retry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            results = self.orchestrator.execute_next()

        self.assertFalse(results[0].success)
        stored = self.task_manager.get_task(task.task_id)
        self.assertEqual(stored.status, TaskStatus.FAILED)
        self.assertTrue(stored.errors)
        types = self.event_types()
        self.assertIn(EventType.TASK_FAILED, types)
        self.assertIn(EventType.ANTIGRAVITY_ACCOUNT2_FAILED, types)

    def test_soft_provider_error_is_not_treated_as_success(self):
        runner = RecordingRunner(payload={"status": "ERROR", "response": "terminated"})
        task = self.orchestrator.plan_and_dispatch("Review the retry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()
        self.assertEqual(self.task_manager.get_task(task.task_id).status, TaskStatus.FAILED)


class TestRestartRecovery(Account2PipelineTestCase):
    """Requirement: restart recovery."""

    def test_interrupted_running_task_is_recovered_to_ready(self):
        task = self.orchestrator.plan_and_dispatch("Review the retry component")
        self.task_manager.update_status(task.task_id, TaskStatus.RUNNING)
        self.assertEqual(self.task_manager.get_task(task.task_id).status, TaskStatus.RUNNING)

        recovered = TaskManager(root_tasks_dir=self.root / "tasks").recover_orphaned_tasks()
        self.assertEqual(recovered, 1)
        reloaded = self.task_manager.get_task(task.task_id)
        self.assertEqual(reloaded.status, TaskStatus.READY)
        self.assertTrue(any("interrupted" in e for e in reloaded.errors))

    def test_task_record_survives_a_fresh_manager(self):
        runner = RecordingRunner()
        task = self.orchestrator.plan_and_dispatch("Review the retry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        fresh = TaskManager(root_tasks_dir=self.root / "tasks").get_task(task.task_id)
        self.assertEqual(fresh.status, TaskStatus.COMPLETED)
        self.assertEqual(fresh.conversation_id, CONVERSATION_ID)


class TestUniversalContinue(Account2PipelineTestCase):
    """Requirement: continue, without the user re-explaining anything."""

    def test_continue_resumes_from_handoff_and_session(self):
        runner = RecordingRunner()
        first = self.orchestrator.plan_and_dispatch("Review and refactor the telemetry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        ctx = self.orchestrator.build_continue_context()
        self.assertIn("handoff", ctx.source)
        self.assertTrue(ctx.next_recommended_action)
        self.assertIn(first.task_id, ctx.next_recommended_action)
        self.assertTrue(ctx.remaining_work)
        # Handoff recommended a different agent, so continue must hand over.
        self.assertNotEqual(ctx.target_agent, "antigravity-account-2")
        self.assertIn("Universal Continue", ctx.prompt)

    def test_continue_only_resumes_a_conversation_of_the_same_account(self):
        runner = RecordingRunner()
        self.orchestrator.plan_and_dispatch(
            "Review the retry component", preferred_agent="antigravity-account-2"
        )
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        ctx = self.orchestrator.build_continue_context()
        if ctx.target_agent != "antigravity-account-2":
            self.assertIsNone(
                ctx.resume_conversation_id,
                "a conversation id must never cross accounts",
            )

    def test_continue_reuses_the_conversation_when_the_same_agent_continues(self):
        runner = RecordingRunner()
        task = self.orchestrator.plan_and_dispatch(
            "Review the retry component", preferred_agent="antigravity-account-2"
        )
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        # Force the handoff to recommend the same agent, as an escalation would.
        record = self.handoff_manager.get_current_record()
        record.recommended_agent = "antigravity-account-2"
        self.handoff_manager.write_handoff(record)

        ctx = self.orchestrator.build_continue_context()
        self.assertEqual(ctx.target_agent, "antigravity-account-2")
        self.assertEqual(ctx.resume_conversation_id, CONVERSATION_ID)

    def test_continue_executes_the_next_step_end_to_end(self):
        """Requirement: full swarm chain — account 2 -> handoff -> another agent."""
        runner = RecordingRunner()
        self.orchestrator.plan_and_dispatch("Review and refactor the telemetry component")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        real_run = subprocess.run

        def fake_kiro(argv, **kwargs):
            argv_list = list(argv)
            if argv_list and "kiro-cli" in str(argv_list[0]):
                return subprocess.CompletedProcess(argv_list, 0, "verification complete", "")
            return real_run(argv, **kwargs)

        with mock.patch("agents.kiro.adapter.subprocess.run", fake_kiro):
            ctx, result = self.orchestrator.continue_work()

        self.assertEqual(ctx.target_agent, "kiro-cli")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.agent_id, "kiro-cli")


class TestMemoryScoping(Account2PipelineTestCase):
    """Requirement: retrieve only relevant memory, never the whole database."""

    def test_prompt_contains_only_bounded_relevant_memory(self):
        for i in range(30):
            self.memory_store.add(
                content=f"unrelated fact number {i} about penguins and glaciers",
                source_agent="kiro-cli",
                importance=1,
            )
        self.memory_store.add(
            content="The telemetry component uses an exponential backoff retry policy",
            source_agent="antigravity-account-1",
            importance=5,
            tags=["telemetry"],
        )

        runner = RecordingRunner()
        self.orchestrator.plan_and_dispatch("Refactor the telemetry component retry policy")
        with mock.patch("agents.antigravity.adapter.subprocess.run", runner):
            self.orchestrator.execute_next()

        prompt = runner.last[-1]
        self.assertIn("telemetry", prompt)
        self.assertLess(len(prompt), 6000, "prompt must stay bounded")
        self.assertLessEqual(prompt.count("unrelated fact number"), 4)


if __name__ == "__main__":
    unittest.main()
