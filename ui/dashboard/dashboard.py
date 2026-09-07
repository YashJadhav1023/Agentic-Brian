#!/usr/bin/env python3
"""Mission Control Dashboard Server.

Next-Gen Shared Brain & Multi-Agent Mission Control web application.
Serves interactive tabs: Overview, Agents, Tasks Kanban, Execution Flow,
Memory Explorer, Handoff Viewer, Model Policy, Live Events, and Git Monitor.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import threading
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
from events.bus import EventBus
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


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class MissionControlHandler(BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path == "/":
            self._serve_html()
        elif path.startswith("/static/"):
            self._serve_static(path)
        elif path == "/api/status":
            self._serve_json(self._get_system_status())
        elif path == "/api/agents":
            self._serve_json(registry.to_dict())
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
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

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
        elif path == "/api/dispatch":
            task = orchestrator.plan_and_dispatch(
                instruction=payload.get("instruction", "Untitled Task"),
                preferred_agent=payload.get("agent"),
                preferred_model=payload.get("model"),
            )
            self._serve_json({"status": "created", "task": task.to_dict()})
        elif path == "/api/continue":
            continuator = UniversalContinuator(task_manager, handoff_manager, workspace_dir=PROJECT_ROOT)
            ctx = continuator.build_continue_context()
            task = orchestrator.plan_and_dispatch(
                instruction=ctx.next_recommended_action,
                preferred_agent=ctx.target_agent,
                preferred_model=ctx.target_model,
            )
            # Execute in thread to avoid blocking HTTP
            threading.Thread(target=orchestrator.execute_next, daemon=True).start()
            self._serve_json({"status": "continued", "task": task.to_dict()})
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_json(self, data: Any) -> None:
        out = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
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
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_response(404)
            self.end_headers()

    def _get_system_status(self) -> dict[str, Any]:
        tasks = task_manager.list_tasks()
        return {
            "tasks_count": len(tasks),
            "running_tasks": len([t for t in tasks if t.status == TaskStatus.RUNNING]),
            "completed_tasks": len([t for t in tasks if t.status == TaskStatus.COMPLETED]),
            "ready_tasks": len([t for t in tasks if t.status == TaskStatus.READY]),
            "memories_count": memory_store.count(),
            "agents": registry.to_dict(),
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
            <div class="px-3 py-1.5 bg-purple-950 border border-purple-700 text-purple-300 rounded">5. FILE LOCKS & EXECUTION</div>
            <i class="fa-solid fa-arrow-right text-slate-500"></i>
            <div class="px-3 py-1.5 bg-emerald-950 border border-emerald-700 text-emerald-300 rounded">6. HANDOFF & CONTINUE</div>
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
            card.innerHTML = `
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <div class="w-2.5 h-2.5 rounded-full ${dot}"></div>
                  <span class="font-bold text-white text-sm">${a.agent_id}</span>
                </div>
                <span class="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">${a.execution_mode}</span>
              </div>
              <div class="text-xs text-slate-400">Account: <span class="text-slate-200">${a.account_id}</span> | Provider: <span class="text-slate-200">${a.provider}</span></div>
              <div class="text-[11px] text-slate-500">Status: ${a.health_reason}</div>
              <div class="text-[11px] text-slate-400">Models: <span class="font-mono text-emerald-400">${a.models.slice(0, 3).join(', ')}...</span></div>
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

    async function dispatchTask() {
      const input = document.getElementById('quick-instruction');
      const agentSel = document.getElementById('quick-agent');
      const text = input.value.trim();
      if (!text) return;
      await fetch('/api/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: json.stringify({ instruction: text, agent: agentSel.value || null })
      });
      input.value = '';
      refreshData();
    }

    async function triggerContinue() {
      await fetch('/api/continue', { method: 'POST' });
      alert('Universal Continue triggered!');
      refreshData();
    }

    setInterval(refreshData, 3000);
    refreshData();
  </script>
</body>
</html>"""
        out = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def run_server(port: int = PORT) -> None:
    server = ThreadedHTTPServer(("127.0.0.1", port), MissionControlHandler)
    print(f"🚀 Mission Control Dashboard listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
