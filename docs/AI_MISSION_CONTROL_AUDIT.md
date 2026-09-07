# AI Mission Control Audit
**Date:** 2026-09-07  
**Auditor:** Antigravity IDE (Gemini 2.5 Pro)  
**Status:** PHASE 1 COMPLETE — READ ONLY. Nothing modified.

---

## 1. Executive Summary

A **functional, multi-layered shared-brain multi-agent system** exists and is actively in use. The core is well-engineered: a `basic-memory` SQLite knowledge graph (MCP server named `brain`) wired into five different agent surfaces, a filesystem-based task queue with JSON task records, a Python orchestrator with keyword-based routing, a smart model-selection policy, a self-healing sentinel, and a local web dashboard served at port 3333.

The system **works in production** — 35 completed tasks, 7 archived handoffs, and verified parallel execution across two Antigravity accounts. However, several **critical gaps** exist:

- The orchestrator's model selection is **computed but never applied** to the actual agent invocations.
- Model tier costs in `model_policy.py` are **inverted** (most expensive model tagged as BALANCED).
- The **Antigravity IDE Account B** cannot be invoked headlessly — delivery-only.
- File locking exists in the `execution.py` module but is **not wired** into the swarm worker path.
- No structured event/observability stream exists beyond a 4-line sentinel audit log.
- The `brain` binary is **not on PATH** (symlinked to `~/.local/bin/brain` but `~/.local/bin` missing from shell PATH).
- The dashboard `default_project` drifts between `main` and `brain` — auto-healed once, will recur.
- A Gemini API key was pasted into a chat transcript — rotation is required.

---

## 2. Current Architecture

```
USER (human or scripted)
│
├─ brain CLI  (~/YashDevops/Agentic_os/scripts/brain/brain — bash 537 lines)
│   ├─ brain ui            → dashboard.py  (Python HTTP on :3333)
│   ├─ brain swarm         → swarm.py      (task queue + parallel worker pool)
│   ├─ brain plan          → orchestrator.py (plan-only, no execution)
│   ├─ brain orchestrator-mcp → orchestrator_mcp.py (FastMCP stdio)
│   ├─ brain watch         → sentinel.py   (continuous self-healing)
│   └─ brain expand/find   → cognitive_engine.py + search_sidecar.py
│
├─ MCP Server "brain"  (basic-memory MCP at stdio, project=brain)
│   └─ Knowledge store: ~/agentic-brain/ (Markdown + SQLite + embeddings)
│
├─ Search Sidecar  (search_sidecar.py — loopback HTTP on :3334)
│
└─ AGENTS (all share the same brain MCP)
    ├─ Kiro CLI           (headless, kiro-cli v2.20.2)
    ├─ Antigravity CLI    (headless, OAuth — Account A)
    ├─ Antigravity API    (headless, Gemini API key — Account B proxy)
    ├─ Cline              (headless CLI + VS Code delivery mode, v3.0.61)
    ├─ Antigravity IDE    (delivery-only — Account B, in editor)
    └─ VS Code Copilot    (delivery-only — individual plan, not headlessly executable)
```

Swarm task lifecycle:
```
brain swarm dispatch "task text"
  → orchestrator.py classifies (keyword heuristics)
  → select_model() picks model/agent (COMPUTED BUT DISCARDED — critical bug)
  → JSON task → ~/agentic-brain/swarm/tasks/pending/task-<id>.json
  → brain swarm dispatch --run
      → headless agents: kiro-cli, antigravity, antigravity-api, cline
           → git worktree sandbox: branch brain/swarm/<task-id>
      → delivery agents: antigravity-ide, copilot
           → write CURRENT_TASK.md; human pastes briefing and acknowledges
```

---

## 3. Directory Structure

### Primary Brain Implementation
```
~/YashDevops/Agentic_os/
├── scripts/brain/
│   ├── brain                     # Bash entrypoint (537 lines)
│   ├── swarm.py                  # Task queue + worker pool (1846 lines)
│   ├── orchestrator.py           # Plan-only classifier/router (124 lines)
│   ├── orchestrator_mcp.py       # FastMCP stdio server (122 lines)
│   ├── model_policy.py           # Model selection policy (553 lines)
│   ├── agent_adapters.py         # Agent capability registry (326 lines)
│   ├── cognitive_engine.py       # 2-hop context retrieval (252 lines)
│   ├── dashboard.py              # Main dashboard (2154 lines) — port 3333
│   ├── dashboard_execution.py    # Dashboard-side Kiro executor (269 lines)
│   ├── dashboard.py.bak-*        # Two backup versions preserved
│   ├── execution.py              # Approval/locking primitives (200 lines)
│   ├── lifecycle.py              # Archive plan primitives (221 lines)
│   ├── search_sidecar.py         # Semantic search HTTP service (309 lines)
│   ├── sentinel.py               # Self-healing daemon (598 lines)
│   ├── install-protocol.py       # Brain protocol installer (110 lines)
│   ├── register-brain-mcp.py     # MCP registration across all agents (187 lines)
│   ├── test-handoff.py           # Handoff verification script (309 lines)
│   ├── attach-antigravity-key.sh # API key attachment utility
│   ├── README.md                 # System documentation
│   └── protocol/shared-brain.md  # Protocol definition (source of truth)
│
├── tests/                        # 14 test files (170+ tests)
│   ├── test_brain_agent_adapters.py
│   ├── test_brain_antigravity_accounts.py  (42 tests)
│   ├── test_brain_antigravity_ide_lifecycle.py
│   ├── test_brain_cline_headless.py
│   ├── test_brain_delivery_model_advice.py
│   ├── test_brain_execution.py
│   ├── test_brain_lifecycle.py
│   ├── test_brain_model_policy.py
│   ├── test_brain_orchestrator_mcp.py
│   ├── test_brain_retrieval_and_orchestrator.py
│   ├── test_brain_swarm_sandbox.py  (21 tests)
│   ├── test_dashboard_execution.py
│   ├── test_phase3_verification.py
│   └── test_v8_telemetry_processor.js
│
├── .kiro/steering/               # Kiro steering documents
│   ├── shared-brain.md           # Brain protocol (inclusion: always)
│   ├── agentic-os-mcp.md         # (inclusion: always)
│   ├── azure-env-map.md          # (manual)
│   ├── local-dev-tools.md
│   ├── mcp-usage.md
│   ├── microservice-pipeline-setup.md
│   ├── multi-agent-coordination.md  # (fileMatch: AGENT_TASKBOARD.md)
│   └── new-environment-clone.md
│
├── AGENTS.md                     # Shared brain protocol (Antigravity hook file)
├── agentic-os-mcp/               # ← Active: branch Devops/pipline, CLEAN
│   └── brain/swarm/task-35d9ca9a  # Swarm sandbox branch (live proof)
└── [8 other service repos — each with own .git]
```

