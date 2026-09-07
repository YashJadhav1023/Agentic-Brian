# Agentic Shared Memory & Multi-Agent Swarm Orchestrator

[![Multi-Agent Status](https://img.shields.io/badge/Agents-4%20Active%20Headless-emerald.svg)](./docs/AGENTS.md)
[![Hardware](https://img.shields.io/badge/Host-CachyOS%20Linux%20(2%20cores%20/%2016GB)-blue.svg)](./docs/ARCHITECTURE.md)
[![Security](https://img.shields.io/badge/Security-Zero%20Secret%20Leak-success.svg)](./docs/OPERATIONS.md)

An enterprise-grade, lightweight autonomous multi-agent development and orchestration platform. Designed for local headless execution, cross-agent handoffs, persistent memory, and unified observability.

---

## 🚀 Key Highlights

1. **Antigravity Account 2 First-Class Headless Integration:**  
   Natively leverages the official Antigravity CLI's `--app_data_dir=antigravity-ide` flag to run Account 2 headlessly without GUI automation or browser workarounds.
2. **Four Active Execution Resources:**  
   - **Antigravity Account 1** (`antigravity-account-1` via `antigravity-cli` profile)
   - **Antigravity Account 2** (`antigravity-account-2` via `antigravity-ide` profile)
   - **Kiro CLI** (`kiro-cli` headless terminal & testing agent)
   - **Cline CLI** (`cline` headless frontend & editor refactoring agent)
3. **Future-Proof Provider/Adapter Architecture:**  
   Decoupled core orchestrator and router from agent-specific interfaces. Future providers (Gemini API, OpenAI API, Anthropic API) plug in seamlessly via the `ProviderRegistry` without modifying brain logic.
4. **Verified Model Routing:**  
   Smart routing maps task complexity (`FAST`, `STANDARD`, `STRONG`, `REASONING`) to the correct model tier, explicitly passes `--model`, and verifies `actual_model` returned in task responses.
5. **Universal Continue Engine:**  
   A single command (`brain continue`) synthesizes latest task status, handoffs, git diffs, and relevant memory to resume uninterrupted work across agent boundaries.
6. **Hardware-Optimized for CachyOS Linux:**  
   Bounded concurrency (`MAX_CONCURRENT_AGENTS = 2`), zero heavy bloatware (no Kubernetes, Kafka, or Elasticsearch required).

---

## 📁 Repository Structure

```
Agentic_shared_memory/
├── README.md
├── .gitignore
├── .env.example
├── brain/
│   ├── orchestrator/       # Task dispatching and multi-agent swarm
│   ├── router/             # Smart heuristic and capability routing
│   ├── context/            # Universal Continue engine
│   └── state/              # Lifecycle and status tracking
├── agents/
│   ├── base/               # Abstract AgentAdapter interface
│   ├── kiro/               # Headless Kiro CLI adapter
│   ├── cline/              # Headless Cline CLI adapter
│   └── antigravity/
│       ├── account1/       # Antigravity CLI session profile
│       └── account2/       # Antigravity IDE session profile (headless)
├── providers/
│   ├── registry/           # Provider & Adapter discovery registry
│   └── adapters/           # Future provider templates
├── memory/
│   ├── store/              # Scoped SQLite memory store
│   └── retrieval/          # Relevance-filtered contextual retrieval
├── tasks/
│   ├── queue/              # Pending and ready tasks
│   ├── active/             # Running tasks
│   ├── completed/          # Successfully finished tasks
│   └── failed/             # Failed/blocked tasks
├── handoffs/               # Picoschema structured handoff records
├── locks/                  # File locking and concurrency control
├── events/                 # Append-only structured telemetry bus
├── models/
│   └── policies/           # Deterministic model routing & tiers
├── scripts/
│   └── brain.py            # Unified CLI entrypoint
├── tests/                  # Unit and integration test suites
└── ui/
    └── dashboard/          # Next-Gen Mission Control Web UI (:3333)
```

---

## 🛠 Quick Start

### 1. View Registered Agents
```bash
python3 scripts/brain.py agents
```

### 2. Route an Instruction
```bash
python3 scripts/brain.py route "Restructure telemetry helpers across services"
```

### 3. Plan & Queue a Task
```bash
python3 scripts/brain.py plan "Refactor database connection pool"
```

### 4. Execute Tasks in Swarm
```bash
python3 scripts/brain.py execute
```

### 5. Universal Continue
```bash
python3 scripts/brain.py continue
```

### 6. Launch Mission Control Dashboard
```bash
python3 ui/dashboard/dashboard.py
# Open http://127.0.0.1:3333
```

---

## 🧪 Running Tests

```bash
PYTHONPATH=. python3 -m unittest discover -s tests/unit -p "test_*.py"
PYTHONPATH=. python3 -m unittest discover -s tests/integration -p "test_*.py"
```

---

## 📖 Detailed Documentation

- [Architecture Guide](./docs/ARCHITECTURE.md)
- [Agent Specifications](./docs/AGENTS.md)
- [Provider Registry & Future Extensibility](./docs/PROVIDERS.md)
- [Smart Model Routing Policy](./docs/MODEL_ROUTING.md)
- [Multi-Agent Orchestration & Swarm](./docs/ORCHESTRATION.md)
- [Shared Memory System](./docs/MEMORY.md)
- [Structured Handoffs](./docs/HANDOFFS.md)
- [Mission Control UI Guide](./docs/UI.md)
- [Observability & Events](./docs/OBSERVABILITY.md)
- [Operations & Runbook](./docs/OPERATIONS.md)
- [Troubleshooting](./docs/TROUBLESHOOTING.md)
- [System Audit Report](./docs/AI_MISSION_CONTROL_AUDIT.md)
- [Migration Plan & Inventory](./docs/MIGRATION_PLAN.md)
- [Implementation Progress](./docs/IMPLEMENTATION_PROGRESS.md)
