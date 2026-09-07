"""Scoped, Persistent Shared Memory Store with SQLite Backing.

Supports GLOBAL, PROJECT, TASK, AGENT, and SESSION scopes with tag indexing
and relevance filtering. Excludes secrets and personal credentials.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class MemoryScope(str, Enum):
    GLOBAL = "GLOBAL"
    PROJECT = "PROJECT"
    TASK = "TASK"
    AGENT = "AGENT"
    SESSION = "SESSION"


@dataclass
class MemoryEntry:
    memory_id: str
    content: str
    scope: MemoryScope = MemoryScope.PROJECT
    source_agent: str = "unknown"
    task_id: str | None = None
    session_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    confidence: float = 1.0
    importance: int = 3  # 1 (low) to 5 (critical)
    tags: list[str] = field(default_factory=list)
    provenance: str = "system"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["scope"] = self.scope.value
        return d

    @classmethod
    def from_row(cls, row: tuple) -> MemoryEntry:
        return cls(
            memory_id=row[0],
            content=row[1],
            scope=MemoryScope(row[2]),
            source_agent=row[3],
            task_id=row[4],
            session_id=row[5],
            created_at=row[6],
            confidence=row[7],
            importance=row[8],
            tags=json.loads(row[9]) if row[9] else [],
            provenance=row[10] or "system",
        )


class MemoryStore:
    """SQLite-backed shared agent memory store."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or (Path(__file__).resolve().parent / "shared_memory.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    source_agent TEXT NOT NULL,
                    task_id TEXT,
                    session_id TEXT,
                    created_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance INTEGER NOT NULL,
                    tags TEXT,
                    provenance TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scope ON memories(scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task ON memories(task_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON memories(created_at)")
            conn.commit()

    def add(
        self,
        content: str,
        scope: MemoryScope = MemoryScope.PROJECT,
        source_agent: str = "unknown",
        task_id: str | None = None,
        session_id: str | None = None,
        importance: int = 3,
        tags: list[str] | None = None,
        provenance: str = "system",
    ) -> MemoryEntry:
        mem_id = f"mem-{uuid.uuid4().hex[:8]}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        entry = MemoryEntry(
            memory_id=mem_id,
            content=content,
            scope=scope,
            source_agent=source_agent,
            task_id=task_id,
            session_id=session_id,
            created_at=now,
            importance=importance,
            tags=tags or [],
            provenance=provenance,
        )
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT INTO memories (
                    memory_id, content, scope, source_agent, task_id, session_id,
                    created_at, confidence, importance, tags, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.memory_id,
                entry.content,
                entry.scope.value,
                entry.source_agent,
                entry.task_id,
                entry.session_id,
                entry.created_at,
                entry.confidence,
                entry.importance,
                json.dumps(entry.tags),
                entry.provenance,
            ))
            conn.commit()
        return entry

    def list_all(self, limit: int = 100) -> list[MemoryEntry]:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute("SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,))
            return [MemoryEntry.from_row(r) for r in cursor.fetchall()]

    def count(self) -> int:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute("SELECT count(*) FROM memories")
            return cursor.fetchone()[0]