### Brain Memory Store
```
~/agentic-brain/
├── handoff/
│   ├── current.md               # Live baton (last by kiro-cli, status: done)
│   ├── current.md.bak
│   └── [7 archived dated handoffs: 2026-08-27 to 2026-09-04]
├── decisions/                   # 8 architectural decision records
│   ├── shared-brain-engine-choice.md
│   ├── antigravity-is-a-pool-of-accounts-with-capacity-failover.md
│   ├── headless-swarm-execution-runs-in-a-guarded-git-worktree.md
│   ├── per-agent-model-switching-constraints.md
│   ├── copilot-as-delivery-swarm-agent.md
│   └── [3 more]
├── projects/agentic-os.md, agentic-os-mcp.md, agentic-os-shared-brain.md
├── runbooks/                    # 9 runbooks
├── schemas/handoff.md           # Strict Picoschema contract
├── swarm/
│   ├── antigravity-ide/CURRENT_TASK.md + deliveries/ (4 records)
│   ├── cline/CURRENT_TASK.md + deliveries/ (1 record)
│   ├── copilot/CURRENT_TASK.md
│   └── tasks/
│       ├── pending/   (0)
│       ├── in-progress/ (4 — stale since Sep 4)
│       ├── completed/ (35)
│       └── escalated/ (9)
└── .sentinel-audit.jsonl        # 4 entries from 2026-09-03
```

### Other Key Locations
```
~/.basic-memory/         # basic-memory config.json + memory.db (SQLite)
~/.gemini/
├── antigravity/         # IDE Account B data dir
├── antigravity-cli/     # CLI Account A data dir (OAuth)
├── antigravity-api/     # CLI Account B proxy (API key, modelProvider=gemini)
├── antigravity-ide/     # IDE install data (NO mcp_config.json — PROBLEM)
└── config/mcp_config.json  # Global MCP (Antigravity)
~/.kiro/                 # Kiro settings, sessions (20+ CLI session files)
~/.cline/                # Cline data (cache, cron, data, tasks)
~/.aws/amazonq/          # Amazon Q config
~/.config/Code/User/mcp.json  # VS Code MCP (brain only)
~/.local/bin/            # brain, kiro-cli, basic-memory, cline, antigravity
~/.semantic_search/      # all-MiniLM-L6-v2 model cache
~/scripts/brain/dashboard.py  # OLD dashboard (1006 lines) — stale duplicate
~/kharaai-service-account-key.json  # GCP service account key — SECURITY RISK
```

---

## 4. Current Brain

**Entry point:** `~/YashDevops/Agentic_os/scripts/brain/brain` (bash, 537 lines)
Symlinked to `~/.local/bin/brain`. **PROBLEM:** `~/.local/bin` is not in the active shell PATH.

| Component | File | Purpose |
|---|---|---|
| Entrypoint | `brain` (bash) | CLI dispatcher, `basic-memory` wrapper |
| Task Queue | `swarm.py` | JSON filesystem queue, parallel workers |
| Router | `orchestrator.py` | Keyword classifier, plan generator |
| MCP Surface | `orchestrator_mcp.py` | FastMCP stdio for plan/approve/execute |
| Model Policy | `model_policy.py` | Per-agent model catalog and selection |
| Agent Registry | `agent_adapters.py` | Capability declarations, availability probe |
| Context Engine | `cognitive_engine.py` | 2-hop wikilink graph traversal |
| Search | `search_sidecar.py` | Loopback HTTP at :3334 |
| Self-healing | `sentinel.py` | Schema repair, wikilink fix, sidecar watchdog |
| Dashboard | `dashboard.py` | All-in-one HTTP at :3333 (Python stdlib, no framework) |
| Dashboard Exec | `dashboard_execution.py` | Controlled executor (Kiro only, fs_read+fs_write tools) |
| Execution Prim | `execution.py` | Approval (15-min TTL), file locking, verification receipts |
| Lifecycle | `lifecycle.py` | Archive planning (immutable — never mutates notes) |

**Communication:** MCP tools (brain server) + filesystem task JSON + dashboard HTTP API.

---

## 5. Memory System

