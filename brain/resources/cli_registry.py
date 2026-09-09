"""System CLI Tool Discovery and Registry for Mission Control.

Safely detects available system command-line tools without executing arbitrary commands.
Maps discovered CLI utilities to capabilities and safety risk profiles.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Pre-defined approved CLI tools to probe
APPROVED_CLI_TOOLS: Dict[str, Dict[str, Any]] = {
    "git": {
        "capabilities": ["git", "version-control", "diff", "branch", "commit", "worktree"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "az": {
        "capabilities": ["azure", "cloud", "deployment", "infrastructure", "aks", "storage"],
        "risk": "HIGH_RISK_WRITE",
        "version_flag": "--version"
    },
    "terraform": {
        "capabilities": ["terraform", "iac", "infrastructure", "cloud", "deployment"],
        "risk": "HIGH_RISK_WRITE",
        "version_flag": "-version"
    },
    "kubectl": {
        "capabilities": ["kubernetes", "k8s", "aks", "cluster", "pods", "deployments"],
        "risk": "HIGH_RISK_WRITE",
        "version_flag": "version --client"
    },
    "docker": {
        "capabilities": ["docker", "containers", "build", "image"],
        "risk": "HIGH_RISK_WRITE",
        "version_flag": "--version"
    },
    "helm": {
        "capabilities": ["helm", "kubernetes", "charts", "deployment"],
        "risk": "HIGH_RISK_WRITE",
        "version_flag": "version"
    },
    "aws": {
        "capabilities": ["aws", "cloud", "s3", "ec2", "iam"],
        "risk": "HIGH_RISK_WRITE",
        "version_flag": "--version"
    },
    "agy": {
        "capabilities": ["antigravity", "agent", "coding", "architecture", "reasoning"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "cline": {
        "capabilities": ["cline", "agent", "coding", "refactoring", "frontend"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "kiro": {
        "capabilities": ["kiro", "agent", "terminal", "cloud-read"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "python3": {
        "capabilities": ["python", "scripting", "backend", "testing"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "node": {
        "capabilities": ["node", "javascript", "typescript", "npm", "frontend"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "npm": {
        "capabilities": ["npm", "package-manager", "javascript", "dependencies"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "pnpm": {
        "capabilities": ["pnpm", "package-manager", "javascript"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "curl": {
        "capabilities": ["curl", "http", "api", "network", "download"],
        "risk": "READ_ONLY",
        "version_flag": "--version"
    },
    "make": {
        "capabilities": ["make", "build", "compilation", "tasks"],
        "risk": "LOW_RISK_WRITE",
        "version_flag": "--version"
    },
    "ollama": {
        "capabilities": ["ollama", "local-models", "llm", "inference"],
        "risk": "READ_ONLY",
        "version_flag": "--version"
    }
}


@dataclass
class CLICommand:
    """Represents a discovered command-line tool."""
    name: str
    path: str
    version: str = "unknown"
    capabilities: List[str] = field(default_factory=list)
    risk: str = "LOW_RISK_WRITE"
    available: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CLIRegistry:
    """Discovers and inspects approved system CLI tools."""

    def __init__(self) -> None:
        self._commands: Dict[str, CLICommand] = {}

    def discover(self) -> List[CLICommand]:
        """Probe the system for approved CLI utilities."""
        discovered: Dict[str, CLICommand] = {}

        for tool_name, meta in APPROVED_CLI_TOOLS.items():
            tool_path = shutil.which(tool_name)
            if not tool_path:
                discovered[tool_name] = CLICommand(
                    name=tool_name,
                    path="",
                    version="not installed",
                    capabilities=meta["capabilities"],
                    risk=meta["risk"],
                    available=False
                )
                continue

            # Query version safely
            ver_str = "available"
            v_flag = meta.get("version_flag", "--version")
            cmd_args = [tool_path] + v_flag.split()
            try:
                res = subprocess.run(
                    cmd_args,
                    capture_output=True,
                    text=True,
                    timeout=2.0
                )
                out = (res.stdout or res.stderr).strip().split("\n")[0]
                if out:
                    ver_str = out[:60]
            except Exception:
                pass

            discovered[tool_name] = CLICommand(
                name=tool_name,
                path=tool_path,
                version=ver_str,
                capabilities=meta["capabilities"],
                risk=meta["risk"],
                available=True
            )

        self._commands = discovered
        logger.info("CLIRegistry discovered %d tools (%d available)",
                    len(self._commands), sum(1 for c in self._commands.values() if c.available))
        return list(self._commands.values())

    def get_command(self, name: str) -> Optional[CLICommand]:
        """Get a specific CLI command metadata."""
        return self._commands.get(name.lower())

    def list_commands(self, available_only: bool = True) -> List[CLICommand]:
        """List all registered CLI commands."""
        cmds = list(self._commands.values())
        if available_only:
            cmds = [c for c in cmds if c.available]
        return cmds

    def find_relevant_commands(self, task: str) -> List[CLICommand]:
        """Find available CLI commands matching task keywords or capabilities."""
        terms = set(re.findall(r"\b[a-z0-9_-]{2,}\b", task.lower()))
        matched: List[CLICommand] = []

        for cmd in self._commands.values():
            if not cmd.available:
                continue

            # Check direct name match
            if cmd.name.lower() in terms:
                matched.append(cmd)
                continue

            # Check capability overlap
            caps = set(c.lower() for c in cmd.capabilities)
            if terms.intersection(caps):
                matched.append(cmd)

        return matched
