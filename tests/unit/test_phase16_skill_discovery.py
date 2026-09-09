"""Unit tests for Phase 16: Skill Discovery Engine."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from brain.resources.resource_model import ResourceType
from brain.resources.skill_discovery import SkillDiscoveryEngine


class TestPhase16SkillDiscovery(unittest.TestCase):
    def test_parse_skill_file_with_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(
                "---\nname: Azure Deploy Skill\ndescription: Automated deployment to Azure\n---\n\n# Content\nSome instructions.",
                encoding="utf-8"
            )

            engine = SkillDiscoveryEngine(search_roots=[tmpdir])
            skills = engine.discover()
            self.assertEqual(len(skills), 1)
            s = skills[0]
            self.assertEqual(s.name, "Azure Deploy Skill")
            self.assertIn("Azure", s.description)
            self.assertIn("azure", s.capabilities)

            res = s.to_resource()
            self.assertEqual(res.type, ResourceType.SKILL)
            self.assertTrue(res.read_only)

    def test_live_skill_discovery(self):
        engine = SkillDiscoveryEngine()
        skills = engine.discover()
        # System has plugin skills in ~/.gemini/config/plugins
        self.assertGreaterEqual(len(skills), 5)
        for s in skills[:5]:
            self.assertTrue(len(s.name) > 0)
            self.assertTrue(Path(s.location).exists())


if __name__ == "__main__":
    unittest.main()