**Backend:** `basic-memory` — open-source, local, no cloud
- **SQLite:** `~/.basic-memory/memory.db`
- **Source files:** Markdown in `~/agentic-brain/` (project: `brain`)
- **91 entities** indexed
- **Hybrid search:** FTS5 (SQLite) + fastembed `bge-small-en-v1.5` semantic
- **Reranker:** `jinaai/jina-reranker-v1-tiny-en`
- **Projects:** `main` (`~/basic-memory`) and `brain` (`~/agentic-brain`)
- **PROBLEM:** `default_project` drifts to `main` — sentinel healed once (2026-09-03)

All agents share the **same** `~/agentic-brain/` via the `brain` MCP. Memory is shared files, not a live pub/sub. One agent writes, all others read.

**Memory scopes:**
- `handoff/` — transient in-flight state
- `decisions/` — durable architectural choices
- `projects/` — durable project knowledge
- `runbooks/` — durable procedures
- `schemas/` — validation contracts

**No automated purge policy exists.** Brain grows unbounded.

---

## 6. Agent Registry

| Name | Mode | Provider | Binary | Account |
|---|---|---|---|---|
| `kiro-cli` | HEADLESS | Kiro/Amazon Q | `~/.local/bin/kiro-cli` v2.20.2 | Single account |
| `antigravity` | HEADLESS | Google Antigravity | `~/.local/bin/antigravity` v1.1.26 | OAuth (Account A) |
| `antigravity-api` | HEADLESS | Google Antigravity | `~/.local/bin/antigravity` v1.1.26 | API key (Gemini only) |
| `cline` | HEADLESS | OpenRouter | `~/.local/bin/cline` v3.0.61 | Separate API key |
| `antigravity-ide` | DELIVERY | Google Antigravity | `/opt/antigravity-ide/` | OAuth Account B (IDE) |
| `copilot` | DELIVERY | GitHub Copilot | VS Code Chat only | Individual Copilot plan |

---

## 7. Antigravity CLI — Account A

- **Binary:** `~/.local/bin/antigravity` (v1.1.26)
- **Auth:** OAuth via gnome-keyring (Google account)
- **Data dir:** `~/.gemini/antigravity-cli/`
- **Models:** Full Antigravity catalog (Gemini + Claude + GPT variants)
- **Invocation:** `antigravity -p "<prompt>" --model <model> [--effort low|medium|high]`
- **Capabilities:** architecture, deep reasoning, multi-file synthesis, code generation
- **Sandbox:** git worktree `~/.cache/brain/sandboxes/<task-id>` on branch `brain/swarm/<task-id>`
- **Headless:** YES — `--trust-all-tools` flag
- **MCP config:** `~/.gemini/config/mcp_config.json`
- **Session continuation:** NO native session — Brain MCP is the only state bridge

---

## 8. Antigravity IDE — Account B

- **Type:** VS Code IDE extension (Electron `/opt/antigravity-ide/`, v1.107.0)
- **Auth:** Separate OAuth session from CLI Account A
- **Data dir:** `~/.gemini/antigravity-ide/` (separate)
- **MCP config:** `~/.gemini/antigravity-ide/config/mcp_config.json` — **FILE DOES NOT EXIST**
- **Headless:** NO — IDE requires human interaction
- **Swarm role:** DELIVERY only — `CURRENT_TASK.md` written; human pastes briefing
- **Current task:** `task-1f3cf850` (Restructure telemetry helpers — awaiting acknowledgement)
- **Running:** YES (`pid 2466` confirmed, 2× basic-memory MCP processes visible)
- **MCP wiring:** INFERRED working (basic-memory process running alongside IDE)

---

## 9. Kiro

- **Binary:** `~/.local/bin/kiro-cli` (v2.20.2)
- **Auth:** UNKNOWN provider (AWS/Amazon Q territory)
- **Sessions:** 20+ in `~/.kiro/sessions/cli/`
- **Models (verified from `--list-models`):**
  - CHEAPEST: `qwen3-coder-next` (0.05x credit)
  - FAST: `claude-haiku-4.5` (0.40x), `auto` (1.00x), `deepseek-3.2` (0.25x)
  - BALANCED: `gpt-5.6-luna` (0.10x), `gpt-5.6-terra` (1.00x)
  - ADVANCED: `claude-sonnet-4/4.5/4.6/5` (1.30x)
  - MOST EXPENSIVE: `gpt-5.6-sol` (2.40x), `claude-opus-5` (2.20x)
- **MCP:** `~/.kiro/settings/mcp.json` (11 servers, 7 enabled)
- **Headless:** YES — `kiro-cli chat --no-interactive --trust-tools= --model <model>`
- **Capabilities:** terminal ops, builds, Azure CLI, kubectl, npm, tests
- **Conflict:** Both `.kiro` and `.amazonq` exist — Kiro wins with warning

---

## 10. Cline

- **Binary:** `~/.local/bin/cline` (v3.0.61)
- **VS Code extension:** `saoudrizwan.claude-dev-4.1.17`
- **Auth:** OpenRouter (API key location not audited — likely `~/.cline/data/secrets.json`)
- **Data dir:** `~/.cline/` (cache, cron, data, tasks)
- **Models:** Provider-agnostic via OpenRouter
- **Headless:** YES — used as headless worker in swarm
- **Capabilities:** VS Code in-editor modifications, frontend styling, React/Tailwind, component refactoring

---

## 11. Gemini / API Key Setup

- **API key mode:** `antigravity-api` agent — `GEMINI_API_KEY` env + `modelProvider: gemini`
- **Key file:** `~/.config/brain/antigravity-api.key` (0600 permissions ✅)
- **Models accessible on key (verified):** `gemini-3.8-flash-medium`, `gemini-3.7-flash-high`, `gemini-3.7-flash-medium`, `gemini-3.8-flash-low`, `gemini-3.7-flash-low`
- **Rejected on key:** pro models, Claude, GPT variants
- **SECURITY:** Gemini API key was pasted in a chat transcript. Key rotation is listed in handoff `[next]` but status is UNKNOWN.
- **Load balancing:** `balance_antigravity()` in `swarm.py` round-robins between `antigravity` (oauth) and `antigravity-api` (key)

