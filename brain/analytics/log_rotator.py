"""Local Log Rotation Manager for Mission Control Runtime Ledgers.

Implements size-based log rotation (default 10 MB limit, 5 backup generations)
for high-volume append-only JSONL ledgers in runtime/:
- runtime/logs/routing_history.jsonl
- runtime/logs/events.jsonl
- runtime/audit/audit.jsonl
- runtime/logs/token_telemetry.jsonl
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("MissionControl.LogRotator")

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT = 5

DEFAULT_TARGET_FILES: tuple[str, ...] = (
    "runtime/logs/routing_history.jsonl",
    "runtime/logs/events.jsonl",
    "runtime/audit/audit.jsonl",
    "runtime/logs/token_telemetry.jsonl",
)


class LogRotator:
    """Size-based log file rotator with generational backup management."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        target_files: list[str | Path] | tuple[str, ...] | None = None,
    ) -> None:
        if base_dir:
            self.base_dir = Path(base_dir).resolve()
        else:
            self.base_dir = Path(__file__).resolve().parents[2]

        self.max_bytes = max_bytes
        self.backup_count = backup_count
        targets = target_files if target_files is not None else DEFAULT_TARGET_FILES
        self.target_files: list[Path] = [
            Path(p) if Path(p).is_absolute() else (self.base_dir / p)
            for p in targets
        ]

    def should_rotate(self, target: Path | str) -> bool:
        """Return True if the target file exists and its size meets or exceeds max_bytes."""
        p = Path(target) if Path(target).is_absolute() else (self.base_dir / target)
        if not p.is_file():
            return False
        try:
            return p.stat().st_size >= self.max_bytes
        except OSError:
            return False

    def rotate_file(self, target: Path | str) -> dict[str, Any]:
        """Rotate a single file if it exceeds the size threshold.

        Generations: file.jsonl -> file.jsonl.1 -> file.jsonl.2 ... -> file.jsonl.N
        Oldest generation beyond backup_count is discarded.
        """
        p = Path(target) if Path(target).is_absolute() else (self.base_dir / target)
        try:
            rel_name = str(p.relative_to(self.base_dir))
        except ValueError:
            rel_name = str(p)

        result: dict[str, Any] = {
            "file": rel_name,
            "path": str(p),
            "rotated": False,
            "size_bytes": 0,
            "threshold_bytes": self.max_bytes,
            "backups": self.backup_count,
            "error": None,
        }

        if not p.is_file():
            return result

        try:
            size = p.stat().st_size
            result["size_bytes"] = size
        except OSError as exc:
            result["error"] = f"Failed to stat file: {exc}"
            return result

        if size < self.max_bytes:
            return result

        try:
            # Shift existing backup generations down
            for i in range(self.backup_count, 0, -1):
                if i == self.backup_count:
                    oldest = p.parent / f"{p.name}.{i}"
                    if oldest.exists():
                        oldest.unlink()
                else:
                    src = p.parent / f"{p.name}.{i}"
                    dst = p.parent / f"{p.name}.{i + 1}"
                    if src.exists():
                        src.replace(dst)

            # Move current log file to generation .1
            first_backup = p.parent / f"{p.name}.1"
            p.replace(first_backup)

            # Re-create empty file with secure permissions
            p.touch(mode=0o600, exist_ok=True)
            result["rotated"] = True

            # Audit log the rotation action
            try:
                from brain.governance.audit_logger import AuditLogger
                AuditLogger().log(
                    category="MAINTENANCE",
                    action="rotate_log_file",
                    actor="system",
                    status="SUCCESS",
                    details={
                        "file": rel_name,
                        "size_bytes": size,
                        "backup": first_backup.name,
                    },
                )
            except Exception:
                pass

        except Exception as exc:
            logger.error("Failed to rotate %s: %s", p, exc)
            result["error"] = str(exc)

        return result

    def rotate_all(self) -> list[dict[str, Any]]:
        """Evaluate and rotate all configured target files."""
        results: list[dict[str, Any]] = []
        for target in self.target_files:
            results.append(self.rotate_file(target))
        return results


_default_rotator: LogRotator | None = None


def get_log_rotator(
    base_dir: Path | str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> LogRotator:
    """Obtain or initialize the default LogRotator singleton."""
    global _default_rotator
    if _default_rotator is None:
        _default_rotator = LogRotator(
            base_dir=base_dir,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
    return _default_rotator
