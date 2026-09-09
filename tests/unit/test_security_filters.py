"""Unit tests for Phase 15 Security Filters & Sanitization."""
import unittest

from brain.knowledge.document_registry import DocumentRegistry, PATH_DENY_LIST
from providers.registry.credential_manager import SecretRedactor


class TestSecurityFilters(unittest.TestCase):

    def test_path_deny_list_covers_critical_patterns(self):
        self.assertIn(".git", PATH_DENY_LIST)
        self.assertIn(".gemini", PATH_DENY_LIST)
        self.assertIn(".ssh", PATH_DENY_LIST)
        self.assertIn("credentials", PATH_DENY_LIST)
        self.assertIn("secrets", PATH_DENY_LIST)
        self.assertIn("tokens", PATH_DENY_LIST)

    def test_document_redaction(self):
        reg = DocumentRegistry(knowledge_roots=[])
        # Test sanitization logic directly
        raw_text = "Here is my secret sk-abcdef123456789012345678 and token ghp_123456789012345678901234567890123456"
        redactor = SecretRedactor()
        redacted = redactor.redact(raw_text)
        self.assertNotIn("sk-abcdef123456789012345678", redacted)
        self.assertNotIn("ghp_123456789012345678901234567890123456", redacted)


if __name__ == "__main__":
    unittest.main()
