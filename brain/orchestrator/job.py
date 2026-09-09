"""Unified Job Model for Mission Control.

Represents a unit of execution submitted to any Agent or API provider.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from providers.base import ProviderType


@dataclass
class Job:
    """Unified job representation passed to providers and tracked by Mission Control."""
    id: str = field(default_factory=lambda: f"job-{uuid.uuid4().hex[:12]}")
    provider: str = ""
    provider_type: ProviderType = ProviderType.API
    account: str = ""
    worker: str | None = None
    model: str = ""
    task: str = ""
    status: str = "pending"  # pending, running, completed, failed, cancelled
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    duration: float | None = None
    retry_count: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] | None = None

    def mark_started(self) -> None:
        self.status = "running"
        self.started_at = datetime.now(timezone.utc).isoformat()

    def mark_completed(self, result: dict[str, Any], duration: float | None = None) -> None:
        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.result = result
        if duration is not None:
            self.duration = duration
        elif self.started_at:
            try:
                t0 = datetime.fromisoformat(self.started_at).timestamp()
                t1 = datetime.fromisoformat(self.completed_at).timestamp()
                self.duration = round(t1 - t0, 3)
            except Exception:
                pass

    def mark_failed(self, error: str, duration: float | None = None) -> None:
        self.status = "failed"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.error = error
        if duration is not None:
            self.duration = duration

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "provider_type": self.provider_type.value if hasattr(self.provider_type, "value") else str(self.provider_type),
            "account": self.account,
            "worker": self.worker,
            "model": self.model,
            "task": self.task,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration": self.duration,
            "retry_count": self.retry_count,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
            "context": self.context,
        }
