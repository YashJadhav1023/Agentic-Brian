"""Unit tests for Universal AI Provider Abstraction."""
import unittest
from unittest.mock import MagicMock

from agents.base.adapter import AgentAdapter, Capability, ExecutionMode, TaskExecutionResult
from brain.orchestrator.job import Job
from providers.adapters.bridge import AgentProviderBridge
from providers.base import AIProvider, ProviderType, RateLimitInfo, UsageStats
from providers.registry.model_registry import ModelMetadata


class DummyAIProvider(AIProvider):
    @property
    def provider_id(self) -> str:
        return "dummy-provider"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.API

    def capabilities(self) -> frozenset[Capability]:
        return frozenset([Capability.DEEP_REASONING, Capability.ARCHITECTURE])

    def health(self) -> tuple[bool, str]:
        return True, "Dummy provider healthy"

    def list_models(self) -> list[ModelMetadata]:
        return [
            ModelMetadata(
                model_id="dummy-v1",
                provider_id=self.provider_id,
                display_name="Dummy V1",
                capabilities=frozenset(["deep-reasoning"]),
            )
        ]

    def execute(self, job: Job) -> TaskExecutionResult:
        return TaskExecutionResult(
            task_id=job.id,
            agent_id=job.account or "dummy",
            account_id=job.account or "dummy",
            provider=self.provider_id,
            success=True,
            exit_code=0,
            output="dummy output",
            error="",
            actual_model=job.model or "dummy-v1",
        )

    def validate_config(self) -> tuple[bool, list[str]]:
        return True, []


class TestProviderAbstraction(unittest.TestCase):
    def test_provider_type_enum(self):
        self.assertEqual(ProviderType.AGENT.value, "agent")
        self.assertEqual(ProviderType.IDE.value, "ide")
        self.assertEqual(ProviderType.API.value, "api")
        self.assertEqual(ProviderType.LOCAL_MODEL.value, "local_model")
        self.assertEqual(ProviderType.GATEWAY.value, "gateway")

    def test_usage_stats_and_rate_limit(self):
        stats = UsageStats(request_count=5, input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_usd=0.002)
        d = stats.to_dict()
        self.assertEqual(d["request_count"], 5)
        self.assertEqual(d["total_tokens"], 150)
        self.assertEqual(d["estimated_cost_usd"], 0.002)

        limits = RateLimitInfo(requests_per_minute=60, requests_remaining=55)
        ld = limits.to_dict()
        self.assertEqual(ld["requests_per_minute"], 60)
        self.assertEqual(ld["requests_remaining"], 55)

    def test_dummy_provider_contract(self):
        prov = DummyAIProvider()
        self.assertEqual(prov.provider_id, "dummy-provider")
        self.assertEqual(prov.provider_type, ProviderType.API)
        self.assertIn(Capability.DEEP_REASONING, prov.capabilities())
        ok, reason = prov.health()
        self.assertTrue(ok)
        self.assertIn("Dummy provider healthy", reason)
        models = prov.list_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].model_id, "dummy-v1")

        job = Job(id="test-job-1", task="Say hello", model="dummy-v1")
        res = prov.execute(job)
        self.assertTrue(res.success)
        self.assertEqual(res.output, "dummy output")

    def test_agent_provider_bridge(self):
        mock_adapter = MagicMock()
        mock_adapter.provider = "antigravity"
        mock_adapter.provider_id = "antigravity"
        mock_adapter.agent_id = "antigravity-account-1"
        mock_adapter.account_id = "account-1"
        mock_adapter.execution_mode = ExecutionMode.HEADLESS
        mock_adapter.capabilities.return_value = frozenset([Capability.CODE_REVIEW])
        mock_adapter.health.return_value = (True, "Bridge OK")
        mock_adapter.available_models.return_value = ("gemini-3.8-flash",)
        mock_adapter.execute.return_value = TaskExecutionResult(
            task_id="job-bridge-1",
            agent_id="antigravity-account-1",
            account_id="account-1",
            provider="antigravity",
            success=True,
            exit_code=0,
            output="Bridge output",
            error="",
        )

        bridge = AgentProviderBridge(mock_adapter)
        self.assertEqual(bridge.provider_id, "antigravity")
        self.assertEqual(bridge.provider_type, ProviderType.IDE)
        self.assertTrue(bridge.health()[0])
        self.assertEqual(len(bridge.list_models()), 1)

        job = Job(id="job-bridge-1", task="Test prompt", model="gemini-3.8-flash")
        res = bridge.execute(job)
        self.assertTrue(res.success)
        self.assertEqual(res.output, "Bridge output")


if __name__ == "__main__":
    unittest.main()
