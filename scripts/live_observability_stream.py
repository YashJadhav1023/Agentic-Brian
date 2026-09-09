#!/usr/bin/env python3
"""Temporary read-only live observability monitor for Universal AI Mission Control.

Tails runtime/audit/audit.jsonl and runtime/analytics/performance.jsonl in real-time,
scrubbing any credentials with SecretRedactor, and formatting output according to Section 11.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from providers.registry.credential_manager import get_credential_manager


def format_event(raw_line: str) -> None:
    if not raw_line.strip():
        return
    try:
        data = json.loads(raw_line.strip())
    except Exception:
        return

    cm = get_credential_manager()
    redacted = cm.redactor.redact_dict(data)

    event_id = redacted.get("event_id", "aud-stream")
    category = redacted.get("category", "SYSTEM")
    action = redacted.get("action", "")
    target = redacted.get("target", "")
    status = redacted.get("status", "SUCCESS")
    actor = redacted.get("actor", "system")
    details = redacted.get("details", {})
    ts = redacted.get("timestamp", datetime.now(timezone.utc).isoformat())

    # Format timestamp
    time_str = ts.split("T")[1][:8] if "T" in ts else ts[:8]

    # Map category to Section 11 STAGE
    print(f"\n{time_str}")
    print(f"MISSION {target if target and target.startswith('mission-') else 'mission-active'}")
    print(f"STAGE   {category}")
    if "agent" in details:
        print(f"agent={details['agent']}")
    if "selected_agent" in details:
        print(f"agent={details['selected_agent']}")
    if "model" in details:
        print(f"model={details['model']}")
    if "provider" in details:
        print(f"provider={details['provider']}")
    if "account" in details:
        print(f"account={details['account']}")
    if target:
        print(f"resource={target}")
    print(f"event={action}")
    print(f"status={status}")

    for k, v in details.items():
        if k not in ("agent", "selected_agent", "model", "provider", "account"):
            print(f"  {k}={v}")
    sys.stdout.flush()


def tail_audit(log_path: Path, follow: bool = True) -> None:
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

    print("==================================================")
    print("LIVE MISSION CONTROL OBSERVABILITY STREAM STARTED")
    print(f"Monitoring: {log_path}")
    print("==================================================")
    sys.stdout.flush()

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        # Seek to end
        f.seek(0, os.SEEK_END)
        while follow:
            line = f.readline()
            if line:
                format_event(line)
            else:
                time.sleep(0.5)


if __name__ == "__main__":
    audit_file = PROJECT_ROOT / "runtime" / "audit" / "audit.jsonl"
    tail_audit(audit_file, follow=True)
