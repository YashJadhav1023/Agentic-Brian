"""Structured Agent-to-Agent Handoff Manager.

Produces, validates, and archives standardized Picoschema markdown handoff records.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HandoffRecord:
    task: str
    objective: str
    completed: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    relevant_memory: list[str] = field(default_factory=list)
    git_state: str = "clean"
    remaining_work: list[str] = field(default_factory=list)
    next_action: str = ""
    recommended_agent: str = "antigravity-account-1"
    recommended_model: str = "auto"
    created_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

    def to_markdown(self) -> str:
        lines = [
            f"# Handoff: {self.task}",
            f"**Date:** {self.created_at}",
            f"**Objective:** {self.objective}",
            f"**Git State:** {self.git_state}",
            f"**Recommended Agent:** `{self.recommended_agent}`",
            f"**Recommended Model:** `{self.recommended_model}`",
            "",
            "## Completed Work",
        ]
        for c in (self.completed or ["None reported"]):
            lines.append(f"- {c}")

        lines.extend(["", "## Files Modified"])
        for f in (self.files_modified or ["None"]):
            lines.append(f"- {f}")

        lines.extend(["", "## Tests Run"])
        for t in (self.tests or ["None"]):
            lines.append(f"- {t}")

        lines.extend(["", "## Errors Encountered"])
        for e in (self.errors or ["None"]):
            lines.append(f"- {e}")

        lines.extend(["", "## Decisions Made"])
        for d in (self.decisions or ["None"]):
            lines.append(f"- {d}")

        lines.extend(["", "## Remaining Work"])
        for r in (self.remaining_work or ["None"]):
            lines.append(f"- {r}")

        lines.extend(["", f"## Next Action\n{self.next_action}"])
        return "\n".join(lines)


class HandoffManager:
    """Manages reading, writing, and archiving handoff records."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self._root = root_dir or Path(__file__).resolve().parent
        self._root.mkdir(parents=True, exist_ok=True)
        self._current_file = self._root / "current.md"
        self._archive_dir = self._root / "archive"
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    def write_handoff(self, record: HandoffRecord) -> Path:
        # If current exists, archive it
        if self._current_file.exists():
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
            archive_path = self._archive_dir / f"handoff_{timestamp}.md"
            self._current_file.rename(archive_path)

        md = record.to_markdown()
        self._current_file.write_text(md, encoding="utf-8")
        return self._current_file

    def get_current_handoff(self) -> str | None:
        if self._current_file.exists():
            return self._current_file.read_text(encoding="utf-8")
        return None
