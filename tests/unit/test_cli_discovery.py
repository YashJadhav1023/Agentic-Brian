"""Unit tests for system CLI tool discovery."""
import unittest

from brain.resources.cli_registry import CLIRegistry


class TestCLIDiscovery(unittest.TestCase):

    def setUp(self):
        self.registry = CLIRegistry()
        self.tools = self.registry.discover()

    def test_python_and_git_detected(self):
        git_cmd = self.registry.get_command("git")
        self.assertIsNotNone(git_cmd)
        self.assertTrue(git_cmd.available)

        py_cmd = self.registry.get_command("python3")
        self.assertIsNotNone(py_cmd)
        self.assertTrue(py_cmd.available)

    def test_find_relevant_commands(self):
        matches = self.registry.find_relevant_commands("Commit changes to git branch")
        names = [m.name for m in matches]
        self.assertIn("git", names)

    def test_uninstalled_tool_safe(self):
        # A tool unlikely to exist should be marked not available
        cmd = self.registry.get_command("helm")
        if cmd and not cmd.available:
            self.assertFalse(cmd.available)
            self.assertEqual(cmd.version, "not installed")


if __name__ == "__main__":
    unittest.main()
