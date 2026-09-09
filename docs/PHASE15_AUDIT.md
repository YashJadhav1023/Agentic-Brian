# Phase 15 System Audit: Universal MCP, Steering, Tools, Documentation & Knowledge Integration Layer

**Date:** 2026-09-08  
**Scope:** Phase 15 Architecture Integration across `~/YashDevops/Agentic_shared_memory` and connected environments.  
**Governing Rule:** Read-only audit; zero modification of running Antigravity GUI (PID 4904), Phase 8 frozen profiles, GNOME keyring `service=gemini`, or credential stores.

---

## 1. Existing MCP Servers Discovered

Based on filesystem inspections across user configurations and connected repositories, the following real MCP servers are configured:

| Server ID | Host Configuration Source | Transport / Executable | Detected Arguments | Capabilities / Tools Provided |
| :--- | :--- | :--- | :--- | :--- |
| **`azure`** | `~/.kiro/settings/mcp.json`, `Agentic_os/.../mcp_config.json` | stdio (`npx`) | `-y @azure/mcp@3.0.0-beta.28` | Azure resource management, resource groups, storage, AKS, VM inspection, ARM deployments. |
| **`firecrawl`** | `~/.kiro/settings/mcp.json`, `Agentic_os/.../mcp_config.json` | stdio (`npx`) | `-y firecrawl-mcp@3.24.0` | Web scraping, crawling, markdown extraction, search. |
| **`render`** | `~/.kiro/settings/mcp.json`, `Agentic_os/.../mcp_config.json` | stdio (`npx`) | `-y mcp-remote` | Cloud hosting, web services, Postgres, logs, deploys. |
| **`cloudflare`** | `~/.kiro/settings/mcp.json`, `Agentic_os/.../mcp_config.json` | stdio (`npx`) | `-y @cloudflare/mcp-server-cloudflare` | DNS, Workers, KV, Cloudflare API management. |
| **`bitdefender`** | `~/.kiro/settings/mcp.json` | stdio (`node`) | `/home/setoo/.local/share/bitdefender-mcp/index.js` | Endpoint security, scan tasks, network inventory, quarantine. |
| **`brain` / `basic-memory`**| `~/.cline/data/settings/cline_mcp_settings.json`, `Code/User/mcp.json` | stdio (binary) | `/home/setoo/.local/bin/basic-memory mcp --project ...` | Persistent semantic memory, notes, context graph. |
| **`omniroute`** | `~/.kiro/settings/mcp.json` | stdio (`npx`) | `-y mcp-remote` | Dynamic API routing and remote proxying. |
| **`betterclaw`** | `~/.kiro/settings/mcp.json` | stdio (`npx`) | `-y @betterclaw-ai/cli` | Autonomous tool coordination and execution. |
| **`kirocrew-core`** | `~/.kiro/settings/mcp.json` | stdio (`kirocrew`) | `mcp-core` | Multi-agent coordination and task delegation. |
| **`kirocrew-cron`** | `~/.kiro/settings/mcp.json` | stdio (`kirocrew`) | `mcp-cron` | Recurring scheduled jobs and cron automation. |
| **`kirocrew-computer`** | `~/.kiro/settings/mcp.json` | stdio (`kirocrew`) | `mcp-computer` | Local desktop automation and shell execution. |

*Safety verification:* All credentials referenced in these configs are environment-variable references (`FIRECRAWL_API_KEY`, `RENDER_API_KEY`, `CLOUDFLARE_API_TOKEN`, etc.). No plaintext secrets are imported into Mission Control.

---

## 2. Existing MCP Configurations Discovered

The following configuration files were discovered:
1. `~/.cline/data/settings/cline_mcp_settings.json` (Format: standard VS Code / Cline MCP schema: `{"mcpServers": {...}}`)
2. `~/.config/Code/User/mcp.json` (Format: VS Code user configuration: `{"servers": {...}}`)
3. `/home/setoo/YashDevops/Agentic_os/.agents/plugins/kiro-mcp/mcp_config.json` (Format: Agentic OS MCP plugin schema)
4. `/home/setoo/YashDevops/Agentic_os/.kiro/settings/mcp.json` (Format: Kiro IDE / CLI MCP settings: `{"mcpServers": {...}}`)
5. `/home/setoo/YashDevops/Agentic_os/.kiro/hooks/azure-mcp-routing.json` (Format: Hook routing configuration for Azure MCP)
6. `~/YashDevops/Agentic_shared_memory/mcp/` (Pre-existing directory skeleton: `configs/`, `servers/`, `tools/`)