---

## 12. MCP Configuration

### Global — Antigravity CLI + IDE
**File:** `~/.gemini/config/mcp_config.json`

| Server | Status | Transport |
|---|---|---|
| `brain` | ENABLED | `basic-memory mcp --project brain` |
| `bitdefender` | ENABLED | `node bitdefender-mcp/index.js` |
| `firecrawl` | ENABLED | `npx firecrawl-mcp@3.24.0` |
| `cloudflare` | ENABLED | `npx mcp-remote https://mcp.cloudflare.com/mcp` |
| `azure` | ENABLED | `npx @azure/mcp@3.0.0-beta.28` |
| `render` | ENABLED | `npx mcp-remote https://mcp.render.com/mcp` |

### Kiro CLI
**File:** `~/.kiro/settings/mcp.json`

| Server | Status | Notes |
|---|---|---|
| `brain` | ENABLED | |
| `bitdefender` | ENABLED | |
| `firecrawl` | ENABLED | |
| `azure` | ENABLED | |
| `cloudflare` | ENABLED | Different impl: `@cloudflare/mcp-server-cloudflare` |
| `render` | ENABLED | |
| `betterclaw` | DISABLED | Unused |
| `omniroute` | DISABLED | Placeholder token `REQUIRES_MANAGEMENT_TOKEN` |
| `kirocrew-core/cron/computer` | DISABLED | |

### Amazon Q CLI — `~/.aws/amazonq/mcp.json`
Enabled: firecrawl, azure, render, cloudflare, bitdefender, brain  
**PROBLEM:** `autoApprove: ["*"]` — grants all tools without confirmation

### VS Code (Copilot) — `~/.config/Code/User/mcp.json`
Enabled: brain only

### Key Problems
- `brain` MCP runs as **2 separate processes** (pid 2627 + pid 3041) = ~540 MB RAM
- Cloudflare uses different implementations across Antigravity vs Kiro configs
- `omniroute` configured with placeholder token — cleanup risk
- Antigravity IDE config dir missing `mcp_config.json`
- All secrets duplicated in 3-4 plaintext JSON files

---

## 13. Shared MCP Tools

| Tool | Auto-Approved | Purpose |
|---|---|---|
| `read_note`, `view_note`, `read_content` | YES | Read brain notes |
| `search_notes`, `search` | YES | Hybrid FTS + semantic search |
| `recent_activity`, `build_context` | YES | Graph/timeline queries |
| `list_directory`, `list_memory_projects` | YES | Navigation |
| `write_note`, `edit_note` | YES | Create/update notes |
| `move_note`, `delete_note`, `delete_project` | **NO** | Destructive — requires confirmation |

`orchestrator_mcp.py` exposes 6 additional tools (`brain_plan_task`, `brain_agent_capabilities`, `brain_approve_plan`, `brain_execute_plan`, `brain_execution_receipt`, `brain_lifecycle_plan`) via FastMCP stdio — but these are **not registered in any agent's MCP config**; only available when `brain orchestrator-mcp` is explicitly started.

---

## 14. Steering / Rules

### Kiro Steering (`.kiro/steering/` — workspace-level)
| File | Inclusion | Purpose |
|---|---|---|
| `shared-brain.md` | always | Brain read/write protocol (The Three Rules) |
| `agentic-os-mcp.md` | always | MCP service history + deployment state |
| `azure-env-map.md` | manual | Azure environment switching |
| `multi-agent-coordination.md` | fileMatch: AGENT_TASKBOARD.md | Two-lane coordination |
| Others | UNKNOWN | Various procedures |

### AGENTS.md
Loaded by Antigravity (reads AGENTS.md as its hook file). Contains full brain protocol + swarm mesh instructions.

### The Three Rules (enforced in all agents)
1. Read `handoff/current` before doing anything
2. Write a checkpoint before running out of context
3. Archive, then reset when done

### Handoff Schema (Picoschema — strictly validated)
Required: `[status]`, `[agent]`, `[scope]`, `[done]`, `[next]`, `[blocker]`  
Optional: `[file]`, `[command]`, `[decision]`

---

## 15. Orchestrator

**File:** `orchestrator.py` (124 lines) — plan only, never executes.

### Classification (keyword heuristics)
| Keywords | Result |
|---|---|
| "delete", "drop", "remove", "overwrite" | Action.MUTATE, Risk.HIGH |
| "deploy", "terraform", "kubernetes" | Action.IMPLEMENT, Risk.MEDIUM |
| "antigravity-ide", "in-editor", "walkthrough" | Agent: antigravity-ide |
| "frontend", "css", "tailwind", "ui", "react" | Agent: cline |
| "multi-file", "cross-file", "restructure" | Agent: antigravity |
| "azure", "kubectl", "build", "test", "npm" | Agent: kiro-cli |
| text > 240 chars or "architecture" | Complexity.HIGH |
| Default | Agent: antigravity |

- No recursive sub-delegation
- Cross-repo tasks: escalate immediately
- Timeout: 120 seconds → escalated status

---

## 16. Task System

