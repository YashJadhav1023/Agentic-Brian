"""Adversarial Challenge Test Suite for Phase 23 Milestone 2: ECC Normalizer.

Authored by: Challenger 1 (teamwork_preview_challenger_m2_1)
Objective: Empirically stress-test ECCComponentNormalizer edge cases, boundary conditions,
failure modes, symlink escapes, extension anomalies, Category D rejection enforcement,
and provenance integrity.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from brain.resources.ecc_normalizer import (
    ECCCategory,
    ECCComponentNormalizer,
    NormalizedECCComponent,
    compute_provenance_hash,
    parse_yaml_frontmatter,
)
from brain.resources.resource_model import (
    PermissionLevel,
    Resource,
    ResourceLifecycleState,
    ResourceType,
    TrustLevel,
)


class TestPhase23ECCChallenge(unittest.TestCase):
    """Adversarial challenge test suite for ECC normalizer."""

    # ----------------------------------------------------------------------
    # 1. Empty Markdown Files & Malformed YAML Frontmatter
    # ----------------------------------------------------------------------
    def test_01_empty_markdown_files_handling(self) -> None:
        """Verifies normalizer behavior on completely empty (0-byte) and whitespace-only files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            agents_dir = root / "agents"
            agents_dir.mkdir()
            (agents_dir / "zero_byte.md").touch()
            (agents_dir / "whitespace_only.md").write_text("   \n\n\t  \n")

            norm = ECCComponentNormalizer(ecc_root=str(root))
            agents = norm.discover_agents()
            self.assertEqual(len(agents), 2)

            for a in agents:
                # Should not crash, description empty string
                self.assertEqual(a.description, "")
                # 0-byte file hash is sha256 of empty string
                if a.name == "zero_byte":
                    self.assertEqual(
                        a.provenance_hash,
                        hashlib.sha256(b"").hexdigest()
                    )

    def test_02_malformed_yaml_frontmatter_syntax(self) -> None:
        """Verifies parser resilience against syntax errors, non-dict objects, and edge cases."""
        # Unclosed frontmatter (missing closing ---)
        self.assertEqual(parse_yaml_frontmatter("---\nname: unclosed\n"), {})

        # Non-dict YAML frontmatter: scalar
        self.assertEqual(parse_yaml_frontmatter("---\njust a string\n---\n# Body"), {})

        # Non-dict YAML frontmatter: list
        self.assertEqual(parse_yaml_frontmatter("---\n- item 1\n- item 2\n---\n# Body"), {})

        # Non-dict YAML frontmatter: number
        self.assertEqual(parse_yaml_frontmatter("---\n42\n---\n# Body"), {})

        # Duplicate keys: last key should win without crashing
        res = parse_yaml_frontmatter("---\nname: first\nname: second\n---\n")
        self.assertEqual(res.get("name"), "second")

        # Unbalanced quotes: should not crash
        res_quote = parse_yaml_frontmatter('---\nname: "unclosed\n---\n')
        self.assertEqual(res_quote.get("name"), '"unclosed')

        # Colons in value without quotes: should not crash
        res_colon = parse_yaml_frontmatter("---\nname: foo: bar: baz\n---\n")
        self.assertEqual(res_colon.get("name"), "foo: bar: baz")

        # Tab indentation: should be parsed into nested dict
        res_tab = parse_yaml_frontmatter("---\nkey:\n\tsubkey: val\n---\n")
        self.assertIn("key", res_tab)
        self.assertEqual(res_tab["key"].get("subkey"), "val")

    # ----------------------------------------------------------------------
    # 2. Path Traversal Attempts & Symlink Escapes
    # ----------------------------------------------------------------------
    def test_03_symlink_escape_demonstration(self) -> None:
        """Empirically demonstrates that symlinks escaping ecc_root are followed and ingested.
        
        VULNERABILITY SURFACE:
        ECCComponentNormalizer does not check Path.is_symlink() or Path.resolve(),
        allowing arbitrary files outside the repository boundary to be read and ingested.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            agents_dir = root / "agents"
            agents_dir.mkdir()

            # Create an external target file
            external_file = root.parent / "secret_host_data.txt"
            external_file.write_text("HOST_SECRET_DATA=super_confidential_12345\n")

            try:
                symlink_agent = agents_dir / "host_data_leak.md"
                symlink_agent.symlink_to(external_file)

                norm = ECCComponentNormalizer(ecc_root=str(root))
                agents = norm.discover_agents()
                self.assertEqual(len(agents), 1)

                leaked_agent = agents[0]
                # Empirically verify external file was read and contents leaked into description
                self.assertIn("HOST_SECRET_DATA", leaked_agent.description)
                # Critically, it was assigned Category A (HIGH trust)
                self.assertEqual(leaked_agent.category, ECCCategory.A)
                self.assertEqual(leaked_agent.trust_level, TrustLevel.HIGH)

                res = leaked_agent.to_resource()
                self.assertEqual(res.lifecycle_state, ResourceLifecycleState.ENABLED)
                self.assertTrue(res.availability)
            finally:
                if external_file.exists():
                    external_file.unlink()

    # ----------------------------------------------------------------------
    # 3. Missing or Unusual File Extensions
    # ----------------------------------------------------------------------
    def test_04_unusual_extensions_and_case_sensitivity(self) -> None:
        """Verifies behavior on unusual extensions, uppercase, and non-markdown files."""
        norm = ECCComponentNormalizer()

        # Uppercase .SH and .PS1 are handled because .lower() is called
        rej_sh, _ = norm.is_strict_rejection("script", "deploy.SH", "/path/deploy.SH")
        self.assertTrue(rej_sh)

        rej_ps1, _ = norm.is_strict_rejection("script", "deploy.PS1", "/path/deploy.PS1")
        self.assertTrue(rej_ps1)

        # But double extensions where .sh is not at the end fail to match endswith:
        # e.g., script.sh.txt, install.ps1.bak
        rej_dbl_cmd, _ = norm.is_strict_rejection("command", "script.sh.txt", "/path/script.sh.txt")
        self.assertFalse(rej_dbl_cmd)

        rej_dbl_skill, _ = norm.is_strict_rejection("skill", "install.ps1.bak", "/path/install.ps1.bak")
        self.assertFalse(rej_dbl_skill)

    # ----------------------------------------------------------------------
    # 4. Rejection Enforcement for .sh, .ps1, setup, install
    # ----------------------------------------------------------------------
    def test_05_rejection_failure_modes_setup_and_install(self) -> None:
        """Empirically demonstrates failure modes where components containing 'setup' or 'install'
        are NOT rejected as Category D.
        
        FAILURE MODES IDENTIFIED:
        1. Commands named 'install' or 'setup' become Category C, not Category D.
        2. Skills named 'install' or 'setup' become Category B, not Category D.
        3. Agents named 'install.sh.md' or 'setup.md' become Category A, not Category D.
        4. Rules named 'install.md' or 'setup.sh.md' become Category A, not Category D.
        5. Documentation named 'install.md' or 'setup.sh.md' become Category A, not Category D.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Agents
            agents_dir = root / "agents"
            agents_dir.mkdir()
            (agents_dir / "install.sh.md").write_text("# Install Agent")
            (agents_dir / "setup.md").write_text("# Setup Agent")

            # Skills
            skills_dir = root / "skills"
            (skills_dir / "install").mkdir(parents=True)
            (skills_dir / "install" / "SKILL.md").write_text("# Install Skill")
            (skills_dir / "setup").mkdir(parents=True)
            (skills_dir / "setup" / "SKILL.md").write_text("# Setup Skill")

            # Commands
            commands_dir = root / "commands"
            commands_dir.mkdir(parents=True)
            (commands_dir / "install.md").write_text("# /install")
            (commands_dir / "setup.md").write_text("# /setup")

            norm = ECCComponentNormalizer(ecc_root=str(root))

            # Empirical Verification: Agents with install.sh / setup are NOT rejected as Category D
            agents = {a.id: a for a in norm.discover_agents()}
            self.assertIn("ecc:agent:install.sh", agents)
            self.assertNotEqual(agents["ecc:agent:install.sh"].category, ECCCategory.D)
            self.assertEqual(agents["ecc:agent:install.sh"].category, ECCCategory.A)

            self.assertIn("ecc:agent:setup", agents)
            self.assertNotEqual(agents["ecc:agent:setup"].category, ECCCategory.D)
            self.assertEqual(agents["ecc:agent:setup"].category, ECCCategory.A)

            # Empirical Verification: Skills with install / setup are NOT rejected as Category D
            skills = {s.id: s for s in norm.discover_skills()}
            self.assertIn("ecc:skill:install", skills)
            self.assertNotEqual(skills["ecc:skill:install"].category, ECCCategory.D)
            self.assertEqual(skills["ecc:skill:install"].category, ECCCategory.B)

            # Empirical Verification: Commands with install / setup are NOT rejected as Category D
            commands = {c.id: c for c in norm.discover_commands()}
            self.assertIn("ecc:command:install", commands)
            self.assertNotEqual(commands["ecc:command:install"].category, ECCCategory.D)
            self.assertEqual(commands["ecc:command:install"].category, ECCCategory.C)

    def test_06_real_repo_embedded_scripts_not_rejected(self) -> None:
        """Empirically demonstrates that skills in external/ecc/ containing embedded .sh scripts
        are categorized as Category A (LOW_RISK, ENABLED) rather than Category D.
        """
        norm = ECCComponentNormalizer()
        skills = {s.id.split(":")[-1]: s for s in norm.discover_skills()}

        # These skills in external/ecc contain real .sh scripts
        skills_with_scripts = [
            "continuous-learning-v2",
            "skill-stocktake",
            "rules-distill",
            "openclaw-persona-forge",
            "ios-icon-gen",
            "frontend-slides",
            "continuous-learning",
        ]

        for s_name in skills_with_scripts:
            if s_name in skills:
                skill = skills[s_name]
                # Empirically verify that they are Category A instead of Category D
                self.assertEqual(
                    skill.category,
                    ECCCategory.A,
                    f"Skill {s_name} containing shell scripts was expected to be Category A due to normalizer gap"
                )
                self.assertEqual(skill.risk_level, "LOW_RISK")
                self.assertEqual(skill.rejection_reason, "")

    # ----------------------------------------------------------------------
    # 5. Provenance Hash Sensitivity & Determinism
    # ----------------------------------------------------------------------
    def test_07_provenance_hash_single_byte_mutation(self) -> None:
        """Empirically verifies that altering a single byte in any input changes the SHA-256 digest."""
        # 1. File byte mutation
        with tempfile.NamedTemporaryFile("wb", delete=False) as f1:
            f1.write(b"A" * 1024)
            p1 = Path(f1.name)
        with tempfile.NamedTemporaryFile("wb", delete=False) as f2:
            f2.write(b"A" * 1023 + b"B")  # 1 byte flipped
            p2 = Path(f2.name)

        try:
            h1 = compute_provenance_hash(p1)
            h2 = compute_provenance_hash(p2)
            self.assertNotEqual(h1, h2)
            self.assertEqual(len(h1), 64)
            self.assertEqual(len(h2), 64)
        finally:
            p1.unlink()
            p2.unlink()

        # 2. String 1-character mutation
        s_orig = "export API_KEY=abcdef12345"
        s_mut = "export API_KEY=abcdef12346"
        self.assertNotEqual(
            compute_provenance_hash(s_orig),
            compute_provenance_hash(s_mut)
        )

        # 3. Dict single-value mutation
        d_orig = {"tool": "bash", "timeout": 30}
        d_mut = {"tool": "bash", "timeout": 31}
        self.assertNotEqual(
            compute_provenance_hash(d_orig),
            compute_provenance_hash(d_mut)
        )

        # 4. Dict key mutation
        d_key_mut = {"t00l": "bash", "timeout": 30}
        self.assertNotEqual(
            compute_provenance_hash(d_orig),
            compute_provenance_hash(d_key_mut)
        )

        # 5. Dict key ordering invariance (canonical JSON sorting)
        d_ord1 = {"z": 1, "a": 2, "m": {"y": 10, "x": 20}}
        d_ord2 = {"a": 2, "z": 1, "m": {"x": 20, "y": 10}}
        self.assertEqual(
            compute_provenance_hash(d_ord1),
            compute_provenance_hash(d_ord2)
        )


if __name__ == "__main__":
    unittest.main()
