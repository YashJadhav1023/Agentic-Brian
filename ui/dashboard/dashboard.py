#!/usr/bin/env python3
"""Mission Control Dashboard Server.

Next-Gen Shared Brain & Multi-Agent Mission Control web application.
Serves interactive tabs: Overview, Agents, Tasks Kanban, Execution Flow,
Memory Explorer, Handoff Viewer, Worktree Sandboxes, Live Events, and Git Monitor.
Equipped with local security hardening: Bearer token auth for mutating actions,
rate limiting, CORS local-origin restriction, security headers, and worktree approval gates.
"""
from __future__ import annotations

import datetime
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from brain.context.continuator import UniversalContinuator
from brain.orchestrator.orchestrator import Orchestrator
from brain.router.smart_router import SmartRouter
from events.bus import Event, EventBus, EventType
from handoffs.handoff_manager import HandoffManager
from memory.store.memory_store import MemoryStore
from providers.registry.bootstrap import create_default_registry
from tasks.manager import TaskManager, TaskStatus

STATIC_DIR = (Path(__file__).resolve().parent / "static").resolve()
PORT = int(os.environ.get("BRAIN_PORT", "3333"))

# Core singletons
registry = create_default_registry()
task_manager = TaskManager(root_tasks_dir=PROJECT_ROOT / "tasks")
handoff_manager = HandoffManager(root_dir=PROJECT_ROOT / "handoffs")
event_bus = EventBus(log_path=PROJECT_ROOT / "runtime" / "logs" / "events.jsonl")
memory_store = MemoryStore(db_path=PROJECT_ROOT / "memory" / "store" / "shared_memory.db")
orchestrator = Orchestrator(
    task_manager=task_manager,
    registry=registry,
    event_bus=event_bus,
    workspace_dir=PROJECT_ROOT,
)

AUTH_TOKEN_ENV_VAR = "MISSION_CONTROL_AUTH_TOKEN"
AUTH_TOKEN_FILE = PROJECT_ROOT / "runtime" / "mission_control.token"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "img-src 'self' data:;"
    ),
}


def get_or_create_auth_token() -> str:
    """Retrieve the mission control auth token from env or disk, or generate one."""
    env_token = os.environ.get(AUTH_TOKEN_ENV_VAR)
    if env_token and env_token.strip():
        return env_token.strip()

    if AUTH_TOKEN_FILE.is_file():
        try:
            token = AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
            if token:
                return token
        except Exception:
            pass

    token = secrets.token_urlsafe(32)
    AUTH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        os.chmod(AUTH_TOKEN_FILE, 0o600)
    except Exception:
        pass
    return token


class RateLimiter:
    """Sliding-window in-memory rate limiter."""

    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}

    def is_allowed(self, client_id: str = "global") -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._requests.setdefault(client_id, [])
            self._requests[client_id] = [t for t in timestamps if t > cutoff]
            if len(self._requests[client_id]) >= self.max_requests:
                return False
            self._requests[client_id].append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()


execution_rate_limiter = RateLimiter(max_requests=30, window_seconds=60.0)