### JSON Schema (confirmed from live task files)
```json
{
  "id": "task-<8hex>",
  "title": "string",
  "assigned_to": "kiro-cli|antigravity|antigravity-api|cline|antigravity-ide",
  "confidence": 0.0-1.0,
  "status": "pending|in-progress|completed|escalated",
  "created_at/started_at/completed_at/heartbeat_at": "ISO8601",
  "output": "string",
  "error": "string|null",
  "duration_seconds": float,
  "model_recommendation": { "agent", "candidates[]", "tier", "rationale" },
  "model_reported": "string",
  "stage_state": "acknowledged_by_<agent>|completed"
}
```

### Current State
- Pending: 0 | In-progress: 4 (stale since Sep 4) | Completed: 35 | Escalated: 9

### Missing Fields
No subtasks/dependencies, no priority, no retry count, no per-task audit trail.

---

## 17. Session System

- **Kiro sessions:** 20+ in `~/.kiro/sessions/cli/` — persist but continuation reliability UNKNOWN
- **Antigravity sessions:** Likely ephemeral per invocation — Brain MCP is the only state bridge
- **Session store:** `handoff/current.md` functions as the canonical session state
- No dedicated session database; no time-bounded sessions

---

## 18. Handoff System

### What Handoffs Capture
✅ task/objective, completed work, files modified, verification commands, errors/blockers, decisions, agent identity, next action  
❌ Git commit SHA, test results, memory excerpts, recommended model

### Current State
- `handoff/current.md` — last written by `kiro-cli`, status `done`
- 7 archived dated handoffs (2026-08-27 through 2026-09-04)

---

## 19. Continue Mechanism

No automatic continuation. Protocol:
1. Dying agent: `write_note("handoff/current", ...)` via brain MCP
2. Human switches agent
3. New agent at session start: `recent_activity("7d")` → `read_note("handoff/current")` → states what it resumed
4. Continues from `[next]` field

Dependent on agent following steering rules. No interrupt handler triggers automatic checkpoint.

---

## 20. Smart Model Routing

### Model Catalogs

**Kiro CLI (verified from `--list-models`):**
```
CODING:   qwen3-coder-next       0.05x
BALANCED: gpt-5.6-luna           0.10x
FAST:     minimax-m2.1           0.15x
          deepseek-3.2           0.25x
          claude-haiku-4.5       0.40x
          glm-5                  0.50x
          auto, gpt-5.6-terra    1.00x
ADVANCED: claude-sonnet-4/4.5/4.6/5  1.30x
FRONTIER: claude-opus-5          2.20x
          gpt-5.6-sol            2.40x
```

**Antigravity CLI (OAuth):** Full Antigravity catalog (Gemini + Claude + GPT)  
**Antigravity API (key):** Gemini flash models only (3.7/3.8 variants)

### CRITICAL BUG
`swarm.py` builds agent commands **without `--model` flag**. `select_model()` output is computed and **discarded**. Fallbacks also unused. Model routing is completely inoperative.

### Additional Problems in `model_policy.py`
- `gpt-5.6-sol` (2.40x) labeled as BALANCED — is actually the **most expensive model**
- `gpt-5.6-terra` (1.00x) labeled as FRONTIER — not frontier pricing
- 7 Kiro models missing from catalog (including `qwen3-coder-next` — cheapest at 0.05x)
- `ModelSpec` has no `credit_multiplier` field — impossible to catch cost inversions in tests

---

## 21. Event System

### What Exists
1. **Sentinel audit log:** `~/.sentinel-audit.jsonl` — only 4 entries (config heals + sidecar restarts)
2. **Task JSON files:** `created_at`, `started_at`, `completed_at`, `heartbeat_at`, `error` — de facto events
3. **Dashboard SSE:** `/api/events` endpoint — filesystem watcher notifications to browser

### What Does NOT Exist
- AGENT_STARTED/STOPPED/HEARTBEAT events
- TASK_CREATED/ASSIGNED/HANDOFF events
- MEMORY_CREATED/ACCESSED events
- MODEL_SELECTED/SWITCHED events
- TOOL_CALLED/FAILED events
- Centralized event bus or structured log shipping

---

## 22. Existing UI (Dashboard)

**Location:** `~/YashDevops/Agentic_os/scripts/brain/dashboard.py` (2154 lines, port 3333)  
**OLD DUPLICATE:** `~/scripts/brain/dashboard.py` (1006 lines — stale, should be removed)

**Stack:** Python 3 `http.server.HTTPServer` + Vanilla HTML/JS embedded as string  
**Frontend:** Tailwind CSS (CDN) + Font Awesome (CDN) + Plus Jakarta Sans + custom Canvas force-directed graph

**What Exists:**
- Obsidian-style knowledge graph (force-directed, 2D canvas)
- Live baton HUD card (current handoff agent/status/next)
- All notes vault (searchable list)
- Swarm Mesh modal (task queue by status)
- Self-Heal button → `/api/heal`
- Analytics modal
- Quick search (graph highlight)
- Type filters (handoffs, projects, decisions, runbooks, schemas)
- Note drawer (slide-in panel with full note content)
- Sidecar + validation status indicators

**API Endpoints:** `/api/graph`, `/api/status`, `/api/notes`, `/api/note`, `/api/search`, `/api/events` (SSE), `/api/expand`, `/api/swarm`, `/api/swarm/dispatch`, `/api/swarm/execute`, `/api/heal`, `/api/delivery/lifecycle`

**What Is Missing (vs. Mission Control vision):**
- AGENTS view (live status: available/idle/failed)
- TASKS view (dedicated task management)
- FLOW graph (agent→agent delegation)
- MEMORY explorer (search with scope/source/tag filters)
- HANDOFFS dedicated timeline view
- MODELS view (task→model→routing reason table)
- TOOLS/MCP monitor (call latency, success/failure)
- EVENTS/LOGS stream
- GIT monitor
- SETTINGS panel
- Token usage tracking

