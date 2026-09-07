"""Session and Conversation Mapping Manager.

Tracks and persists the relationship between:
Brain Task ──▶ Agent Session ──▶ Antigravity Conversation ID

Enables seamless cross-task session continuation via the CLI's --conversation flag.
"""
from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SessionRecord:
    task_id: str
    agent_id: str
    session_id: str
    conversation_id: str | None = None
    model: str | None = None
    created_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        return cls(**data)


class SessionManager:
    """Persistent manager for task-to-conversation mapping."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self._file = registry_file or (Path(__file__).resolve().parent / "session_registry.json")
        self._sessions: dict[str, SessionRecord] = {}
        self._load()

    def _load(self) -> None:
        if self._file.is_file():
            try:
                data = json.loads(self._file.read_text(encoding="utf-8"))
                for s_id, s_data in data.items():
                    self._sessions[s_id] = SessionRecord.from_dict(s_data)
            except Exception:
                self._sessions = {}

    def _save(self) -> None:
        try:
            data = {s_id: s.to_dict() for s_id, s in self._sessions.items()}
            self._file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def record_session(
        self,
        task_id: str,
        agent_id: str,
        conversation_id: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        s_id = session_id or f"sess-{uuid.uuid4().hex[:8]}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if s_id in self._sessions:
            rec = self._sessions[s_id]
            rec.conversation_id = conversation_id or rec.conversation_id
            rec.model = model or rec.model
            rec.updated_at = now
            if metadata:
                rec.metadata.update(metadata)
        else:
            rec = SessionRecord(
                task_id=task_id,
                agent_id=agent_id,
                session_id=s_id,
                conversation_id=conversation_id,
                model=model,
                created_at=now,
                updated_at=now,
                metadata=metadata or {},
            )
            self._sessions[s_id] = rec

        self._save()
        return rec

    def get_by_task(self, task_id: str) -> SessionRecord | None:
        for s in self._sessions.values():
            if s.task_id == task_id:
                return s
        return None

    def get_by_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def get_latest_for_agent(self, agent_id: str) -> SessionRecord | None:
        matching = [s for s in self._sessions.values() if s.agent_id == agent_id]
        if not matching:
            return None
        return sorted(matching, key=lambda s: s.updated_at, reverse=True)[0]

    def list_recent(self, limit: int = 50) -> list[SessionRecord]:
        return sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)[:limit]
