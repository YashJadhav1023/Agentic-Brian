"""Structured Append-Only Event Bus and Telemetry.

Records all system and agent state changes into an append-only JSONL log.
Supports specific telemetry events for Antigravity Account 2.
"""
from __future__ import annotations

import datetime
import json
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class EventType(str, Enum):
    # Core Agent Lifecycle
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_STOPPED = "AGENT_STOPPED"
    AGENT_HEARTBEAT = "AGENT_HEARTBEAT"
    AGENT_HEALTH = "AGENT_HEALTH"

    # Core Task Lifecycle
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_HANDOFF = "TASK_HANDOFF"

    # Context & Routing
    MEMORY_CREATED = "MEMORY_CREATED"
    MEMORY_ACCESSED = "MEMORY_ACCESSED"
    MODEL_SELECTED = "MODEL_SELECTED"
    MODEL_SWITCHED = "MODEL_SWITCHED"
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_FAILED = "TOOL_FAILED"
    FILE_LOCKED = "FILE_LOCKED"
    FILE_UNLOCKED = "FILE_UNLOCKED"
    GIT_EVENT = "GIT_EVENT"
    ERROR = "ERROR"

    # Dedicated Antigravity Account 2 Telemetry Events
    ANTIGRAVITY_ACCOUNT2_STARTED = "ANTIGRAVITY_ACCOUNT2_STARTED"
    ANTIGRAVITY_ACCOUNT2_COMPLETED = "ANTIGRAVITY_ACCOUNT2_COMPLETED"
    ANTIGRAVITY_ACCOUNT2_FAILED = "ANTIGRAVITY_ACCOUNT2_FAILED"
    ANTIGRAVITY_ACCOUNT2_HEALTH = "ANTIGRAVITY_ACCOUNT2_HEALTH"
    ANTIGRAVITY_ACCOUNT2_MODEL_SELECTED = "ANTIGRAVITY_ACCOUNT2_MODEL_SELECTED"
    ANTIGRAVITY_ACCOUNT2_HANDOFF = "ANTIGRAVITY_ACCOUNT2_HANDOFF"

    # Phase 3 Security & Audit Events
    AUTH_SUCCESS = "AUTH_SUCCESS"
    AUTH_FAILURE = "AUTH_FAILURE"
    TASK_EXECUTION_REQUESTED = "TASK_EXECUTION_REQUESTED"
    TASK_EXECUTION_STARTED = "TASK_EXECUTION_STARTED"
    TASK_EXECUTION_COMPLETED = "TASK_EXECUTION_COMPLETED"
    TASK_EXECUTION_FAILED = "TASK_EXECUTION_FAILED"
    PERMISSION_ESCALATION_REQUESTED = "PERMISSION_ESCALATION_REQUESTED"
    PERMISSION_ESCALATION_GRANTED = "PERMISSION_ESCALATION_GRANTED"
    PERMISSION_ESCALATION_DENIED = "PERMISSION_ESCALATION_DENIED"

    # Phase 3 Worktree & Sandbox Events
    WORKTREE_CREATED = "WORKTREE_CREATED"
    WORKTREE_DESTROYED = "WORKTREE_DESTROYED"
    DIFF_APPROVED = "DIFF_APPROVED"
    DIFF_REJECTED = "DIFF_REJECTED"
    MERGE_APPLIED = "MERGE_APPLIED"

    # Phase 4A Continuation & Termination Events
    CONTINUATION_STARTED = "CONTINUATION_STARTED"
    CONTINUATION_TERMINATED = "CONTINUATION_TERMINATED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    VERIFICATION_REJECTED = "VERIFICATION_REJECTED"
    RECURSION_PREVENTED = "RECURSION_PREVENTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DEPTH_LIMIT_REACHED = "DEPTH_LIMIT_REACHED"


@dataclass
class Event:
    event_type: EventType
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    agent_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


class EventBus:
    """Thread-safe event bus with persistent disk backing."""

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path = log_path or (Path(__file__).resolve().parent.parent / "runtime" / "logs" / "events.jsonl")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[Event], None]] = []

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def emit(self, event: Event) -> Event:
        line = json.dumps(event.to_dict()) + "\n"

        with self._lock:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line)
            for sub in self._subscribers:
                try:
                    sub(event)
                except Exception:
                    pass

        return event

    def publish(
        self,
        event_type: EventType,
        agent_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Event:
        evt = Event(
            event_type=event_type,
            agent_id=agent_id,
            task_id=task_id,
            session_id=session_id,
            metadata=metadata or {},
        )
        return self.emit(evt)

    def get_recent_events(self, limit: int = 50) -> list[Event]:
        if not self._log_path.exists():
            return []
        events = []
        with open(self._log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in reversed(lines[-limit:]):
                try:
                    data = json.loads(line)
                    data["event_type"] = EventType(data["event_type"])
                    events.append(Event(**data))
                except Exception:
                    pass
        return events