---

## 23. Database / Storage

| Store | Path | Format | Backup |
|---|---|---|---|
| Knowledge graph | `~/.basic-memory/memory.db` | SQLite (22 tables) | Config bak files only |
| Brain notes | `~/agentic-brain/` | Obsidian Markdown | NOT git-versioned |
| Task queue | `~/agentic-brain/swarm/tasks/` | Pretty-printed JSON | None (atomic writes ✅) |
| Configs | Various `mcp.json` files | JSON | Timestamped baks present |

No persistent stores for: events/observability, token usage, agent heartbeats, performance metrics.

---

## 24. Git State

| Repo | Status |
|---|---|
| `~/YashDevops/Agentic_os/` | Skeleton `.git` (no commits) — polyrepo container |
| `agentic-os-mcp/` | Branch `Devops/pipline`, HEAD `6351e18`, CLEAN |
| `agentic-os-mcp/` local branches | `brain/swarm/task-35d9ca9a` — live swarm sandbox proof |
| Other 8 service repos | Each has `.git`, individual states not audited |
| `~/agentic-brain/` | NOT a git repo — no version history |

Swarm creates `brain/swarm/<task-id>` branches on target repos, executes in a worktree at `~/.cache/brain/sandboxes/<task-id>`, then commits and removes the worktree.

---

## 25. Performance

| Component | RAM | Notes |
|---|---|---|
| Total system | 16 GB (6.9 GB used, 8.6 GB free) | |
| Antigravity IDE (Electron) | ~800 MB+ | Runs continuously |
| basic-memory MCP × 2 | ~268 MB each = ~540 MB | One per IDE window |
| Search sidecar | Additional Python + embedding model | |
| Swarm workers | ThreadPoolExecutor | CPU spike during dispatch |

**CPU:** 4 cores (Intel i7, `nproc=4`). No heavy polling observed currently.

**Unnecessary costs:**
- 2× basic-memory MCP processes load the embedding model twice
- Old `~/scripts/brain/dashboard.py` on disk (1006 lines) — dead weight

---

## 26. Security

| Risk | Location | Severity |
|---|---|---|
| Bitdefender API key | Plaintext in 4 MCP JSON files | 🔴 Critical |
| Firecrawl API key | Plaintext in 4 MCP JSON files | 🔴 Critical |
| Render API key | Plaintext in 4 MCP JSON files | 🔴 Critical |
| Cloudflare API token | Plaintext in 3 MCP JSON files | 🔴 Critical |
| Gemini API key (pasted in transcript) | Brain conversation log | 🔴 Critical — rotate now |
| GCP service account key | `~/kharaai-service-account-key.json` (home dir) | 🔴 Critical |
| Gemini API key (file) | `~/.config/brain/antigravity-api.key` (0600) | 🟡 Medium — good practice |
| `autoApprove: ["*"]` | Amazon Q MCP config | 🟠 High |
| `--trust-all-tools` in swarm | swarm.py agent invocations | 🟡 Medium — mitigated by worktrees |

**Positive notes:**
- API key never passed as CLI argument or written to brain (✅ enforced in swarm.py)
- Destructive brain tools require manual confirmation (move_note, delete_note)
- Swarm uses git worktree sandboxes

---

## 27. Duplicate Components

| Duplicate | Location A | Location B | Risk |
|---|---|---|---|
| `dashboard.py` | `~/scripts/brain/` (old, 1006 lines) | `~/YashDevops/Agentic_os/scripts/brain/` (current, 2154 lines) | Wrong version may start |
| `shared-brain.md` | `~/.kiro/steering/shared-brain.md` | `~/YashDevops/Agentic_os/AGENTS.md` | 2 copies to maintain |
| Cloudflare MCP | `mcp-remote` (Antigravity) | `@cloudflare/mcp-server-cloudflare` (Kiro) | Different impls, same token |
| `basic-memory mcp` | Process pid 2627 | Process pid 3041 | 2× RAM |
| MCP secrets | In Antigravity config | In Kiro config, in AWS config | 4× maintenance |

---

## 28. Missing Components

| Gap | Impact |
|---|---|
| Model flag never passed to agents | 🔴 Routing is a dead letter |
| No event/observability bus | 🟠 No system-wide visibility |
| No agent liveness monitoring | 🟠 Stale tasks not detected |
| File locking not in swarm | 🟡 Concurrent write risk |
| Antigravity IDE has no headless API | 🟡 Large jobs require human |
| Git state not in handoffs | 🟡 Context loss on agent switch |
| No token/cost tracking | 🟡 Budget blind |
| Secrets in plaintext JSON | 🔴 All MCP configs |
| No task priority or dependencies | 🟢 Sequential workflows only |
| No memory cleanup policy | 🟢 Brain grows unbounded |
| brain not on PATH | 🟠 Cannot use CLI from terminal |
| Antigravity IDE MCP config missing | 🟠 IDE may not have brain access |
| Mission Control UI incomplete | 🟡 Limited operator visibility |

---

## 29. Current Problems (Prioritized)

