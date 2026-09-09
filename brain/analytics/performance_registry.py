"""Performance Registry and Reliability Tracker for Mission Control Phase 19.

Maintains empirical execution history, component health scores, and dynamic routing
weights without arbitrary source modification.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRecord:
    """Telemetry record for an executed task or plan step."""
    record_id: str
    task: str
    domain: str
    agent_id: str
    model: str
    provider: str
    account: str
    tool: Optional[str] = None
    mcp_server: Optional[str] = None
    success: bool = True
    latency_seconds: float = 0.0
    tokens_used: int = 0
    estimated_cost: float = 0.0
    retry_count: int = 0
    verification_passed: bool = True
    error_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PerformanceRegistry:
    """Empirical reliability and health scorer adjusting routing preferences dynamically."""

    def __init__(self, log_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self.log_path = log_path or Path("runtime/analytics/performance.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: List[ExecutionRecord] = []
        self._agent_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
        self._model_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
        self._tool_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
        self._provider_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
        self._load_records()

    def _load_records(self) -> None:
        if not self.log_path.exists():
            return
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        d = json.loads(line)
                        rec = ExecutionRecord(**d)
                        self._records.append(rec)
                        self._update_internal_counts(rec)
        except Exception as e:
            logger.debug("Failed reading performance log: %s", e)

    def _update_internal_counts(self, rec: ExecutionRecord) -> None:
        key_agent = f"{rec.agent_id}::{rec.domain}"
        self._agent_stats[key_agent]["total"] += 1
        if rec.success:
            self._agent_stats[key_agent]["success"] += 1

        self._model_stats[rec.model]["total"] += 1
        if rec.success:
            self._model_stats[rec.model]["success"] += 1

        if rec.tool:
            self._tool_stats[rec.tool]["total"] += 1
            if rec.success:
                self._tool_stats[rec.tool]["success"] += 1

        self._provider_stats[rec.provider]["total"] += 1
        if rec.success:
            self._provider_stats[rec.provider]["success"] += 1

    def record_execution(self, record: ExecutionRecord) -> None:
        """Record execution outcome and persist to telemetry log."""
        with self._lock:
            self._records.append(record)
            self._update_internal_counts(record)
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record.to_dict()) + "\n")
            except Exception as e:
                logger.debug("Failed appending to performance log: %s", e)

    def get_agent_affinity_boost(self, agent_id: str, domain: str) -> float:
        """Calculate empirical routing bonus/penalty for agent in domain [-2.0 to +2.0]."""
        with self._lock:
            key = f"{agent_id}::{domain}"
            stats = self._agent_stats.get(key)
            if not stats or stats["total"] < 2:
                return 0.0  # Cold start: neutral

            rate = stats["success"] / stats["total"]
            # Map 0.0 -> -2.0, 0.5 -> 0.0, 1.0 -> +2.0
            return round((rate - 0.5) * 4.0, 2)

    def get_provider_health_score(self, provider_id: str) -> float:
        """Return health multiplier for provider [0.0 to 1.0]."""
        with self._lock:
            stats = self._provider_stats.get(provider_id)
            if not stats or stats["total"] < 2:
                return 1.0
            return round(stats["success"] / stats["total"], 2)

    def get_tool_reliability(self, tool_id: str) -> float:
        """Return reliability score for tool [0.0 to 1.0]."""
        with self._lock:
            stats = self._tool_stats.get(tool_id)
            if not stats or stats["total"] == 0:
                return 1.0
            return round(stats["success"] / stats["total"], 2)

    def get_summary(self) -> Dict[str, Any]:
        """Generate aggregate performance telemetry summary."""
        with self._lock:
            total = len(self._records)
            successful = sum(1 for r in self._records if r.success)
            total_tokens = sum(r.tokens_used for r in self._records)
            total_cost = sum(r.estimated_cost for r in self._records)
            avg_latency = (sum(r.latency_seconds for r in self._records) / total) if total > 0 else 0.0

            return {
                "total_executions": total,
                "success_rate": round(successful / total, 3) if total > 0 else 1.0,
                "total_tokens_consumed": total_tokens,
                "total_estimated_cost_usd": round(total_cost, 4),
                "average_latency_seconds": round(avg_latency, 3),
                "agents_tracked": len(self._agent_stats),
                "tools_tracked": len(self._tool_stats),
                "providers_tracked": len(self._provider_stats),
            }
