# Phase 17: Unified Context & Knowledge Federation

## Overview
Phase 17 implements context federation, knowledge retrieval, provenance tracking, and leak-proof redaction across all Mission Control operations. It enables agents to receive rich, targeted execution contexts tailored to their specialized capabilities while guaranteeing zero secret leaks.

---

## 1. Context Registry
Located in [`brain/context/context_registry.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/context/context_registry.py):
- **Storage**: Stores synthesized `TaskContext` bundles with deterministic IDs (e.g. `ctx-xxxxxxxx`).
- **Provenance Tracking**: Every attached document, steering rule, and MCP tool includes full provenance metadata:
  - `source`: Absolute or relative path on disk.
  - `type`: `steering`, `documentation`, `tool`, or `mcp`.
  - `hash`: SHA-256 hash of the content at time of capture.
- **Token Estimation & Budgeting**: Computes token usage estimates to prevent context window overflow.
- **Redaction Verification**: Verifies that no plaintext API keys, passwords, or connection strings exist within the context bundle before storage.

---

## 2. Inverted BM25 Knowledge Index
Located in [`brain/knowledge/knowledge_index.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/knowledge/knowledge_index.py):
- Fast, in-memory inverted index implementing Okapi BM25 term weighting ($k_1=1.5, b=0.75$).
- Indexes project documentation, runbooks, architectural decision records (ADRs), and steering documents.
- Title terms receive a 3x weight multiplier over body terms.
- Supports term frequency/inverse document frequency ranking with tokenization and stop-word filtering.

---

## 3. Tailored Context Generation
Located in [`brain/context/context_builder.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/context/context_builder.py):
- Synthesizes `TaskContext` bundles tailored to agent specializations:
  - **`cline`**: Scoped to local workspace file editing; filters out cluster-wide deployment docs and infrastructure MCPs.
  - **`kiro-cli`**: Filtered for terminal commands, cloud scripts, Docker/K8s, and cloud MCPs (`azure`, `render`, `cloudflare`).
  - **`antigravity`**: Broad synthesis including architecture, protocol governance, and multi-file dependencies.
- Enforces strict non-negotiable safety constraints in every generated context:
  - Preserving active Antigravity IDE GUI session (`PID 5854`).
  - Read-only secret references via `CredentialManager` (`secret://...`).
  - Blocking destructive operations without explicit confirmation.

---

## 4. CLI Verification
```bash
# Preview synthesized context for a task with ZERO tool execution
python3 scripts/brain.py context preview "Refactor authentication tokens across microservices"

# Explain why resources and steering were selected
python3 scripts/brain.py context explain "Refactor authentication tokens across microservices"

# Search knowledge base via BM25
python3 scripts/brain.py knowledge search "authentication"
```