---

## 3. Existing Steering Files Discovered

The following steering and instruction files were identified across the workspace and user environment:
1. `/home/setoo/YashDevops/Agentic_os/AGENTS.md` (Scope: Repository / Organization instructions for autonomous agents)
2. `/home/setoo/YashDevops/Agentic_os/.github/copilot-instructions.md` (Scope: Repository coding standards and patterns)
3. `/home/setoo/YashDevops/Neo-check/neocheck-backend/claude.md` (Scope: Project-specific Claude steering and backend architecture rules)
4. `/home/setoo/YashDevops/docs/AGENTS.md` (Scope: Root workspace agent operational guidelines)
5. `/home/setoo/YashDevops/Agentic_shared_memory/docs/AGENTS.md` (Scope: Mission Control agent adapter contracts and isolation guidelines)

---

## 4. Existing Tool Definitions Discovered

### 4.1 CLI Tools on System PATH
- **`git`**: `/usr/bin/git` (v2.55.0) — Version control and worktree sandboxing.
- **`terraform`**: `/home/setoo/.local/bin/terraform` (v1.9.5) — Infrastructure as code for Azure and cloud.
- **`az`**: `/home/setoo/.local/bin/az` — Microsoft Azure Command-Line Interface.
- **`kubectl`**: `/home/setoo/.local/bin/kubectl` — Kubernetes cluster control.
- **`agy`**: `/home/setoo/.local/bin/agy` (v1.1.27) — Antigravity agent CLI.
- **`cline`**: `/home/setoo/.local/bin/cline` — Cline autonomous coding agent CLI.
- **`node` & `npm`**: `/home/setoo/.local/bin/node` (v22.22.2), `/home/setoo/.local/bin/npm` (12.0.2) — Node runtime for npx-based MCP servers.
- **`python3`**: `/usr/bin/python3` (Python 3.14.7) — Core execution engine.
- **`make`**: `/usr/bin/make` (4.4.1) — Build scripts.
- **`curl`**: `/usr/bin/curl` (8.22.0) — HTTP operations.

### 4.2 Local Project Scripts
- `~/YashDevops/Agentic_shared_memory/scripts/brain.py` — Central CLI management for providers, accounts, models, jobs, routing, usage, quotas, and dashboard.
- `~/YashDevops/Agentic_shared_memory/scripts/benchmark.py` — Pipeline performance benchmark.
- `~/YashDevops/Agentic_shared_memory/scripts/live_pipeline_demonstration.py` — Live pipeline validation.
- `~/YashDevops/docs/build_all_in_one_billing.py` — Cloud billing extraction.
- `~/YashDevops/docs/generate_steering_billing.py` — Cloud steering and cost reporting.

---

## 5. Existing Documentation Discovered

1. **Mission Control Architecture (`~/YashDevops/Agentic_shared_memory/docs/`)**:
   - `ARCHITECTURE.md`, `ORCHESTRATION.md`, `MODEL_ROUTING.md`, `PROVIDERS.md`, `AGENTS.md`, `SECURITY.md`, `OPERATIONS.md`, `UI.md`, `HANDOFFS.md`, `MEMORY.md`, `WORKTREE_SANDBOX.md`
   - `PHASE12_MISSION_CONTROL_UI.md`, `PHASE13_INTELLIGENT_ROUTING.md`, `PHASE14_USAGE_COST_QUOTAS.md`
   - `ROUTING_ARCHITECTURE.md`, `USAGE_ARCHITECTURE.md`, `OPEN_SOURCE_ARCHITECTURE.md`
