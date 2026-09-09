"""Unit tests for Job, JobManager, UsageTracker, and explicit failovers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.base.adapter import TaskExecutionResult
from brain.orchestrator.job import Job
from brain.orchestrator.job_manager import JobManager, UsageTracker
from providers.base import AIProvider, ProviderType
from providers.registry.account_registry import Account, AccountRegistry, AccountStatus
from providers.registry.credential_manager import CredentialManager
from providers.registry.provider_registry import ProviderRegistry


class MockAIProvider(AIProvider):
    def __init__(self, provider_id: str, will_fail: bool = False, error_msg: str = ""):
        self._provider_id = provider_id
        self._will_fail = will_fail
        self._error_msg = error_msg
        self.call_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.API

    def capabilities(self):
        return frozenset()

    def health(self):
        return True, "OK"

    def list_models(self):
        return []

    def execute(self, job: Job) -> TaskExecutionResult:
        self.call_count += 1
        if self._will_fail:
            return TaskExecutionResult(
                task_id=job.id,
                agent_id=job.account,
                account_id=job.account,
                provider=self._provider_id,
                success=False,
                exit_code=1,
                output="",
                error=self._error_msg or "Simulated provider failure",
            )
        return TaskExecutionResult(
            task_id=job.id,
            agent_id=job.account,
            account_id=job.account,
            provider=self._provider_id,
            success=True,
            exit_code=0,
            output=f"Output from {self._provider_id}",
            error="",
            total_tokens=42,
            actual_model=job.model or "default",
        )

    def validate_config(self):
        return True, []


class TestJobManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.storage_dir = Path(self.tmp_dir.name)

        self.account_reg = AccountRegistry()
        self.provider_reg = ProviderRegistry(account_registry=self.account_reg)
        self.cred_mgr = CredentialManager()

        self.job_mgr = JobManager(
            provider_registry=self.provider_reg,
            storage_dir=self.storage_dir,
            credential_manager=self.cred_mgr,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_job_dataclass_lifecycle(self):
        job = Job(id="j1", task="test task", provider="openai", account="a1")
        self.assertEqual(job.status, "pending")

        job.mark_started()
        self.assertEqual(job.status, "running")
        self.assertIsNotNone(job.started_at)

        job.mark_completed(result={"output": "done"}, duration=1.23)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.duration, 1.23)

    def test_usage_tracker(self):
        tracker = UsageTracker()
        tracker.record("j1", "openai", "acct-1", "gpt-4o", prompt_tokens=10, completion_tokens=20, cost_usd=0.001)
        tracker.record("j2", "openai", "acct-1", "gpt-4o", prompt_tokens=5, completion_tokens=15, cost_usd=0.0008)
        tracker.record("j3", "gemini", "acct-2", "gemini-2.0", prompt_tokens=30, completion_tokens=40, cost_usd=0.002)

        summary_all = tracker.get_summary()
        self.assertEqual(summary_all["requests"], 3)
        self.assertEqual(summary_all["total_tokens"], 120)

        summary_openai = tracker.get_summary(provider_id="openai")
        self.assertEqual(summary_openai["requests"], 2)
        self.assertEqual(summary_openai["total_tokens"], 50)

    def test_job_submit_and_execute_successful(self):
        mock_prov = MockAIProvider("test-prov")
        self.provider_reg.register_ai_provider(mock_prov)
        self.account_reg.register_account(
            Account(id="test-acct", provider_id="test-prov", account_name="test-acct", status=AccountStatus.ONLINE)
        )

        job = self.job_mgr.submit_job(
            task="Compute answer",
            provider="test-prov",
            account="test-acct",
            model="test-model",
        )
        self.assertEqual(job.status, "pending")

        executed_job = self.job_mgr.execute_job(job)
        self.assertEqual(executed_job.status, "completed")
        self.assertEqual(executed_job.result["output"], "Output from test-prov")
        self.assertEqual(mock_prov.call_count, 1)

    def test_explicit_failover_chain(self):
        # Primary fails -> Fallback succeeds
        failing_prov = MockAIProvider("failing-prov", will_fail=True, error_msg="Rate limit exceeded")
        backup_prov = MockAIProvider("backup-prov", will_fail=False)

        self.provider_reg.register_ai_provider(failing_prov)
        self.provider_reg.register_ai_provider(backup_prov)

        self.account_reg.register_account(
            Account(id="fail-acct", provider_id="failing-prov", account_name="fail-acct", status=AccountStatus.ONLINE)
        )
        self.account_reg.register_account(
            Account(id="backup-acct", provider_id="backup-prov", account_name="backup-acct", status=AccountStatus.ONLINE)
        )

        failover_chain = [{"provider": "backup-prov", "account": "backup-acct", "model": "backup-model"}]

        job = self.job_mgr.submit_job(
            task="Important query",
            provider="failing-prov",
            account="fail-acct",
            failover_chain=failover_chain,
        )

        executed_job = self.job_mgr.execute_job(job)
        self.assertEqual(executed_job.status, "completed")
        self.assertEqual(executed_job.provider, "backup-prov")
        self.assertEqual(executed_job.account, "backup-acct")
        self.assertEqual(executed_job.result["output"], "Output from backup-prov")
        self.assertEqual(executed_job.retry_count, 1)

    def test_audit_log_secret_redaction(self):
        from providers.registry.credential_manager import SecretRedactor
        self.job_mgr.log_audit("AUTH_EVENT", {"api_key": "sk-proj-secret12345", "user": "alice"})
        logs = self.job_mgr.get_audit_log(limit=1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["details"]["api_key"], SecretRedactor.REDACTION_TOKEN)
        self.assertEqual(logs[0]["details"]["user"], "alice")


if __name__ == "__main__":
    unittest.main()
