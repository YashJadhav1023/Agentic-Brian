# Phase 15 Knowledge & Document Registry

## 1. Overview
The Knowledge subsystem indexes project documentation, runbooks, architecture diagrams, and deployment guides across approved repository roots. Rather than maintaining bloated, uncurated embeddings, the Brain utilizes a fast, deterministic metadata index combined with a multi-factor relevance ranking engine.

---

## 2. Document Discovery & Indexing

### Approved Knowledge Roots
- Repository root (`~/YashDevops/Agentic_shared_memory`)
- `docs/` directories across active projects (`Agentic_os`, `Support_ticket`)
- Architecture guides and runbooks

### Excluded Patterns (`PATH_DENY_LIST`)
- `.git`, `.gemini`, `.ssh`
- `node_modules`, `venv`, `__pycache__`
- `credentials`, `secrets`, `tokens`, `.env`
- Binary artifacts (`.exe`, `.so`, `.png`, `.jpg`, `.zip`)

Currently, 69 high-value documentation files are indexed into memory with cached hashes and modification timestamps.

---

## 3. Knowledge Relevance Engine

Given a user task, the `KnowledgeRelevanceEngine` computes a composite score:

$$\text{Relevance} = W_{\text{keyword}} \cdot S_{\text{term}} + W_{\text{domain}} \cdot S_{\text{domain}} + W_{\text{type}} \cdot S_{\text{type}} + W_{\text{affinity}} \cdot S_{\text{project}}$$

- **Domain Affinity**: Tasks mentioning "Azure", "deployment", or "CI/CD" receive significant affinity boosts for files like `azure-pipelines.yml`, `05-pipeline-review.md`, and deployment runbooks.
- **Type Weighting**: Runbooks and deployment procedures receive higher priority during operational and troubleshooting tasks than generic design docs.
- **Deduplication**: Files with identical content hashes or identical titles across nested symlinks are merged.

---

## 4. CLI Operations

```bash
# Discover and index documentation across approved roots
python3 scripts/brain.py knowledge discover

# List all indexed documentation files
python3 scripts/brain.py knowledge list

# Perform a search across indexed documents
python3 scripts/brain.py knowledge search "Azure deployment"

# Inspect detailed metadata for a specific document
python3 scripts/brain.py knowledge inspect docs_azure-pipelines_1234
```