2. **Infrastructure, DevOps & Cloud Runbooks (`~/YashDevops/docs/`)**:
   - `00-summary-and-recommendations.md` through `15-dev-cost-optimization-summary.md` covering:
     - AKS Cluster status & stability (`01-cluster-status.md`, `06-pod-stability.md`)
     - Microservice & Redis connectivity (`02-microservice-connectivity.md`, `03-redis-connectivity.md`)
     - CI/CD Pipeline review (`05-pipeline-review.md`)
     - Environment configuration (`07-env-vars.md`)
     - Production load testing (`07-prod-load-test-baseline.md`, `08-prod-load-test-report.md`)
     - Infrastructure cost & Azure billing analysis (`11-prod-infra-cost-review.md` to `15-dev-cost-optimization-summary.md`)
   - `PROJECT.md`, `KIRO_SETUP.md`, `TEST_INFRA.md`, `TEST_READY.md`
   - `TALLYPRIME_INTEGRATION_POC_GUIDE.md`

---

## 6. Connected Repositories / Projects

Under `~/YashDevops/`:
1. **`Agentic_shared_memory`** (Current working directory, Git branch: `main`)
   - Universal AI Mission Control control plane, smart routing, multi-account isolation, provider abstractions.
2. **`Agentic_os`** (Git repository, detached head)
   - Multi-agent orchestration workflows, Kiro MCP plugins, agent templates.
3. **`Support_ticket`** (Git repository, branch: `main`)
   - Ticket processing and automation service.
4. **`Neo-check`** (Local project directory)
   - Backend verification service containing `neocheck-backend/claude.md`.

---

## 7. Existing Agents

In `~/YashDevops/Agentic_shared_memory/agents/`:
- **`Antigravity`**: 3 worker instances (`antigravity-account-1`, `antigravity-account-2`, `antigravity-account-3`).
- **`Cline`**: 3 isolated accounts (`cline-account-1`, `cline-account-2`, `cline-account-3`).
- **`Kiro`**: 1 single-account CLI agent (`kiro-cli`).
- **`OpenHands`**: Wrapped agent runtime via git worktree sandbox.

---

## 8. Existing Providers

In `~/YashDevops/Agentic_shared_memory/providers/`:
- `antigravity` (Category: `AGENT`)
- `cline` (Category: `AGENT`)
- `kiro` (Category: `AGENT`)
- `openai` (Category: `API`)
- `gemini` (Category: `API`, native adapter)
- `anthropic` (Category: `API`, native adapter)
- `litellm` (Category: `GATEWAY`, HTTP proxy bridge)
- `ollama` (Category: `LOCAL_MODEL`, registered, currently disabled due to daemon offline)

---

## 9. Existing Integration Mechanisms

- **`ProviderRegistry`** (`providers/registry/provider_registry.py`): Central registration and lookup.
- **`AccountRegistry`** (`providers/registry/account_registry.py`): Account status, cooldown, and metadata tracking.
- **`ModelRegistry`** (`providers/registry/model_registry.py`): Discovered model registry with capabilities.
- **`CredentialManager`** (`providers/registry/credential_manager.py`): Secure secret handling via `service=mission-control` and encrypted store (`0600`).
- **`SmartRouter`** (`brain/router/smart_router.py`): 16-factor scoring engine with deterministic failover.
- **`JobManager`** (`brain/orchestrator/job_manager.py`): Async execution lifecycle and job tracking.
- **`UsageTracker` & `QuotaManager`** (`brain/analytics/`): Observability and quota enforcement.
- **`SharedMemoryStore`** (`memory/store/memory_store.py`): Thread-safe semantic and task memory with SQLite persistence.

---

## 10. What Can Safely Be Integrated

1. **Read-Only MCP Server & Tool Discovery**:
   - Parse approved MCP configuration JSON files (`~/.cline/.../cline_mcp_settings.json`, `~/.kiro/.../mcp.json`, `Agentic_os/.../mcp_config.json`).
   - Register discovered servers into `MCPRegistry`.
   - Index discovered tools with risk classification (`READ_ONLY`, `LOW_RISK_WRITE`, `HIGH_RISK_WRITE`, `DESTRUCTIVE`, `UNKNOWN`).
2. **Steering Document Discovery**:
   - Index approved markdown steering files (`AGENTS.md`, `CLAUDE.md`, `copilot-instructions.md`, etc.).
   - Calculate hierarchical scope (`GLOBAL` -> `ORGANIZATION` -> `PROJECT` -> `REPOSITORY` -> `DIRECTORY` -> `TASK`).
   - Detect conflicting instructions.
