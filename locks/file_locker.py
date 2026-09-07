"""File Locking System to Prevent Conflicting Agent Modifications.

Ensures that multiple concurrent agents do not overwrite the same source files.
Supports automated stale lock reclamation with TTL expiration.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class LockAcquisitionError(Exception):
    """Raised when a resource cannot be locked."""
    pass


class FileLocker:
    """Manages file locks in a designated directory."""

    def __init__(self, lock_dir: Path | None = None) -> None:
        self._lock_dir = lock_dir or (Path(__file__).resolve().parent)
        self._lock_dir.mkdir(parents=True, exist_ok=True)

    def _lock_file_for(self, resource_path: str) -> Path:
        sanitized = resource_path.replace("/", "_").replace("\\", "_").lstrip("_")
        return self._lock_dir / f"{sanitized}.lock"

    def acquire(
        self,
        resource_path: str,
        agent_id: str,
        task_id: str,
        ttl_seconds: int = 600,
    ) -> bool:
        """Attempt to acquire a lock on resource_path. Reclaims if stale."""
        lock_file = self._lock_file_for(resource_path)
        now = time.time()

        if lock_file.exists():
            try:
                data = json.loads(lock_file.read_text(encoding="utf-8"))
                expires_at = data.get("expires_at", 0)
                owner_agent = data.get("agent_id")
                owner_task = data.get("task_id")

                # Re-entrant lock for the same task and agent
                if owner_agent == agent_id and owner_task == task_id:
                    data["expires_at"] = now + ttl_seconds
                    lock_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    return True

                # If lock expired, reclaim it
                if now > expires_at:
                    lock_file.unlink(missing_ok=True)
                else:
                    return False
            except Exception:
                # Corrupted lock file -> remove and reacquire
                lock_file.unlink(missing_ok=True)

        lock_data = {
            "resource": resource_path,
            "agent_id": agent_id,
            "task_id": task_id,
            "acquired_at": now,
            "expires_at": now + ttl_seconds,
            "ttl_seconds": ttl_seconds,
        }

        try:
            # Atomic creation with O_CREAT | O_EXCL
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(lock_data, f, indent=2)
            return True
        except FileExistsError:
            return False

    def release(self, resource_path: str, agent_id: str) -> bool:
        """Release a held lock if owned by agent_id."""
        lock_file = self._lock_file_for(resource_path)
        if not lock_file.exists():
            return True

        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            if data.get("agent_id") == agent_id:
                lock_file.unlink(missing_ok=True)
                return True
            return False
        except Exception:
            lock_file.unlink(missing_ok=True)
            return True

    def is_locked(self, resource_path: str) -> tuple[bool, dict[str, Any] | None]:
        """Check if resource_path is currently locked."""
        lock_file = self._lock_file_for(resource_path)
        if not lock_file.exists():
            return False, None

        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            now = time.time()
            if now > data.get("expires_at", 0):
                lock_file.unlink(missing_ok=True)
                return False, None
            return True, data
        except Exception:
            return False, None

    def recover_stale_locks(self) -> int:
        """Scans all locks and deletes expired ones."""
        count = 0
        now = time.time()
        for p in self._lock_dir.glob("*.lock"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if now > data.get("expires_at", 0):
                    p.unlink(missing_ok=True)
                    count += 1
            except Exception:
                p.unlink(missing_ok=True)
                count += 1
        return count
