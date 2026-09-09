# Phase 15 Implementation: Universal Discovery, Relevance & Context Synthesis Layer

## 1. Overview
Phase 15 provides an end-to-end intelligent resource discovery and contextualization layer for the Mission Control Brain. It removes the operational burden of manually supplying MCP servers, steering guidelines, documentation, and tools to agents.

---

## 2. Directory and File Layout

```
Agentic_shared_memory/
├── providers/
│   ├── registry/
│   │   ├── mcp_registry.py          # MCPServer, MCPTool, MCPRegistry
│   │   └── credential_manager.py   # SecretRedactor, KeyringStore, CredentialManager
│   └── mcp/
│       ├── discovery.py             # Read-only configuration discovery engine
│       └── tool_catalog.py          # MCPToolCatalog, ToolSafetyClassifier
├── brain/
│   ├── knowledge/
│   │   ├── steering_registry.py     # SteeringRegistry, SteeringDocument, conflict resolution
│   │   ├── document_registry.py     # DocumentRegistry, indexing, deny-lists
│   │   └── relevance.py             # KnowledgeRelevanceEngine, BM25/keyword ranking
│   ├── resources/
│   │   ├── cli_registry.py          # CLIRegistry, CLICommand discovery
│   │   ├── repo_registry.py         # RepositoryRegistry, git awareness without secrets
│   │   └── resource_graph.py        # UniversalResourceGraph, GraphBuilder
│   ├── context/
│   │   └── context_builder.py       # ResourceSelector, ContextBuilder, TaskContext
│   └── orchestrator/
│       ├── job.py                   # Context field on Job dataclass
│       └── job_manager.py           # Auto-context injection on submit_job
├── scripts/
│   └── brain.py                     # CLI subcommands: mcp, steering, knowledge, tools, resources, context
├── ui/
│   └── dashboard/
│       └── dashboard.py             # REST API endpoints (/api/mcp, /api/tools, etc.)
└── tests/
    ├── unit/                        # 11 Phase 15 unit test suites (389 tests passed)
    └── integration/                 # 4 Phase 15 integration test suites (90 tests passed)
```

---

## 3. Key Components and Interfaces

### 3.1 `MCPRegistry` and `MCPDiscoveryEngine`
- Discovers configurations from `~/.kiro/settings/mcp.json` and Cline storage directories.
- Masks sensitive environment variables using `SecretRedactor`.
- Exposes:
  - `discover_all() -> list[DiscoveredMCPServer]`
  - `get_server(server_id: str) -> MCPServer | None`
  - `check_health(server_id: str) -> dict[str, Any]`

### 3.2 `MCPToolCatalog` and `ToolSafetyClassifier`
- Extracts tools registered under servers, associating parameter schemas and tags.
- Classifies risk levels (`READ_ONLY`, `LOW_RISK_WRITE`, `HIGH_RISK_WRITE`, `DESTRUCTIVE`, `UNKNOWN`).
- Disallows autonomous execution for `DESTRUCTIVE` and `UNKNOWN` tools.

### 3.3 `SteeringRegistry`
- Scans approved project roots for instruction files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `copilot-instructions.md`).
- Parses headings and rules.
- Resolves conflicts according to priority order:
  - `TASK` (60) > `DIRECTORY` (50) > `REPOSITORY` (40) > `PROJECT` (30) > `ORGANIZATION` (20) > `GLOBAL` (10).

### 3.4 `DocumentRegistry` and `KnowledgeRelevanceEngine`
- Recursively indexes markdown, text, yaml, and json files under approved directories (`docs/`, active repo).
- Applies `PATH_DENY_LIST` to avoid crawling sensitive paths (`.git`, `.gemini`, `.ssh`, `credentials`, `tokens`).
- Scores files based on query keyword matching, path relevance, and domain boost.

### 3.5 `CLIRegistry` and `RepositoryRegistry`
- Probes approved CLI binaries (`az`, `git`, `terraform`, `kubectl`, `node`, `npm`, `agy`, `cline`, `curl`, `make`).
- Safe execution only (`which` and `--version` or `git status --porcelain`).
- Sanitizes git remote URLs so that credentials like `https://user:token@github.com` have credentials stripped.

### 3.6 `UniversalResourceGraph`
- In-memory bipartite graph mapping all discovered entities:
  - Nodes: `MCPServer`, `MCPTool`, `SteeringDocument`, `KnowledgeDocument`, `CLICommand`, `Repository`.
  - Edges: `HAS_TOOL`, `TARGETS_PROJECT`, `HAS_STEERING`, `HAS_DOCS`, `UTILIZES_CLI`.
- Enables high-speed entity lookups without repeated disk I/O.

### 3.7 `ContextBuilder` & `ResourceSelector`
- Inputs: Task string, optional repo path, optional preferred provider.
- Processing:
  1. Classifies domain (e.g. `azure`, `docker`, `kubernetes`, `git`, `python`).
  2. Selects top-N relevant MCP servers and tools.
  3. Selects relevant CLI binaries.
  4. Filters steering rules applicable to active directory and task.
  5. Selects top relevant documentation files.
  6. Attaches non-negotiable safety constraints (GUI safety PID 4904, Phase 8 account protection, secret redaction).
- Output: `TaskContext` dataclass.

---

## 4. Verification and Execution Results

### Unit Tests
- `tests/unit/test_mcp_registry.py`
- `tests/unit/test_mcp_discovery.py`
- `tests/unit/test_mcp_tool_catalog.py`
- `tests/unit/test_steering_registry.py`
- `tests/unit/test_knowledge_registry.py`
- `tests/unit/test_resource_selector.py`
- `tests/unit/test_context_builder.py`
- `tests/unit/test_cli_discovery.py`
- `tests/unit/test_repository_discovery.py`
- `tests/unit/test_security_filters.py`
- `tests/unit/test_resource_graph.py`
**Result**: 389 passed, 0 failures.

### Integration Tests
- `tests/integration/test_mcp_integration.py`
- `tests/integration/test_context_integration.py`
- `tests/integration/test_auto_resource_selection.py`
- `tests/integration/test_gui_safety.py`
**Result**: 90 passed, 0 failures.

---

## 5. Backward Compatibility Safeguards
- **Phases 8–11 Integrity**:
  - `ProviderRegistry`, `AccountRegistry`, `CredentialManager`, `ModelRegistry`, `JobManager`, and `SmartRouter` contracts remain intact.
  - Multi-account isolation for Antigravity workers 1, 2, 3 remains intact.
  - Kiro is strictly maintained as `multi_account = False`.
  - GUI session on PID 4904 is preserved with zero disruption.
- **Phases 12–14 Integrity**:
  - Web Mission Control dashboard, usage meters, routing policies, and telemetry models operate without modification.
  - New endpoints (`/api/mcp`, `/api/tools`, `/api/context/preview`) seamlessly attach to the existing server router.