def is_allowed_origin(origin: str | None) -> bool:
    """Enforce CORS policy: only local loopback origins are permitted."""
    if not origin:
        return True
    try:
        parsed = urllib.parse.urlparse(origin)
        if parsed.scheme in ("http", "https"):
            hostname = parsed.hostname
            if hostname in ("127.0.0.1", "localhost", "::1"):
                return True
    except Exception:
        pass
    return False


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class MissionControlHandler(BaseHTTPRequestHandler):

    def _apply_security_headers(self) -> None:
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        origin = self.headers.get("Origin")
        if origin and is_allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "86400")

    def _check_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin and not is_allowed_origin(origin):
            out = json.dumps({"error": "Forbidden", "message": "Disallowed cross-origin request"}).encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self._apply_security_headers()
            self.end_headers()
            self.wfile.write(out)
            return False
        return True

    def _verify_auth(self, path: str) -> bool:
        auth_header = self.headers.get("Authorization")
        client_ip = self.client_address[0] if hasattr(self, "client_address") else "127.0.0.1"
        valid = False

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            expected = get_or_create_auth_token()
            if token and hmac.compare_digest(token, expected):
                valid = True

        if valid:
            event_bus.emit(
                Event(
                    event_type=EventType.AUTH_SUCCESS,
                    metadata={"path": path, "client": client_ip},
                )
            )
            return True
        else:
            event_bus.emit(
                Event(
                    event_type=EventType.AUTH_FAILURE,
                    metadata={"path": path, "client": client_ip},
                )
            )
            out = json.dumps(
                {"error": "Unauthorized", "message": "Valid Bearer token required"}
            ).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self._apply_security_headers()
            self.end_headers()
            self.wfile.write(out)
            return False

    def do_OPTIONS(self) -> None:
        if not self._check_origin():
            return
        self.send_response(204)
        self._apply_security_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if not self._check_origin():
            return

        path = self.path.split("?")[0]

        if path == "/":
            self._serve_html()
        elif path.startswith("/static/"):
            self._serve_static(path)
        elif path == "/api/status":
            self._serve_json(self._get_system_status())
        elif path == "/api/agents":
            self._serve_json(self._get_agents_runtime())
        elif path == "/api/sessions":
            self._serve_json(
                {"sessions": [s.to_dict() for s in orchestrator.sessions.list_recent(50)]}
            )
        elif path == "/api/health":
            self._serve_json(orchestrator.health(deep=False))
        elif path == "/api/tasks":
            tasks = [t.to_dict() for t in task_manager.list_tasks()]
            self._serve_json({"tasks": tasks})
        elif path == "/api/events":
            evts = [e.to_dict() for e in event_bus.get_recent_events(100)]
            self._serve_json({"events": evts})
        elif path == "/api/memory":
            mems = [m.to_dict() for m in memory_store.list_all(100)]
            self._serve_json({"memories": mems, "count": memory_store.count()})
        elif path == "/api/handoff":
            content = handoff_manager.get_current_handoff() or "No active handoff available."
            self._serve_json({"markdown": content})
        elif path == "/api/git":
            self._serve_json(self._get_git_info())
        elif path == "/api/worktrees":
            records = orchestrator.worktrees.status()
            if isinstance(records, list):
                data = [r.to_dict() for r in records]
            elif records:
                data = [records.to_dict()]
            else:
                data = []
            self._serve_json({"worktrees": data})
        elif path == "/api/worktrees/diff":
            query = self.path.split("?")[1] if "?" in self.path else ""
            params = urllib.parse.parse_qs(query)
            task_id = params.get("task_id", [None])[0]
            if not task_id:
                self._serve_json({"error": "Missing task_id query parameter"}, status=400)
            else:
                try:
                    diff_data = orchestrator.worktrees.diff(task_id)
                    self._serve_json(diff_data)
                except ValueError as exc:
                    self._serve_json({"error": str(exc)}, status=404)
                except Exception as exc:
                    self._serve_json({"error": str(exc)}, status=500)
        elif path == "/api/token":
            # Accessible on loopback to supply the authenticated session token to UI
            self._serve_json({"token": get_or_create_auth_token()})
        else:
            self.send_response(404)
            self._apply_security_headers()
            self.end_headers()

    def do_POST(self) -> None:
        if not self._check_origin():
            return

        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        # 1. Unauthenticated endpoints
        if path == "/api/route":
            router = SmartRouter(registry)
            dec = router.route(
                task_text=payload.get("instruction", ""),
                preferred_agent=payload.get("agent"),
                preferred_model=payload.get("model"),
            )
            self._serve_json({
                "agent_id": dec.agent_id,
                "account_id": dec.account_id,
                "model": dec.model,
                "complexity": dec.complexity.value,
                "reason": dec.reason,
            })
            return

        # 2. Mutating endpoints requiring Bearer token authentication
        mutating_paths = {
            "/api/dispatch",
            "/api/continue",
            "/api/worktrees/approve",
            "/api/worktrees/reject",
            "/api/worktrees/cleanup",
            "/api/worktrees/recover",
        }
        if path in mutating_paths:
            if not self._verify_auth(path):
                return

        # 3. Rate limiting for task execution endpoints
        if path in ("/api/dispatch", "/api/continue"):
            client_ip = self.client_address[0] if hasattr(self, "client_address") else "127.0.0.1"
            if not execution_rate_limiter.is_allowed(client_ip):
                self._serve_json(
                    {
                        "error": "Too Many Requests",
                        "message": "Execution rate limit exceeded (30 req/min). Try again later.",
                    },
                    status=429,
                )
                return

        # 4. Confirmation requirement for destructive actions
        destructive_paths = {
            "/api/worktrees/approve",
            "/api/worktrees/reject",
            "/api/worktrees/cleanup",
        }
        if path in destructive_paths:
            if payload.get("confirm") is not True:
                self._serve_json(
                    {
                        "error": "Confirmation required",
                        "message": "Explicit confirmation ('confirm': true) required for destructive worktree operations",
                    },
                    status=400,
                )
                return

        # 5. Endpoint dispatch handlers
        if path == "/api/dispatch":
            task = orchestrator.plan_and_dispatch(
                instruction=payload.get("instruction", "Untitled Task"),
                preferred_agent=payload.get("agent"),
                preferred_model=payload.get("model"),
                files=payload.get("files"),
            )
            self._serve_json({"status": "created", "task": task.to_dict()})
        elif path == "/api/continue":
            ctx = orchestrator.build_continue_context()
            if getattr(ctx, "is_terminal", False):
                self._serve_json({"status": "terminal", "reason": ctx.terminal_reason, "context": ctx.to_dict()})
            else:
                threading.Thread(target=orchestrator.continue_work, daemon=True).start()
                self._serve_json({"status": "continued", "context": ctx.to_dict()})
        elif path == "/api/worktrees/approve":
            task_id = payload.get("task_id")
            if not task_id:
                self._serve_json({"error": "Missing task_id"}, status=400)
                return
            try:
                approver = payload.get("approver", "mission_control")
                result = orchestrator.worktrees.apply(
                    task_id=task_id, approver=approver, confirm=True
                )
                event_bus.emit(
                    Event(
                        event_type=EventType.DIFF_APPROVED,
                        task_id=task_id,
                        metadata={"approver": approver},
                    )
                )
                event_bus.emit(
                    Event(
                        event_type=EventType.MERGE_APPLIED,
                        task_id=task_id,
                        metadata={"result": result},
                    )
                )
                self._serve_json({"status": "applied", "task_id": task_id, "result": result})
            except RuntimeError as exc:
                self._serve_json(
                    {"error": "Conflict or Dirty Canonical Tree", "message": str(exc)},
                    status=409,
                )
            except Exception as exc:
                self._serve_json({"error": str(exc)}, status=500)
        elif path == "/api/worktrees/reject":
            task_id = payload.get("task_id")
            if not task_id:
                self._serve_json({"error": "Missing task_id"}, status=400)
                return
            try:
                reason = payload.get("reason", "Rejected via Mission Control")
                result = orchestrator.worktrees.reject(task_id=task_id, confirm=True)
                event_bus.emit(
                    Event(
                        event_type=EventType.DIFF_REJECTED,
                        task_id=task_id,
                        metadata={"reason": reason},
                    )
                )
                event_bus.emit(
                    Event(
                        event_type=EventType.WORKTREE_DESTROYED,
                        task_id=task_id,
                        metadata={"action": "reject", "result": result},
                    )
                )
                self._serve_json({"status": "rejected", "task_id": task_id, "result": result})
            except Exception as exc:
                self._serve_json({"error": str(exc)}, status=500)
        elif path == "/api/worktrees/cleanup":
            task_id = payload.get("task_id")
            try:
                if task_id:
                    result = orchestrator.worktrees.remove(
                        task_id=task_id,
                        confirm=True,
                        delete_branch=payload.get("delete_branch", True),
                    )
                    event_bus.emit(
                        Event(
                            event_type=EventType.WORKTREE_DESTROYED,
                            task_id=task_id,
                            metadata={"action": "cleanup_single"},
                        )
                    )
                    self._serve_json({"status": "cleaned", "task_id": task_id, "result": result})
                else:
                    max_age = int(payload.get("max_age_hours", 24))
                    cleaned = orchestrator.worktrees.cleanup(max_age_hours=max_age)
                    for item in cleaned:
                        event_bus.emit(
                            Event(
                                event_type=EventType.WORKTREE_DESTROYED,
                                task_id=item.get("task_id"),
                                metadata={"action": "cleanup_batch"},
                            )
                        )
                    self._serve_json({"status": "cleaned", "cleaned": cleaned})
            except Exception as exc:
                self._serve_json({"error": str(exc)}, status=500)
        elif path == "/api/worktrees/recover":
            task_id = payload.get("task_id")
            if not task_id:
                self._serve_json({"error": "Missing task_id"}, status=400)
                return
            try:
                record = orchestrator.worktrees.recover(task_id=task_id)
                self._serve_json({"status": "recovered", "worktree": record.to_dict()})
            except Exception as exc:
                self._serve_json({"error": str(exc)}, status=500)
        else:
            self.send_response(404)
            self._apply_security_headers()
            self.end_headers()

    def _serve_json(self, data: Any, status: int = 200) -> None:
        out = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(out)

    def _serve_static(self, path: str) -> None:
        rel = path[len("/static/"):]
        file_path = STATIC_DIR / rel
        if file_path.is_file():
            self.send_response(200)
            if rel.endswith(".js"):
                self.send_header("Content-Type", "application/javascript")
            elif rel.endswith(".css"):
                self.send_header("Content-Type", "text/css")
            elif rel.endswith(".woff2"):
                self.send_header("Content-Type", "font/woff2")
            self._apply_security_headers()
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_response(404)
            self._apply_security_headers()
            self.end_headers()

    def _get_agents_runtime(self) -> dict[str, Any]:
        """Registry view enriched with live task, session, handoff and error state."""
        providers = registry.to_dict()
        all_tasks = task_manager.list_tasks()
        sessions = orchestrator.sessions.list_recent(200)
        events = event_bus.get_recent_events(400)

        for provider in providers.values():
            for agent_id, account in provider["accounts"].items():
                agent_tasks = [t for t in all_tasks if t.assigned_agent == agent_id]
                agent_sessions = [s for s in sessions if s.agent_id == agent_id]
                agent_events = [e for e in events if e.agent_id == agent_id]

                running = [t for t in agent_tasks if t.status == TaskStatus.RUNNING]
                completed = [t for t in agent_tasks if t.status == TaskStatus.COMPLETED]
                failed = [t for t in agent_tasks if t.status == TaskStatus.FAILED]

                if not account.get("healthy"):
                    display_status = "OFFLINE"
                elif running:
                    display_status = "WORKING"
                elif failed and (not completed or failed[0].created_at > completed[0].created_at):
                    display_status = "FAILED"
                elif agent_tasks:
                    display_status = "IDLE"
                else:
                    display_status = "ONLINE"

                latest = running[0] if running else (agent_tasks[0] if agent_tasks else None)
                latest_session = agent_sessions[0] if agent_sessions else None

                account.update(
                    {
                        "display_status": display_status,
                        "current_task": {
                            "task_id": latest.task_id,
                            "title": latest.title,
                            "status": latest.status.value,
                            "requested_model": latest.assigned_model,
                            "reported_model": latest.actual_model,
                            "duration_seconds": latest.duration_seconds,
                        }
                        if latest
                        else None,
                        "session_id": latest_session.session_id if latest_session else None,
                        "conversation_id": latest_session.conversation_id if latest_session else None,
                        "last_activity": (
                            agent_events[0].timestamp if agent_events else
                            (latest_session.updated_at if latest_session else None)
                        ),
                        "counts": {
                            "total": len(agent_tasks),
                            "running": len(running),
                            "completed": len(completed),
                            "failed": len(failed),
                        },
                        "recent_tasks": [
                            {
                                "task_id": t.task_id,
                                "title": t.title,
                                "status": t.status.value,
                                "requested_model": t.assigned_model,
                                "reported_model": t.actual_model,
                                "conversation_id": t.conversation_id,
                                "duration_seconds": t.duration_seconds,
                                "files": t.files,
                                "handoffs": t.handoffs,
                            }
                            for t in agent_tasks[:10]
                        ],
                        "recent_errors": [
                            err for t in agent_tasks[:10] for err in t.errors
                        ][:5],
                        "recent_events": [
                            {
                                "event_type": e.event_type.value,
                                "timestamp": e.timestamp,
                                "task_id": e.task_id,
                            }
                            for e in agent_events[:10]
                        ],
                    }
                )
        return providers

    def _get_system_status(self) -> dict[str, Any]:
        tasks = task_manager.list_tasks()
        return {
            "tasks_count": len(tasks),
            "running_tasks": len([t for t in tasks if t.status == TaskStatus.RUNNING]),
            "completed_tasks": len([t for t in tasks if t.status == TaskStatus.COMPLETED]),
            "ready_tasks": len([t for t in tasks if t.status == TaskStatus.READY]),
            "failed_tasks": len([t for t in tasks if t.status == TaskStatus.FAILED]),
            "memories_count": memory_store.count(),
            "agents": self._get_agents_runtime(),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def _get_git_info(self) -> dict[str, Any]:
        try:
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(PROJECT_ROOT), text=True).strip()
            commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(PROJECT_ROOT), text=True).strip()
        except Exception:
            branch, commit = "main", "initial"
        try:
            status = subprocess.check_output(["git", "status", "--short"], cwd=str(PROJECT_ROOT), text=True).strip()
        except Exception:
            status = "clean"
        return {"branch": branch, "commit": commit, "status": status or "clean"}

    def _serve_html(self) -> None:
        html = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>Agentic Brain — Mission Control</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="/static/tailwind.js"></script>
  <link rel="stylesheet" href="/static/fontawesome.css">
  <link rel="stylesheet" href="/static/fonts.css">
  <script src="/static/marked.min.js"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 50: '#f0fdf4', 500: '#22c55e', 600: '#16a34a', 900: '#14532d' },
            slate: { 850: '#151b28', 900: '#0f172a', 950: '#080d1a' }
          }
        }
      }
    }
  </script>
