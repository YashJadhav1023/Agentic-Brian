"""Unit tests for OpenAICompatibleProvider and LiteLLMGatewayProvider."""
import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from brain.orchestrator.job import Job
from providers.api.litellm_gateway import LiteLLMGatewayProvider
from providers.api.openai_compatible import OpenAICompatibleProvider
from providers.base import ProviderType


class TestOpenAICompatibleProvider(unittest.TestCase):
    def test_provider_initialization_and_type(self):
        p1 = OpenAICompatibleProvider(
            provider_id="my-openai",
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o",
            models=["gpt-4o", "gpt-4o-mini"],
        )
        self.assertEqual(p1.provider_id, "my-openai")
        self.assertEqual(p1.provider_type, ProviderType.API)

        # Local Ollama endpoint
        p_ollama = OpenAICompatibleProvider(
            provider_id="ollama-local",
            base_url="http://localhost:11434/v1",
            default_model="llama3",
        )
        self.assertEqual(p_ollama.provider_type, ProviderType.LOCAL_MODEL)

        # OpenRouter endpoint
        p_router = OpenAICompatibleProvider(
            provider_id="openrouter",
            base_url="https://openrouter.ai/api/v1",
            default_model="anthropic/claude-3.5-sonnet",
        )
        self.assertEqual(p_router.provider_type, ProviderType.GATEWAY)

    @patch("urllib.request.urlopen")
    def test_successful_execution(self, mock_urlopen):
        mock_response_data = {
            "id": "chatcmpl-123",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hello! I am an AI assistant.",
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            },
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        provider = OpenAICompatibleProvider(
            provider_id="mock-openai",
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o",
        )

        job = Job(id="job-1", task="Hello", model="gpt-4o")
        result = provider.execute(job)

        self.assertTrue(result.success)
        self.assertEqual(result.output, "Hello! I am an AI assistant.")
        self.assertEqual(result.total_tokens, 20)
        self.assertEqual(result.actual_model, "gpt-4o")

    @patch("urllib.request.urlopen")
    def test_rate_limited_error(self, mock_urlopen):
        # Simulate HTTP 429 Rate Limit
        err = urllib.error.HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "10"},
            fp=io.BytesIO(b'{"error": {"message": "Rate limit reached"}}'),
        )
        mock_urlopen.side_effect = err

        provider = OpenAICompatibleProvider(
            provider_id="mock-openai",
            base_url="https://api.openai.com/v1",
        )
        job = Job(id="job-rate-limit", task="Heavy request")
        result = provider.execute(job)

        self.assertFalse(result.success)
        self.assertIn("429", result.error)
        self.assertIn("Rate limit", result.error)

    @patch("urllib.request.urlopen")
    def test_streaming_execution(self, mock_urlopen):
        sse_lines = [
            b'data: {"choices": [{"delta": {"content": "Chunk 1 "}}]}\n',
            b"\n",
            b'data: {"choices": [{"delta": {"content": "Chunk 2"}}]}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(sse_lines)
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        events = []
        provider = OpenAICompatibleProvider(
            provider_id="mock-stream",
            base_url="https://api.openai.com/v1",
        )
        job = Job(id="job-stream", task="Stream test")
        result = provider.execute_stream(job, on_event=lambda e: events.append(e))

        self.assertTrue(result.success)
        self.assertEqual(result.output, "Chunk 1 Chunk 2")
        self.assertEqual(len(events), 2)

    def test_litellm_gateway_provider(self):
        gateway = LiteLLMGatewayProvider(
            provider_id="litellm",
            base_url="http://localhost:4000",
            default_model="gpt-4o",
            models=["gpt-4o", "claude-3-5-sonnet"],
        )
        self.assertEqual(gateway.provider_id, "litellm")
        self.assertEqual(gateway.provider_type, ProviderType.GATEWAY)
        self.assertEqual(gateway.default_model, "gpt-4o")
        self.assertIn("claude-3-5-sonnet", gateway.models)


if __name__ == "__main__":
    unittest.main()