3. **Documentation Registry & Search**:
   - Index markdown and text documents in approved roots (`~/YashDevops/docs/`, `~/YashDevops/Agentic_shared_memory/docs/`).
   - Index document metadata (title, summary, tags, modified time, content hash).
   - Pluggable relevance scoring (BM25 / TF-IDF keyword overlap + semantic capability scoring).
4. **Local CLI Tool Discovery**:
   - Safely detect available CLI binaries (`git`, `az`, `terraform`, `kubectl`, `node`, `npm`, `agy`, `cline`) using `shutil.which` and non-destructive version flags.
5. **Context Builder & Auto-Selector**:
   - Build `TaskContext` combining winning agent/provider, relevant steering, relevant documents, relevant MCPs, and relevant CLI tools.
   - Context preview without executing tools.

---

## 11. What Must Remain Isolated

1. **Running Antigravity GUI (PID 4904)**:
   - Must never be killed, restarted, or sent inputs.
   - Profile `~/.gemini` remains strictly read-only and unindexed.
   - GNOME keyring `service=gemini` remains completely untouched.
   - Phase 8 accounts remain frozen.
2. **Kiro Multi-Account**:
   - Remains strictly single-account (`multi_account=false`).
3. **Cline Account Directories**:
   - Profiles (`~/.mission-control/cline/account-1/2/3`) remain isolated.
4. **Credential Secrets**:
   - Never index private keys (`id_rsa`, `.pem`), `.env` files with secrets, or token files into knowledge base.

---

## 12. Unsupported Formats & Boundaries

- Binary file formats (.docx, proprietary .pdf binary streams) are indexed by metadata and path only without raw byte ingestion.
- Dynamic tool discovery via active invocation is disallowed during discovery (static schema/config inspection only).
- Non-standard JSON configs or broken JSON files must be flagged with `status="error"` and skipped safely.

---

## 13. Security Risks & Mitigation

| Risk | Impact | Mitigation in Phase 15 |
| :--- | :--- | :--- |
| **Accidental credential indexing** | Exposure of API keys or OAuth tokens in task context | Strict path deny list: exclude `.env*`, `*credential*`, `*key*`, `*secret*`, `*token*`, `~/.gemini/`, `~/.ssh/`. Scan content with `SecretRedactor`. |
| **Automatic destructive execution** | Accidental data loss (`rm -rf`, `terraform destroy`, `az group delete`) | Strict Tool Safety Classification (`READ_ONLY`, `LOW_RISK_WRITE`, `HIGH_RISK_WRITE`, `DESTRUCTIVE`, `UNKNOWN`). `ContextBuilder` and preview never auto-execute destructive tools. |
| **Conflicting steering rules** | Hallucinated or contradictory agent behavior | Steering conflict detection engine: record contradictions explicitly in `SteeringConflict` and expose them rather than silently merging. |
| **Excessive context size** | Token window exhaustion / degraded prompt performance | Ranked resource selection: apply strict relevance thresholds and token caps. |

---

## 14. Recommended Integration Architecture

```
User Task ("Deploy the application to Azure")
   │
   ▼
[Task Classifier & SmartRouter] ──► Selects Agent/Provider (e.g. Antigravity/Cline/API)
   │
   ▼
[Phase 15 Resource Selector]
   ├──► MCPRegistry & Tool Catalog ──► Discovers 'azure' MCP, inspects schemas
   ├──► SteeringRegistry ───────────► Finds AGENTS.md, extracts Azure guidelines
   ├──► DocumentRegistry ───────────► Finds '05-pipeline-review.md', '11-prod-infra-cost-review.md'
   ├──► CLIRegistry ────────────────► Detects '/home/setoo/.local/bin/az', 'terraform'
   ├──► RepositoryRegistry ─────────► Identifies 'Agentic_shared_memory' git context
   └──► SharedMemoryStore ──────────► Retrieves short-term / project context
   │
   ▼
[ContextBuilder] ──► Assembles TaskContext (ranked, de-duplicated, safety-classified)
   │
   ▼
[AgentAdapter / JobManager] ──► Dispatches TaskContext to Agent for execution
```

The audit confirms that the system is ready for Phase 15 implementation following the exact order outlined in the specification.
