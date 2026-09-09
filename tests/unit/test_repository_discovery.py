"""Unit tests for Git repository discovery."""
import unittest

from brain.resources.repo_registry import RepositoryRegistry


class TestRepositoryDiscovery(unittest.TestCase):

    def setUp(self):
        self.registry = RepositoryRegistry()
        self.repos = self.registry.discover()

    def test_current_repo_discovered(self):
        names = [r.name for r in self.repos]
        self.assertIn("Agentic_shared_memory", names)

    def test_branch_and_clean_status(self):
        repo = self.registry.get_repo("Agentic_shared_memory")
        self.assertIsNotNone(repo)
        self.assertIsInstance(repo.branch, str)
        self.assertIsInstance(repo.clean, bool)

    def test_remotes_contain_no_secrets(self):
        for repo in self.repos:
            for rem in repo.remotes:
                # Remotes should be names ('origin'), not raw credential URLs
                self.assertNotIn("http://", rem)
                self.assertNotIn("https://", rem)
                self.assertNotIn("@", rem)


if __name__ == "__main__":
    unittest.main()