</head>
<body class="bg-slate-950 text-slate-100 font-sans min-h-screen flex flex-col">
  <!-- Top Bar -->
  <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur px-6 py-3 flex items-center justify-between sticky top-0 z-50">
    <div class="flex items-center space-x-4">
      <div class="w-3 h-3 rounded-full bg-emerald-500 animate-pulse"></div>
      <h1 class="text-lg font-bold tracking-wide text-white flex items-center gap-2">
        <i class="fa-solid fa-brain text-emerald-400"></i> AGENTIC BRAIN <span class="text-xs px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 font-mono">MISSION CONTROL</span>
      </h1>
    </div>
    <div class="flex items-center space-x-6 text-xs text-slate-400">
      <div>Host: <span class="text-slate-200 font-mono">CachyOS (2 Cores / 16GB)</span></div>
      <div id="git-badge" class="px-2 py-1 rounded bg-slate-800 border border-slate-700 font-mono text-emerald-400">main</div>
      <button onclick="triggerContinue()" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded font-medium shadow-sm transition flex items-center gap-1.5">
        <i class="fa-solid fa-play"></i> Continue Work
      </button>
    </div>
  </header>

  <!-- Main Container -->
  <div class="flex-1 flex overflow-hidden">
    <!-- Sidebar Tabs -->
    <aside class="w-64 border-r border-slate-800 bg-slate-900/50 flex flex-col p-3 space-y-1">
      <button onclick="showTab('dashboard')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium active bg-slate-800 text-white" data-tab="dashboard">
        <i class="fa-solid fa-gauge-high mr-2 text-slate-400"></i> Dashboard
      </button>
      <button onclick="showTab('agents')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="agents">
        <i class="fa-solid fa-robot mr-2 text-indigo-400"></i> Agents & Accounts
      </button>
      <button onclick="showTab('tasks')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="tasks">
        <i class="fa-solid fa-list-check mr-2 text-amber-400"></i> Tasks (Kanban)
      </button>
      <button onclick="showTab('worktrees')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="worktrees">
        <i class="fa-solid fa-shield-halved mr-2 text-emerald-400"></i> Worktree Sandboxes
      </button>
      <button onclick="showTab('flow')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="flow">
        <i class="fa-solid fa-diagram-project mr-2 text-cyan-400"></i> Execution Flow
      </button>
      <button onclick="showTab('memory')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="memory">
        <i class="fa-solid fa-database mr-2 text-purple-400"></i> Shared Memory
      </button>
      <button onclick="showTab('handoffs')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="handoffs">
        <i class="fa-solid fa-file-contract mr-2 text-emerald-400"></i> Handoffs
      </button>
      <button onclick="showTab('events')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="events">
        <i class="fa-solid fa-bolt mr-2 text-yellow-400"></i> Live Events
      </button>
      <button onclick="showTab('git')" class="tab-btn w-full text-left px-3 py-2 rounded text-sm hover:bg-slate-800 text-slate-300 font-medium" data-tab="git">
        <i class="fa-brands fa-git-alt mr-2 text-red-400"></i> Git & Security
      </button>
    </aside>

    <!-- Content Area -->
    <main class="flex-1 overflow-y-auto p-8 bg-slate-950">
      <!-- DASHBOARD TAB -->
      <section id="tab-dashboard" class="tab-pane block space-y-6">
        <h2 class="text-xl font-bold text-white mb-4">Mission Overview</h2>
        <div class="grid grid-cols-4 gap-4">
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Active Agents</div>
            <div id="stat-agents" class="text-2xl font-bold text-emerald-400 mt-1">4 Online</div>
            <div class="text-[11px] text-slate-500 mt-1">Kiro, Cline, Antigravity 1 & 2</div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Running Tasks</div>
            <div id="stat-running" class="text-2xl font-bold text-cyan-400 mt-1">0</div>
            <div class="text-[11px] text-slate-500 mt-1">Bounded Concurrency: Max 2</div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Completed Tasks</div>
            <div id="stat-completed" class="text-2xl font-bold text-purple-400 mt-1">0</div>
            <div class="text-[11px] text-slate-500 mt-1">Verified on Disk</div>
          </div>
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <div class="text-xs text-slate-400 font-medium uppercase">Shared Memories</div>
            <div id="stat-memory" class="text-2xl font-bold text-amber-400 mt-1">0</div>
            <div class="text-[11px] text-slate-500 mt-1">Scoped & Filtered</div>
          </div>
        </div>

        <!-- Quick Dispatch Box -->
        <div class="bg-slate-900 border border-slate-800 p-5 rounded-lg">
          <h3 class="text-sm font-semibold text-white mb-2 flex items-center gap-2">
            <i class="fa-solid fa-paper-plane text-emerald-400"></i> Dispatch Instruction to Swarm
          </h3>
          <div class="flex gap-3">
            <input id="quick-instruction" type="text" placeholder="e.g. Restructure telemetry helpers across services..." class="flex-1 bg-slate-950 border border-slate-700 rounded px-4 py-2 text-sm text-white focus:outline-none focus:border-emerald-500">
            <select id="quick-agent" class="bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300">
              <option value="">Smart Router (Auto)</option>
              <option value="antigravity-account-1">Antigravity Account 1 (CLI)</option>
              <option value="antigravity-account-2">Antigravity Account 2 (IDE)</option>
              <option value="kiro-cli">Kiro CLI</option>
              <option value="cline">Cline CLI</option>
            </select>
            <button onclick="dispatchTask()" class="px-5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-sm font-semibold transition">
              Dispatch
            </button>
          </div>
        </div>
      </section>

      <!-- AGENTS TAB -->
      <section id="tab-agents" class="tab-pane hidden space-y-6">
        <h2 class="text-xl font-bold text-white mb-2">Agent & Account Registry</h2>
        <p class="text-sm text-slate-400 mb-6">Each execution resource is isolated. Antigravity Account 1 and Account 2 maintain distinct profiles and data directories.</p>
        <div id="agents-grid" class="grid grid-cols-2 gap-4">
          <!-- Dynamically populated -->
        </div>
      </section>

      <!-- TASKS TAB -->
      <section id="tab-tasks" class="tab-pane hidden space-y-6">
        <h2 class="text-xl font-bold text-white mb-4">Task Lifecycle (Kanban)</h2>
        <div class="grid grid-cols-3 gap-4">
          <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
            <h3 class="text-xs font-semibold uppercase text-slate-400 mb-3 flex items-center justify-between">
              <span>Ready Queue</span>
              <span id="badge-ready" class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px]">0</span>
            </h3>
            <div id="tasks-ready" class="space-y-2"></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
            <h3 class="text-xs font-semibold uppercase text-cyan-400 mb-3 flex items-center justify-between">
              <span>Running / Active</span>
              <span id="badge-running" class="px-2 py-0.5 rounded bg-cyan-950 text-cyan-300 text-[10px]">0</span>
            </h3>
            <div id="tasks-running" class="space-y-2"></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
            <h3 class="text-xs font-semibold uppercase text-emerald-400 mb-3 flex items-center justify-between">
              <span>Completed</span>
              <span id="badge-completed" class="px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 text-[10px]">0</span>
            </h3>
            <div id="tasks-completed" class="space-y-2"></div>
          </div>
        </div>
      </section>

      <!-- WORKTREES TAB -->
      <section id="tab-worktrees" class="tab-pane hidden space-y-6">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-xl font-bold text-white mb-1">Isolated Git Worktree Sandboxes</h2>
            <p class="text-sm text-slate-400">Deterministic, safe sandboxes where agents execute file modifications. Review diffs before applying or rejecting.</p>
          </div>
          <button onclick="cleanupOldWorktrees()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs transition flex items-center gap-1.5">
            <i class="fa-solid fa-broom"></i> Cleanup Stale
          </button>
        </div>

        <div id="worktrees-container" class="space-y-4">
          <!-- Dynamically populated worktree cards -->
        </div>

        <!-- Diff Viewer Modal -->
        <div id="diff-modal" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-6">
          <div class="bg-slate-900 border border-slate-800 rounded-lg max-w-4xl w-full max-h-[85vh] flex flex-col shadow-2xl">
            <div class="p-4 border-b border-slate-800 flex items-center justify-between">
              <h3 id="diff-modal-title" class="font-bold text-sm text-white font-mono">Diff</h3>
              <button onclick="closeDiffModal()" class="text-slate-400 hover:text-white text-sm px-2 py-1">✕</button>
            </div>
            <pre id="diff-modal-body" class="p-4 overflow-auto font-mono text-xs text-slate-300 flex-1 bg-slate-950 whitespace-pre"></pre>
          </div>
        </div>
      </section>

      <!-- FLOW TAB -->
      <section id="tab-flow" class="tab-pane hidden space-y-6">
        <h2 class="text-xl font-bold text-white mb-2">Autonomous Execution Flow</h2>
        <div class="bg-slate-900 border border-slate-800 rounded-lg p-6 font-mono text-sm text-slate-300 space-y-4">
          <div class="flex items-center gap-3">
            <div class="px-3 py-1.5 bg-indigo-950 border border-indigo-700 text-indigo-300 rounded">1. USER / PROMPT</div>
            <i class="fa-solid fa-arrow-right text-slate-500"></i>
            <div class="px-3 py-1.5 bg-emerald-950 border border-emerald-700 text-emerald-300 rounded">2. SHARED BRAIN</div>
            <i class="fa-solid fa-arrow-right text-slate-500"></i>
            <div class="px-3 py-1.5 bg-cyan-950 border border-cyan-700 text-cyan-300 rounded">3. SMART ROUTER</div>
          </div>
          <div class="pl-12 border-l-2 border-slate-800 space-y-2 py-2">
            <div class="text-xs text-slate-400">├── Task Analysis (Complexity, Action, Risk, Capabilities)</div>
            <div class="text-xs text-slate-400">├── Agent Selection (Account 1, Account 2, Kiro, Cline)</div>
            <div class="text-xs text-slate-400">└── Model Policy (Frontier, Advanced, Balanced, Fast)</div>
          </div>
          <div class="flex items-center gap-3">
            <div class="px-3 py-1.5 bg-amber-950 border border-amber-700 text-amber-300 rounded">4. SWARM WORKER</div>
            <i class="fa-solid fa-arrow-right text-slate-500"></i>
            <div class="px-3 py-1.5 bg-purple-950 border border-purple-700 text-purple-300 rounded">5. WORKTREE SANDBOX & DIFF</div>
            <i class="fa-solid fa-arrow-right text-slate-500"></i>
            <div class="px-3 py-1.5 bg-emerald-950 border border-emerald-700 text-emerald-300 rounded">6. HUMAN APPROVAL & MERGE</div>
          </div>
        </div>
      </section>

      <!-- MEMORY TAB -->
      <section id="tab-memory" class="tab-pane hidden space-y-4">
        <h2 class="text-xl font-bold text-white mb-2">Shared Memory Explorer</h2>
        <div id="memory-list" class="space-y-3"></div>
      </section>

      <!-- HANDOFFS TAB -->
      <section id="tab-handoffs" class="tab-pane hidden space-y-4">
        <h2 class="text-xl font-bold text-white mb-2">Structured Handoff Viewer</h2>
        <div id="handoff-content" class="bg-slate-900 border border-slate-800 p-6 rounded-lg font-sans text-sm prose prose-invert max-w-none">
          Loading handoff...
        </div>
      </section>

      <!-- EVENTS TAB -->
      <section id="tab-events" class="tab-pane hidden space-y-4">
        <h2 class="text-xl font-bold text-white mb-2">Live Structured Telemetry & Events</h2>
        <div id="events-list" class="space-y-2 font-mono text-xs"></div>
      </section>

      <!-- GIT TAB -->
      <section id="tab-git" class="tab-pane hidden space-y-4">
        <h2 class="text-xl font-bold text-white mb-2">Git Repository & Security Status</h2>
        <div class="bg-slate-900 border border-slate-800 p-6 rounded-lg space-y-3 font-mono text-xs">
          <div>Repository: <span class="text-emerald-400">YashJadhav1023/Agentic-Brian</span></div>
          <div>Branch: <span id="git-branch" class="text-cyan-400">main</span></div>
          <div>Latest Commit: <span id="git-commit" class="text-slate-300"></span></div>
          <div>Working Tree: <span id="git-status-text" class="text-amber-400"></span></div>
          <div class="mt-4 p-3 bg-slate-950 border border-slate-800 rounded text-slate-400">
            ✅ Zero-Leak Policy Active: Runtime logs, locks, keys, and private credentials strictly excluded via .gitignore.
          </div>
        </div>
      </section>
    </main>
  </div>

  <script>
    let authToken = '';

    async function initAuth() {
      try {
        const res = await fetch('/api/token');
        if (res.ok) {
          const data = await res.json();
          authToken = data.token;
        }
      } catch (err) {
        console.warn('Could not fetch token:', err);
      }
    }

    async function fetchWithAuth(url, options = {}) {
      options.headers = options.headers || {};
      if (authToken) {
        options.headers['Authorization'] = 'Bearer ' + authToken;
      }
      return fetch(url, options);
    }

    function showTab(tabId) {
      document.querySelectorAll('.tab-pane').forEach(el => el.classList.add('hidden'));
      document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('block'));
      const target = document.getElementById('tab-' + tabId);
      if (target) {
        target.classList.remove('hidden');
        target.classList.add('block');
      }
      document.querySelectorAll('.tab-btn').forEach(btn => {
        if (btn.getAttribute('data-tab') === tabId) {
          btn.classList.add('bg-slate-800', 'text-white');
        } else {
          btn.classList.remove('bg-slate-800', 'text-white');
        }
      });
      refreshData();
    }

    async function refreshData() {
      try {
        const resStatus = await fetch('/api/status');
        const status = await resStatus.json();
        document.getElementById('stat-running').textContent = status.running_tasks;
        document.getElementById('stat-completed').textContent = status.completed_tasks;
        document.getElementById('stat-memory').textContent = status.memories_count;

        // Render Agents
        const grid = document.getElementById('agents-grid');
        grid.innerHTML = '';
        for (const [pId, p] of Object.entries(status.agents)) {
          for (const [aId, a] of Object.entries(p.accounts)) {
            const card = document.createElement('div');
            card.className = 'bg-slate-900 border border-slate-800 p-4 rounded-lg space-y-2';
            const dot = a.healthy ? 'bg-emerald-500' : 'bg-red-500';
            const statusColors = {
              WORKING: 'bg-amber-500/20 text-amber-300',
              ONLINE: 'bg-emerald-500/20 text-emerald-300',
              IDLE: 'bg-sky-500/20 text-sky-300',
              FAILED: 'bg-red-500/20 text-red-300',
              OFFLINE: 'bg-slate-700 text-slate-400'
            };
            const st = a.display_status || (a.healthy ? 'ONLINE' : 'OFFLINE');
            const cur = a.current_task;
            const c = a.counts || {total: 0, completed: 0, failed: 0};
            card.innerHTML = `
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <div class="w-2.5 h-2.5 rounded-full ${dot}"></div>
                  <span class="font-bold text-white text-sm">${a.agent_id}</span>
                </div>
                <span class="text-[10px] px-2 py-0.5 rounded font-mono ${statusColors[st] || statusColors.OFFLINE}">${st}</span>
              </div>
              <div class="text-xs text-slate-400">Account: <span class="text-slate-200">${a.account_id}</span> | Provider: <span class="text-slate-200">${a.provider}</span> | <span class="font-mono text-[10px]">${a.execution_mode}</span></div>
              ${a.profile ? `<div class="text-[10px] text-slate-500 font-mono truncate">Profile: ${a.profile}</div>` : ''}
              <div class="text-[11px] text-slate-500">Health: ${a.health_reason}</div>
              <div class="text-[11px] text-slate-400">Current task: ${cur ? `<span class="text-slate-200">${cur.title.slice(0, 42)}</span> <span class="font-mono text-[10px] text-slate-500">(${cur.status})</span>` : '<span class="text-slate-600">none</span>'}</div>
              <div class="text-[11px] text-slate-400">Model: <span class="font-mono text-emerald-400">${cur ? (cur.requested_model || 'auto') : (a.default_model || 'auto')}</span>${cur && cur.reported_model ? ` <span class="text-slate-500">(reported: ${cur.reported_model})</span>` : ''}</div>
              <div class="text-[10px] text-slate-500 font-mono truncate">Session: ${a.session_id || '-'}</div>
              <div class="text-[10px] text-slate-500 font-mono truncate">Conversation: ${a.conversation_id || '-'}</div>
              <div class="text-[10px] text-slate-500">Duration: ${cur && cur.duration_seconds ? cur.duration_seconds.toFixed(2) + 's' : '-'} | Last activity: ${a.last_activity ? a.last_activity.slice(0, 19).replace('T', ' ') : '-'}</div>
              <div class="text-[10px] text-slate-400">Tasks: ${c.total} total, <span class="text-emerald-400">${c.completed} done</span>, <span class="text-red-400">${c.failed} failed</span></div>
              ${(a.recent_errors && a.recent_errors.length) ? `<div class="text-[10px] text-red-400/80 truncate" title="${a.recent_errors[0].replace(/"/g, '')}">Last error: ${a.recent_errors[0].slice(0, 60)}</div>` : ''}
              <div class="text-[11px] text-slate-400">Models (${a.models.length}): <span class="font-mono text-emerald-400">${a.models.slice(0, 3).join(', ')}${a.models.length > 3 ? ' ...' : ''}</span></div>
            `;
            grid.appendChild(card);
          }
        }

        // Render Tasks
        const resTasks = await fetch('/api/tasks');
        const tasksData = await resTasks.json();
        const readyDiv = document.getElementById('tasks-ready');
        const runningDiv = document.getElementById('tasks-running');
        const compDiv = document.getElementById('tasks-completed');
        readyDiv.innerHTML = ''; runningDiv.innerHTML = ''; compDiv.innerHTML = '';

        let cReady = 0, cRun = 0, cDone = 0;
        for (const t of tasksData.tasks) {
          const item = document.createElement('div');
          item.className = 'p-3 bg-slate-950 border border-slate-800 rounded text-xs space-y-1';
          item.innerHTML = `
            <div class="font-semibold text-slate-200">${t.title}</div>
            <div class="text-slate-500 text-[10px] font-mono">Agent: ${t.assigned_agent || 'auto'} | Model: ${t.assigned_model || 'auto'}</div>
          `;
          if (t.status === 'READY') { readyDiv.appendChild(item); cReady++; }
          else if (t.status === 'RUNNING') { runningDiv.appendChild(item); cRun++; }
          else if (t.status === 'COMPLETED') { compDiv.appendChild(item); cDone++; }
        }
        document.getElementById('badge-ready').textContent = cReady;
        document.getElementById('badge-running').textContent = cRun;
        document.getElementById('badge-completed').textContent = cDone;

        // Render Worktrees
        await renderWorktrees();

        // Render Memories
        const resMem = await fetch('/api/memory');
        const memData = await resMem.json();
        const memList = document.getElementById('memory-list');
        memList.innerHTML = '';
        for (const m of memData.memories) {
          const mItem = document.createElement('div');
          mItem.className = 'p-3 bg-slate-900 border border-slate-800 rounded text-xs space-y-1';
          mItem.innerHTML = `
            <div class="flex justify-between text-[10px] text-slate-500 font-mono">
              <span>[${m.scope}] Source: ${m.source_agent}</span>
              <span>Importance: ${m.importance}/5</span>
            </div>
            <div class="text-slate-200">${m.content}</div>
          `;
          memList.appendChild(mItem);
        }

        // Render Handoff
        const resHandoff = await fetch('/api/handoff');
        const handoffData = await resHandoff.json();
        document.getElementById('handoff-content').innerHTML = marked.parse(handoffData.markdown);

        // Render Events
        const resEvents = await fetch('/api/events');
        const eventsData = await resEvents.json();
        const evList = document.getElementById('events-list');
        evList.innerHTML = '';
        for (const e of eventsData.events.slice(0, 30)) {
          const evItem = document.createElement('div');
          evItem.className = 'p-2 bg-slate-900 border border-slate-800 rounded flex justify-between';
          evItem.innerHTML = `
            <div><span class="text-emerald-400 font-bold">${e.event_type}</span> <span class="text-slate-400">${e.agent_id || ''} ${e.task_id || ''}</span></div>
            <div class="text-slate-600">${e.timestamp.split('T')[1].slice(0, 8)}</div>
          `;
          evList.appendChild(evItem);
        }

        // Render Git
        const resGit = await fetch('/api/git');
        const gitData = await resGit.json();
        document.getElementById('git-badge').textContent = gitData.branch + '@' + gitData.commit;
        document.getElementById('git-branch').textContent = gitData.branch;
        document.getElementById('git-commit').textContent = gitData.commit;
        document.getElementById('git-status-text').textContent = gitData.status;

      } catch (err) {
        console.error('Error refreshing data:', err);
      }
    }

    async function renderWorktrees() {
      try {
        const res = await fetch('/api/worktrees');
        const data = await res.json();
        const container = document.getElementById('worktrees-container');
        if (!container) return;
        container.innerHTML = '';

        if (!data.worktrees || data.worktrees.length === 0) {
          container.innerHTML = `
            <div class="p-8 bg-slate-900/50 border border-slate-800 rounded-lg text-center text-slate-500 text-sm">
              <i class="fa-solid fa-code-commit text-2xl mb-2 text-slate-600"></i>
              <div>No active sandbox worktrees. Mutating tasks will automatically instantiate sandboxes here.</div>
            </div>
          `;
          return;
        }

        for (const wt of data.worktrees) {
          const card = document.createElement('div');
          card.className = 'bg-slate-900 border border-slate-800 p-5 rounded-lg space-y-3';
          
          const statusBadge = {
            ACTIVE: 'bg-cyan-950 text-cyan-400 border border-cyan-800',
            PENDING_REVIEW: 'bg-amber-950 text-amber-300 border border-amber-800',
            APPROVED: 'bg-indigo-950 text-indigo-300 border border-indigo-800',
            APPLIED: 'bg-emerald-950 text-emerald-300 border border-emerald-800',
            REJECTED: 'bg-red-950 text-red-400 border border-red-800',
            FAILED: 'bg-red-950 text-red-400 border border-red-800',
            CLEANED: 'bg-slate-800 text-slate-500 border border-slate-700',
          }[wt.status] || 'bg-slate-800 text-slate-400';

          const filesText = (wt.files_changed && wt.files_changed.length > 0)
            ? wt.files_changed.join(', ')
            : (wt.diff_stat || 'No modifications yet');

          card.innerHTML = `
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-3">
                <span class="font-bold text-white text-sm font-mono">${wt.task_id}</span>
                <span class="text-[10px] px-2 py-0.5 rounded font-mono ${statusBadge}">${wt.status}</span>
              </div>
              <div class="text-xs text-slate-400 font-mono">Branch: <span class="text-emerald-400">${wt.branch}</span></div>
            </div>
            <div class="grid grid-cols-3 gap-2 text-xs text-slate-400 bg-slate-950 p-3 rounded border border-slate-850">
              <div>Agent: <span class="text-slate-200">${wt.agent_id}</span></div>
              <div>Account: <span class="text-slate-200">${wt.account_id}</span></div>
              <div>Base commit: <span class="text-slate-200 font-mono">${(wt.base_commit || '').slice(0, 8)}</span></div>
            </div>
            <div class="text-xs text-slate-400 font-mono">
              Changes: <span class="text-slate-300">${filesText}</span>
              ${wt.insertions ? `<span class="text-emerald-400 ml-2">+${wt.insertions}</span>` : ''}
              ${wt.deletions ? `<span class="text-red-400 ml-1">-${wt.deletions}</span>` : ''}
            </div>
            <div class="flex items-center gap-2 pt-2 border-t border-slate-800">
              <button onclick="viewWorktreeDiff('${wt.task_id}')" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs transition flex items-center gap-1">
                <i class="fa-solid fa-file-lines"></i> View Diff
              </button>
              ${wt.status === 'PENDING_REVIEW' || wt.status === 'ACTIVE' ? `
                <button onclick="approveWorktree('${wt.task_id}')" class="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 text-white rounded text-xs font-medium transition flex items-center gap-1">
                  <i class="fa-solid fa-check"></i> Approve & Apply
                </button>
                <button onclick="rejectWorktree('${wt.task_id}')" class="px-3 py-1.5 bg-red-800 hover:bg-red-700 text-white rounded text-xs font-medium transition flex items-center gap-1">
                  <i class="fa-solid fa-xmark"></i> Reject
                </button>
              ` : ''}
            </div>
          `;
          container.appendChild(card);
        }
      } catch (err) {
        console.error('Error rendering worktrees:', err);
      }
    }

    async function viewWorktreeDiff(taskId) {
      try {
        const res = await fetch('/api/worktrees/diff?task_id=' + encodeURIComponent(taskId));
        const data = await res.json();
        document.getElementById('diff-modal-title').textContent = 'Diff for task: ' + taskId + ' (' + (data.branch || '') + ')';
        document.getElementById('diff-modal-body').textContent = data.diff || data.diff_stat || '(Empty diff or no staged changes)';
        document.getElementById('diff-modal').classList.remove('hidden');
      } catch (err) {
        alert('Failed to load diff: ' + err.message);
      }
    }

    function closeDiffModal() {
      document.getElementById('diff-modal').classList.add('hidden');
    }

    async function approveWorktree(taskId) {
      if (!confirm('Apply sandbox changes from task ' + taskId + ' into canonical repository?')) return;
      try {
        const res = await fetchWithAuth('/api/worktrees/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: taskId, confirm: true })
        });
        const data = await res.json();
        if (res.ok) {
          alert('Changes successfully merged into canonical repository!');
          refreshData();
        } else {
          alert('Error approving worktree: ' + (data.message || data.error));
        }
      } catch (err) {
        alert('Network error approving worktree: ' + err.message);
      }
    }

    async function rejectWorktree(taskId) {
      if (!confirm('Reject and destroy sandbox worktree for task ' + taskId + '?')) return;
      try {
        const res = await fetchWithAuth('/api/worktrees/reject', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: taskId, confirm: true })
        });
        const data = await res.json();
        if (res.ok) {
          alert('Worktree sandbox rejected and destroyed.');
          refreshData();
        } else {
          alert('Error rejecting worktree: ' + (data.message || data.error));
        }
      } catch (err) {
        alert('Network error rejecting worktree: ' + err.message);
      }
    }

    async function cleanupOldWorktrees() {
      if (!confirm('Cleanup all stale worktree sandboxes older than 24 hours?')) return;
      try {
        const res = await fetchWithAuth('/api/worktrees/cleanup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true, max_age_hours: 24 })
        });
        const data = await res.json();
        if (res.ok) {
          alert('Cleanup complete.');
          refreshData();
        } else {
          alert('Error during cleanup: ' + (data.message || data.error));
        }
      } catch (err) {
        alert('Network error: ' + err.message);
      }
    }

    async function dispatchTask() {
      const input = document.getElementById('quick-instruction');
      const agentSel = document.getElementById('quick-agent');
      const text = input.value.trim();
      if (!text) return;
      await fetchWithAuth('/api/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: text, agent: agentSel.value || null })
      });
      input.value = '';
      refreshData();
    }

    async function triggerContinue() {
      await fetchWithAuth('/api/continue', { method: 'POST' });
      alert('Universal Continue triggered!');
      refreshData();
    }

    initAuth().then(() => {
      refreshData();
      setInterval(refreshData, 3000);
    });
  </script>
</body>
</html>"""
        out = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(out)


def run_server(port: int = PORT) -> None:
    server = ThreadedHTTPServer(("127.0.0.1", port), MissionControlHandler)
    print(f"🚀 Mission Control Dashboard listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
