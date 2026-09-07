# Architecture & Technical Design

## 1. System Overview

The Agentic Shared Memory architecture is an autonomous multi-agent orchestration framework designed for local, resource-constrained environments (CachyOS Linux, Intel i7, 2 CPU cores, 16 GB RAM).

```
 USER PROMPT / INSTRUCTION
            │
            ▼
┌───────────────────────┐
│     SHARED BRAIN      │
│  Orchestrator Core    │
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐       ┌────────────────────────┐
│     SMART ROUTER      │◀─────▶│    PROVIDER REGISTRY   │
│  Capability & Model   │       │  Antigravity, Kiro,    │
│  Heuristic Classifier │       │  Cline, Future APIs    │
└───────────┬───────────┘       └────────────────────────┘
            │
            ▼
┌───────────────────────┐
│    SWARM RUNNER       │
│  Bounded Concurrency  │
│  Max 2 Workers        │
└───────────┬───────────┘
            │
    ┌───────┴────────────────────────┬───────────────────────┐
    ▼                                ▼                       ▼
┌─────────────────────────┐  ┌────────────────────┐  ┌───────────────┐
│  Antigravity Adapter    │  │    Kiro Adapter    │  │ Cline Adapter │
│  Account 1: CLI Profile │  │    `kiro-cli`      │  │ `cline`       │
│  Account 2: IDE Profile │  │                    │  │               │
└───────────┬─────────────┘  └─────────┬──────────┘  └───────┬───────┘
            │                          │                     │
            └──────────────────────────┼─────────────────────┘
                                       │
                                       ▼
                         ┌───────────────────────────┐
                         │   FILE LOCKER & RUNTIME   │
                         │   Task Persistence & TTL  │
                         └─────────────┬─────────────┘
                                       │
                                       ▼
                         ┌───────────────────────────┐
                         │     OBSERVABILITY BUS     │
                         │   Events, Memory, Handoff │
                         └─────────────┬─────────────┘
                                       │
                                       ▼
                         ┌───────────────────────────┐
                         │    MISSION CONTROL UI     │
                         │   Web Dashboard (:3333)   │
                         └───────────────────────────┘
```

## 2. Core Subsystems

### A. Provider & Agent Abstraction (`agents/base/` & `providers/registry/`)
Every execution agent implements the `AgentAdapter` abstract interface:
- `execute()`: Runs a prompt with explicit model flag and cwd isolation.
- `continue_session()`: Continues an ongoing conversation id.
- `health()`: Verifies local executable and data directory readiness.
- `capabilities()`: Declares functional specializations.
- `available_models()`: Declares locally verified model strings.

### B. Smart Router (`brain/router/`)
Analyzes tasks using regex pattern heuristics, required capabilities, and task complexity:
- `antigravity-account-1`: Architecture, Governance, Strategic Design.
- `antigravity-account-2`: Deep Refactoring, Component Architecture, Code Review.
- `kiro-cli`: Terminal Execution, Pytest, Environment Scaffolding, DevOps.
- `cline`: Frontend Styling, HTML/CSS, UI Components.

### C. Persistent Tasks (`tasks/`)
State survives crashes, shell exits, and system reboots via atomic JSON disk serialization:
- `tasks/queue/`: Pending ready tasks.
- `tasks/active/`: Running tasks with start timestamps.
- `tasks/completed/`: Completed tasks with outputs and token metrics.
- `tasks/failed/`: Failed or blocked tasks with error logs.

### D. Shared Memory (`memory/`)
SQLite-backed scoped memory store (`GLOBAL`, `PROJECT`, `TASK`, `AGENT`, `SESSION`) with relevance-filtered contextual retrieval to keep agent prompts lean and effective.

### E. Concurrency & File Safety (`locks/`)
FileLocker prevents concurrent conflicting writes by multiple agents using atomic lockfiles with TTL expiration.