| # | Problem | Evidence | Severity |
|---|---|---|---|
| P1 | Gemini API key in transcript — not yet rotated | Handoff `[next]` field | 🔴 Critical |
| P2 | GCP service account key unprotected in home dir | `~/kharaai-service-account-key.json` | 🔴 Critical |
| P3 | All MCP API keys in plaintext JSON (4 configs) | Direct inspection | 🔴 Critical |
| P4 | Model selection computed but never applied | `swarm.py` — no `--model` flag | 🔴 Critical |
| P5 | Model cost catalog inverted (gpt-5.6-sol as BALANCED) | Decision record | 🟠 High |
| P6 | 7 Kiro models missing from catalog (cheapest omitted) | Decision record | 🟠 High |
| P7 | `brain` binary not on PATH | Shell: command not found | 🟠 High |
| P8 | `default_project` drifts to `main` | Sentinel audit log | 🟠 High |
| P9 | Antigravity IDE MCP config missing | Directory check | 🟠 High |
| P10 | `autoApprove: ["*"]` in Amazon Q config | `~/.aws/amazonq/mcp.json` | 🟠 High |
| P11 | 4 stale in-progress tasks since Sep 4 | Task JSON heartbeats | 🟡 Medium |
| P12 | Old dashboard.py at `~/scripts/brain/` | Two dashboard.py copies | 🟡 Medium |
| P13 | File locking not wired into swarm workers | `execution.py` vs `swarm.py` | 🟡 Medium |
| P14 | No event system | Sentinel log: 4 entries only | 🟡 Medium |
| P15 | 2× basic-memory MCP processes (~540 MB) | `ps aux` | 🟡 Medium |
| P16 | 9 escalated tasks never cleaned up | Task filesystem | 🟢 Low |
| P17 | OmniRoute placeholder token in Kiro config | `mcp.json` | 🟢 Low |

---

## 30. Recommended Architecture

> **AUDIT ONLY — DO NOT IMPLEMENT**

```
Yashdevops/Agentic_shared_memory/
├── frontend/         Dashboard (React + Vite) at :3333
├── backend/          Brain HTTP API (FastAPI)
├── orchestrator/     orchestrator.py, execution.py, lifecycle.py
├── memory/           cognitive_engine.py, search_sidecar.py
├── agents/           agent_adapters.py, model_policy.py, swarm.py
├── mcp/              mcp_config.json (single source, secrets via env)
├── router/           Model/agent routing — with --model flag actually applied
├── scripts/          brain bash entrypoint, install scripts
├── config/           Single config file, no embedded secrets
├── docs/             This audit + architecture docs
└── tests/            All test_brain_*.py migrated here
```

Priority fixes:
1. Apply `select_model()` output to agent invocations
2. Fix model catalog cost ordering + add missing 7 models
3. Wire `ResourceLockManager` into swarm worker path
4. Move all secrets to env vars or OS keyring
5. Fix `brain` PATH
6. Create Antigravity IDE MCP config
7. Rotate compromised Gemini API key
8. Add event bus (append-only JSONL or SQLite events table)

---

## 31. Migration Strategy

> **DO NOT EXECUTE — documentation only**

**Phase 1 (COMPLETE):** Audit  
**Phase 2:** Fix critical security + routing bugs (no structural changes)  
**Phase 3:** Structural consolidation → `Yashdevops/Agentic_shared_memory/`  
**Phase 4:** Missing components (event bus, file locking, Mission Control UI)

---

## 32. GitHub Reference Repositories

