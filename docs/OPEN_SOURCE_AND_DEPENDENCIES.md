# Open-Source & Dependency Audit

**Repository:** Agentic Shared Memory & Multi-Agent Swarm Brain  
**Date:** September 2026  
**Auditor:** Master AI Systems Architect  
**Architecture Policy:** Zero Bloat, Zero Unnecessary 3rd-Party Python Packages, Strict Localhost Isolation  

---

## 1. Classification Methodology

Dependencies and external projects are strictly categorized according to real repository usage:

* **Category A:** Directly integrated open-source repository / submodule
* **Category B:** Runtime dependency / Host CLI tool
* **Category C:** Development dependency
* **Category D:** Reference / Architectural inspiration
* **Category E:** Evaluated but explicitly NOT used / Prohibited

---

## 2. Dependency Inventory

| Classification | Project / Tool | Repository / Origin | Purpose & Integration Point | License |
| :--- | :--- | :--- | :--- | :--- |
| **Category B** | **Python 3 Standard Library** | [python/cpython](https://github.com/python/cpython) | Entire brain core: `sqlite3` (BM25 FTS5 memory), `http.server` & `socketserver` (Mission Control), `dataclasses`, `subprocess`, `hmac`/`hashlib`, `threading`, `urllib` | PSF License |
| **Category B** | **Git CLI (`git`)** | [git/git](https://github.com/git/git) | Worktree sandboxing, isolated execution branches, diff inspection, and non-destructive merges (`brain/worktree/worktree_manager.py`) | GNU GPL v2 |
| **Category B** | **Google Antigravity CLI (`agy`)** | Google Antigravity IDE Engine | Headless execution adapter for Account 1 (`antigravity-cli`) and Account 2 (`antigravity-ide`) via `--app_data_dir` (`agents/antigravity/adapter.py`) | Proprietary / Google EULA |
| **Category B** | **Kiro CLI (`kiro-cli`)** | Kiro / AWS AI Suite | Headless CLI agent for terminal execution, test running, and local cloud reads (`agents/kiro/adapter.py`) | Proprietary / AWS EULA |
| **Category B** | **Cline CLI (`cline`)** | [cline/cline](https://github.com/cline/cline) | Headless CLI agent for UI refactoring, frontend styling, and code review (`agents/cline/adapter.py`) | Apache 2.0 |
| **Category C** | **`unittest`** | Python Standard Library | Comprehensive test runner for 175+ unit, integration, and security tests | PSF License |
| **Category D** | **BM25 / SQLite FTS5** | [SQLite FTS5](https://www.sqlite.org/fts5.html) | High-speed keyword and BM25 ranking for targeted memory retrieval without vector database bloat | Public Domain |
| **Category D** | **Claude Opus / Sonnet (via Antigravity / Kiro)** | Anthropic | Architectural reasoning and complex decision planning via registered multi-agent CLI adapters | Commercial API EULA |
| **Category E** | **Google Gemini Direct REST/SDK** | google-generativeai / google-genai | **EXPLICITLY NOT USED.** All LLM calls route through isolated CLI subshells (`agy`); direct API keys and HTTP requests to Gemini are prohibited | N/A |
| **Category E** | **Playwright / Puppeteer** | Microsoft Playwright | Evaluated for browser UI testing; omitted due to container CDN driver download limitations. Headless HTTP/DOM & API validation used instead | Apache 2.0 |
| **Category E** | **React / Tailwind CSS / Vue** | Frontend Frameworks | **NOT USED.** Mission Control uses vanilla HTML5, CSS custom properties, and native ES6 JavaScript to ensure zero build-step overhead, zero node_modules, and instant loading on 2 CPU cores | MIT |
| **Category E** | **Redis / Chroma / Pinecone / pgvector** | Vector DBs & Caches | **NOT USED.** SQLite FTS5 with BM25 ranking provides millisecond retrieval with zero host resource footprint | Apache / MIT |

---

## 3. Dependency Verification Invariants

1. **Zero External Python Packages:**
   Running `pip list` or inspecting `import` statements confirms that the entire core runs purely on Python standard library modules. No `pip install` is required to run the Brain or Mission Control.
2. **Deterministic CLI Locators:**
   CLI paths for `agy`, `kiro-cli`, and `cline` are resolved via portable user expansion (`~/.gemini/bin/agy`, `~/.local/bin/kiro-cli`, etc.) or host `$PATH`, with zero hard-coded absolute machine usernames.
3. **Strict License Compliance:**
   No restrictive copyleft licenses are embedded in proprietary or user project code. Git is invoked purely as an external process without linking against `libgit2`.
