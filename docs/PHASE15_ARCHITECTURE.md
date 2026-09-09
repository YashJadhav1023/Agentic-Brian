# Phase 15 Architecture: Universal MCP, Steering, Tools, Documentation & Knowledge Integration Layer

## 1. Executive Summary

Phase 15 transforms the **Mission Control Brain** into an intelligent, centralized discovery and orchestration layer. Previously, agents operated with manually assigned tools, hardcoded prompt instructions, or isolated provider contexts. Under Phase 15, the Brain automatically analyzes tasks, explores the host environment for available MCP servers, tools, steering instructions, project documentation, repositories, and CLI utilities, and synthesizes a high-relevance, security-scoped `TaskContext` without requiring the user to explicitly specify them.

All discoveries are **read-only**, **non-destructive**, **credential-safe**, and completely isolated from active GUI sessions (`PID 4904`), GNOME Gemini keyrings, and Phase 8 multi-account profiles.

---

## 2. Universal Architecture & Execution Flow

```mermaid
graph TD
    User([User Task / Prompt]) --> Brain[Mission Control Brain]
    Brain --> TaskAnalyzer[Task Analyzer & Domain Classifier]
    TaskAnalyzer --> ResourceSelector[Resource Selector]
    
    subgraph Registries & Discovery [Unified Registries]
        ResourceSelector --> Agents[Agent Registry]
        ResourceSelector --> Providers[Provider Registry]
        ResourceSelector --> Accounts[Account Registry]
        ResourceSelector --> MCPReg[MCP Registry]
        MCPReg --> MCPTools[MCP Tools & Risk Classifier]
        ResourceSelector --> ToolReg[CLI & Script Registry]
        ResourceSelector --> SteeringReg[Steering & Instruction Registry]
        ResourceSelector --> KnowledgeReg[Knowledge & Doc Registry]
        ResourceSelector --> RepoReg[Repository Registry]
        ResourceSelector --> Memory[Shared Memory & Vector/BM25]
    end
    
    ResourceSelector --> RelevanceEngine[Multi-Factor Relevance Engine]
    RelevanceEngine --> ContextBuilder[Context Builder]
    ContextBuilder --> TaskContext[TaskContext (Read-Only Preview / Injected)]
    TaskContext --> SelectedAgent[Selected Agent & Provider Adapter]
    SelectedAgent --> Execution([Safe Execution Pipeline])
```

---

## 3. Core Component Subsystems

### A. MCP Registry & Discovery (`providers/registry/mcp_registry.py`, `providers/mcp/discovery.py`)
- **Metadata Standard**: Models `MCPServer`, `MCPTool`, `MCPResource`, and `MCPPrompt` with transport type (`stdio`/`sse`), health status, and authorization references.
- **Config Discovery**: Discovers MCP servers from standard configuration files (`~/.kiro/settings/mcp.json`, `~/.config/Code/User/globalStorage/rooveterinaryinc.cline/settings/cline_mcp_settings.json`, and project configs).
- **Safety**: Pure read-only discovery. Never executes tools or modifies discovered JSON files.

### B. MCP Tool Catalog & Safety Classifier (`providers/mcp/tool_catalog.py`)
- **Categorization**: Analyzes schemas and operation names into:
  - `READ_ONLY` (e.g. `list_resources`, `get_resource`, `search_notes`)
  - `LOW_RISK_WRITE` (e.g. `create_file`, `git status`)
  - `HIGH_RISK_WRITE` (e.g. `deploy_template`, `trigger_deploy`, `write_note`)
  - `DESTRUCTIVE` (e.g. `delete_resource`, `delete_dns_record`, `drop_database`)
  - `UNKNOWN` (default for unrecognized destructive actions)
- **Safety Policy**: Unknown and destructive tools are never automatically invoked without explicit operator escalation.

### C. Steering Registry & Precedence (`brain/knowledge/steering_registry.py`)
- **Precedence Hierarchy**:
  ```
  TASK (Priority 60)
    ↓
  DIRECTORY (Priority 50)
    ↓
  REPOSITORY (Priority 40)
    ↓
  PROJECT (Priority 30)
    ↓
  ORGANIZATION (Priority 20)
    ↓
  GLOBAL (Priority 10)
  ```
- **Conflict Tracking**: Identifies semantic or contradictory rules between scopes (e.g. directory rule overriding repo-level rule) and records them in `SteeringConflict` without silent masking.

### D. Knowledge Document Registry & Relevance (`brain/knowledge/document_registry.py`, `relevance.py`)
- **Approved Roots**: Scans repository trees and approved project directories.
- **Deny-Lists**: Enforces absolute exclusions for `.git`, `.gemini`, `node_modules`, `venv`, `credentials`, `secrets`, `tokens`, `.ssh`, and private keys.
- **Relevance Scoring**: Combines domain token matching, BM25-like keyword presence, project boundary affinity, and doc type boost (e.g., CI/CD runbooks for deployment tasks).

### E. CLI & Repository Registries (`brain/resources/cli_registry.py`, `repo_registry.py`)
- **Safe CLI Probing**: Verifies availability via `shutil.which` and non-mutating `--version` commands for tools like `git`, `az`, `terraform`, `kubectl`, `node`, etc.
- **Repository Detection**: Extracts Git branch, remote origin (with tokens and embedded credentials sanitized), and working tree state.

### F. Universal Resource Graph (`brain/resources/resource_graph.py`)
- **Graph Topology**: Represents the entire operating ecosystem as a directed bipartite graph connecting tasks, agents, providers, accounts, MCP servers, tools, steering documents, knowledge files, repositories, and CLI tools.
- **Dynamic Exploration**: Powers complex relational queries such as: "Which MCP tools are attached to servers capable of cloud deployment?"

### G. Context Builder & Auto-Selection (`brain/context/context_builder.py`)
- **Zero-Spec Task Synthesis**: Given only a task string (e.g., `"diagnose Azure deployment failure"`), the builder:
  1. Classifies task domain and intent.
  2. Resolves active repository context.
  3. Queries the resource graph and relevance engines.
  4. Applies security policies and safety constraints.
  5. Packages the resulting `TaskContext` containing relevant MCP servers, tools, steering rules, documentation, and CLI tools.
  6. Supports a strictly read-only preview mode (`brain context preview "<task>"`) where zero tools or subprocesses are executed.

---

## 4. Integration with Job Manager & Smart Router
- In `brain/orchestrator/job.py`, `Job` contains an optional `context: TaskContext | None`.
- In `brain/orchestrator/job_manager.py`, `submit_job()` automatically invokes `ContextBuilder.build_task_context()` if no context was pre-supplied.
- The downstream agent adapter receives the rich context, enabling the model to directly execute against pre-selected tools and relevant documentation without prompt bloating.