| Repo | Relevance | Gap Addressed |
|---|---|---|
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | HIGH | Event/observability bus, LLM call tracing |
| [dan-calin/shared-agent-memory](https://github.com/dan-calin/shared-agent-memory) | MEDIUM | Memory scoping/cleanup patterns |
| [seslak/agent-router](https://github.com/seslak/agent-router) | MEDIUM | Model selection actually applied at invocation |
| [lx-wnk/Agent-Dashboard](https://github.com/lx-wnk/Agent-Dashboard) | MEDIUM | Mission Control UI patterns |
| [builderz-labs/mission-control](https://github.com/builderz-labs/mission-control) | MEDIUM | Mission Control UI reference |
| [smolboon/mission-control](https://github.com/smolboon/mission-control) | MEDIUM | Alternative Mission Control UI |
| [dyoshikawa/rulesync](https://github.com/dyoshikawa/rulesync) | LOW-MEDIUM | Steering rule sync across agents |
| [JSCOP/atc-kanban](https://github.com/JSCOP/atc-kanban) | LOW | Task board visualization |

---

## 33. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Compromised Gemini API key misused | HIGH | HIGH | Rotate at aistudio.google.com/app/apikey |
| Service account key abused | MEDIUM | HIGH | Move to GCP secrets manager |
| Concurrent write conflict | MEDIUM | MEDIUM | No file locks in swarm (worktrees mitigate) |
| Brain grows unbounded | HIGH | LOW | 429 GB free disk; add TTL policy in Phase 4 |
| Config drift breaks brain project | HIGH | MEDIUM | Sentinel heals; persist `default_project: brain` |
| Wrong dashboard started | LOW | LOW | Remove old `~/scripts/brain/dashboard.py` |
| Stale tasks block worker view | MEDIUM | MEDIUM | Archive stale in-progress tasks |
| npx MCP cold-start failure | MEDIUM | MEDIUM | Consider pre-installing MCP servers |

---

## 34. Open Questions

| # | Question | Resolution Path |
|---|---|---|
| 1 | Has the compromised Gemini API key been rotated? | Check aistudio.google.com/app/apikey |
| 2 | What is Cline's OpenRouter configuration? | `cat ~/.cline/data/globalState.json` |
| 3 | What is Kiro's authentication provider? | `kiro-cli auth status` |
| 4 | Is the old `~/scripts/brain/dashboard.py` ever started? | `systemctl list-units --user`, `crontab -l` |
| 5 | Why is `~/.local/bin` not in PATH? | Check `~/.config/fish/config.fish` or `~/.zshrc` |
| 6 | What GCP service does `kharaai-service-account-key.json` control? | File name suggests GCP project |
| 7 | Is basic-memory cloud sync disabled? | Config shows `workspace_id: null` — inferred disabled |
| 8 | What is OmniRoute at `127.0.0.1:20128`? | Not running; placeholder in Kiro MCP config |
| 9 | Are all 9 service repos actively used? | Check last commit date per repo |
| 10 | Does Antigravity IDE use a workspace-specific MCP config path? | Check IDE settings for config file location |
| 11 | What is the `kirocrew` binary family? | Listed in Kiro MCP but DISABLED |
| 12 | What Amazon Q account is configured alongside Kiro? | Both `.kiro` and `.amazonq` exist; Kiro wins |
| 13 | What is the `betterclaw` MCP server? | DISABLED in Kiro config — unidentified |

---

## Final Audit Summary Table

| Area | State | Priority | Action |
|---|---|---|---|
| Gemini API key in transcript | 🔴 Compromised | 🔴 Critical | Rotate immediately |
| GCP service account in home dir | 🔴 Unprotected | 🔴 Critical | Move to secrets manager |
| MCP API keys in plaintext JSON | 🔴 4 config files | 🔴 Critical | Migrate to env vars |
| Model selection | 🔴 Dead letter | 🔴 Critical | Pass `--model` in swarm.py |
| Model cost catalog | 🟠 Inverted + incomplete | 🟠 High | Fix tiers + add 7 models |
| `brain` PATH | 🟠 Not on PATH | 🟠 High | Add `~/.local/bin` to shell PATH |
| `default_project` drift | 🟠 Recurs after heal | 🟠 High | Persist `default_project: brain` |
| Antigravity IDE MCP | 🟠 Config missing | 🟠 High | Create config file |
| `autoApprove: ["*"]` | 🟠 Amazon Q config | 🟠 High | Restrict to named tools |
| Stale in-progress tasks | 🟡 4 since Sep 4 | 🟡 Medium | Archive or escalate |
| Old dashboard copy | 🟡 Stale duplicate | 🟡 Medium | Remove `~/scripts/brain/dashboard.py` |
| File locking in swarm | 🟡 Not wired | 🟡 Medium | Connect `ResourceLockManager` |
| Event system | 🟡 4 entries only | 🟡 Medium | Implement structured event log |
| 2× MCP processes | 🟡 ~540 MB waste | 🟡 Medium | Single-window discipline |
| Dashboard completeness | 🟡 3/12 sections | 🟡 Medium | Build remaining Mission Control views |
| Antigravity IDE headless | 🟡 Delivery-only | 🟡 Medium | Document; route to headless instead |
| Task dependencies/priority | 🟢 Missing fields | 🟢 Low | Add to task schema in Phase 4 |
| Memory cleanup policy | 🟢 None | 🟢 Low | Add TTL in Phase 4 |
| OmniRoute placeholder | 🟢 Config noise | 🟢 Low | Remove entry |
| Duplicate steering files | 🟢 Maintenance overhead | 🟢 Low | Single source via install-protocol.py |

---

## Migration Map

> **DO NOT EXECUTE — mapping only**

```
CURRENT                                           TARGET
~/YashDevops/Agentic_os/scripts/brain/            → Agentic_shared_memory/scripts/brain/
~/YashDevops/Agentic_os/scripts/brain/swarm.py    → Agentic_shared_memory/agents/swarm.py
~/YashDevops/Agentic_os/scripts/brain/orchestrator.py → Agentic_shared_memory/orchestrator/
~/YashDevops/Agentic_os/scripts/brain/model_policy.py → Agentic_shared_memory/router/
~/YashDevops/Agentic_os/scripts/brain/agent_adapters.py → Agentic_shared_memory/agents/
~/YashDevops/Agentic_os/scripts/brain/dashboard.py → Agentic_shared_memory/frontend/ (rebuild)
~/YashDevops/Agentic_os/scripts/brain/execution.py → Agentic_shared_memory/orchestrator/
~/YashDevops/Agentic_os/scripts/brain/lifecycle.py → Agentic_shared_memory/orchestrator/
~/YashDevops/Agentic_os/scripts/brain/cognitive_engine.py → Agentic_shared_memory/memory/
~/YashDevops/Agentic_os/scripts/brain/search_sidecar.py → Agentic_shared_memory/memory/
~/YashDevops/Agentic_os/scripts/brain/sentinel.py  → Agentic_shared_memory/scripts/
~/YashDevops/Agentic_os/scripts/brain/orchestrator_mcp.py → Agentic_shared_memory/mcp/
~/YashDevops/Agentic_os/scripts/brain/protocol/   → Agentic_shared_memory/docs/protocol/
~/YashDevops/Agentic_os/tests/test_brain_*.py     → Agentic_shared_memory/tests/
~/agentic-brain/                                  → keep at ~/agentic-brain (or mount)
~/.basic-memory/                                  → keep in place
~/.gemini/config/mcp_config.json                  → Agentic_shared_memory/mcp/ (secrets via env)
~/.kiro/settings/mcp.json                         → Agentic_shared_memory/mcp/ (secrets via env)
~/scripts/brain/dashboard.py (OLD)               → ARCHIVE / DELETE
~/YashDevops/Agentic_os/AGENTS.md                 → Agentic_shared_memory/AGENTS.md (single source)
~/YashDevops/Agentic_os/.kiro/steering/           → Agentic_shared_memory/config/steering/
```

---

*PHASE 1 AUDIT COMPLETE. Nothing was modified during this audit. Awaiting Phase 2 instructions.*
