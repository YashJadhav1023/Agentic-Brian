# Phase 16: Universal Resource & Tool Intelligence Layer

## Overview
Phase 16 establishes the universal resource and tool intelligence layer for Mission Control in `~/YashDevops/Agentic_shared_memory`. It dynamically discovers, catalogs, classifies, and indexes every compute and knowledge asset available on the host machine without mutating any active configurations.

---

## 1. Unified Resource Model
The resource model in [`brain/resources/resource_model.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/resources/resource_model.py) defines normalized representations across 10 distinct resource categories:

- **Resource Types**: `MCP_SERVER`, `MCP_TOOL`, `SKILL`, `DOCUMENTATION`, `STEERING`, `CLI_UTILITY`, `AGENT`, `PROVIDER`, `MODEL`, `REPOSITORY`, `WORKSPACE`.
- **Permission & Risk Levels**:
  - `READ_ONLY`: Pure inspection with zero mutation side-effects.
  - `LOW_RISK_WRITE`: Idempotent or append-only modifications (e.g. logging, scratch notes).
  - `HIGH_RISK_WRITE`: State mutations requiring runtime tracking and verification.
  - `DESTRUCTIVE`: Potentially irreversible operations requiring human approval gates.
- **Trust Levels**: `LOCAL_VERIFIED`, `AUTHENTICATED`, `COMMUNITY`, `UNTRUSTED`.
- **Lifecycle States**: `DISCOVERED`, `ACTIVE`, `DEGRADED`, `DISABLED`, `OFFLINE`.

---

## 2. Resource Discovery Subsystems

### 2.1 MCP Server & Tool Discovery
Located in [`providers/mcp/discovery.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/providers/mcp/discovery.py) and [`providers/mcp/tool_catalog.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/providers/mcp/tool_catalog.py):
- **Sources Scanned**:
  - `~/.gemini/antigravity-cli/mcp/` (11 servers discovered: azure, bitdefender, brain, cloudflare, firecrawl, render, and native tools).
  - Project `.mcp.json` and `mcp.json`.
  - Global configuration references.
- **Tool Cataloging & Safety Classification**:
  - Parses JSON schemas for each tool's input schema and parameters.
  - Automatic `ToolSafetyClassifier` heuristic classifies each tool as `READ_ONLY` or write/destructive based on action semantics.
  - Secrets referenced via `secret://` URIs; zero plaintext secrets stored.

### 2.2 AI Skill Discovery Engine
Located in [`brain/resources/skill_discovery.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/resources/skill_discovery.py):
- Discovers 86 AI skills across safe plugin/builtin roots:
  - `~/.gemini/config/plugins/` (science, firebase, flutter, chrome-devtools).
  - `~/.gemini/antigravity-cli/builtin/skills/` (antigravity-guide, agy-customizations).
- Extracts YAML frontmatter (`name`, `description`) and capabilities without touching `~/.gemini/` root configuration.

### 2.3 CLI & Repository Registries
- **CLI Registry** ([`brain/resources/cli_registry.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/resources/cli_registry.py)): Discovers 17 verified CLI utilities (`git`, `docker`, `kubectl`, `az`, `npm`, `python3`, `gh`, `curl`, `jq`, etc.) and determines risk classifications.
- **Repository Registry** ([`brain/resources/repo_registry.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/resources/repo_registry.py)): Discovers workspace repositories, active branches, and remote URLs.

---

## 3. Universal Resource Registry
The central catalog in [`brain/resources/resource_registry.py`](file:///home/setoo/YashDevops/Agentic_shared_memory/brain/resources/resource_registry.py) maintains in-memory indexes and BM25 search over all resources:
- Indexes 232 total resources (11 MCP servers, 26 MCP tools, 17 CLIs, 5 steering docs, 86 skills, 76 docs, 3 repos, 3 providers, 5 agents).
- Health verification reports 223 healthy resources.
- Thread-safe querying by ID, capability, tag, and lifecycle state.

---

## 4. CLI Verification
```bash
# Discover and index all system resources
python3 scripts/brain.py resources discover

# List discovered resources
python3 scripts/brain.py resources list

# Inspect a specific resource
python3 scripts/brain.py resources inspect mcp_server.brain

# Check tool catalog health
python3 scripts/brain.py tools health
```
