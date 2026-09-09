"""Lightweight Data Retention and Cleanup Manager for Mission Control.

Ensures that usage metrics, routing logs, and execution artifacts do not
grow indefinitely. Enforces a default 30-day retention window.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("MissionControl.RetentionManager")

DEFAULT_RETENTION_DAYS = 30


class RetentionManager:
    """Prunes expired execution artifacts, usage logs, and routing history."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        default_retention_days: int | None = None,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parents[2]
        self.retention_days = default_retention_days if default_retention_days is not None else retention_days
        self.default_retention_days = self.retention_days
        self.runtime_dir = self.base_dir / "runtime"

    def get_cutoff_timestamp(self, days: int = 30) -> datetime:
        """Calculate UTC cutoff datetime for retention policy."""
        return datetime.now(timezone.utc) - timedelta(days=days)

    def cleanup(self, retention_days: int | None = None) -> dict[str, int]:
        """Prune records older than retention threshold."""
        days = retention_days if retention_days is not None else self.retention_days
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff_dt.isoformat()
        cutoff_epoch = cutoff_dt.timestamp()

        results = {
            "usage_records_pruned": 0,
            "routing_records_pruned": 0,
            "jobs_pruned": 0,
            "retention_days": days,
        }

        # 1. Prune usage.jsonl
        usage_file = self.runtime_dir / "analytics" / "usage.jsonl"
        if usage_file.is_file():
            try:
                surviving = []
                with open(usage_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            item = json.loads(line_str)
                            ts = item.get("timestamp", "")
                            if ts >= cutoff_iso:
                                surviving.append(line_str)
                            else:
                                results["usage_records_pruned"] += 1
                        except Exception:
                            surviving.append(line_str)

                if results["usage_records_pruned"] > 0:
                    with open(usage_file, "w", encoding="utf-8") as f:
                        for item in surviving:
                            f.write(item + "\n")
            except Exception as exc:
                logger.warning(f"Failed to prune {usage_file}: {exc}")

        # 2. Prune routing_history.jsonl
        routing_file = self.runtime_dir / "logs" / "routing_history.jsonl"
        if routing_file.is_file():
            try:
                surviving_routing = []
                with open(routing_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            item = json.loads(line_str)
                            ts = item.get("timestamp", "")
                            if ts >= cutoff_iso:
                                surviving_routing.append(line_str)
                            else:
                                results["routing_records_pruned"] += 1
                        except Exception:
                            surviving_routing.append(line_str)

                if results["routing_records_pruned"] > 0:
                    with open(routing_file, "w", encoding="utf-8") as f:
                        for item in surviving_routing:
                            f.write(item + "\n")
            except Exception as exc:
                logger.warning(f"Failed to prune {routing_file}: {exc}")

        # 3. Prune old completed jobs in runtime/jobs/
        jobs_dir = self.runtime_dir / "jobs"
        if jobs_dir.is_dir():
            for f in jobs_dir.glob("job-*.json"):
                try:
                    if f.stat().st_mtime < cutoff_epoch:
                        f.unlink(missing_ok=True)
                        results["jobs_pruned"] += 1
                except Exception:
                    pass

        return results


_GLOBAL_RETENTION_MANAGER: RetentionManager | None = None


def get_retention_manager() -> RetentionManager:
    global _GLOBAL_RETENTION_MANAGER
    if _GLOBAL_RETENTION_MANAGER is None:
        _GLOBAL_RETENTION_MANAGER = RetentionManager()
    return _GLOBAL_RETENTION_MANAGER
