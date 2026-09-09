# Open-Source Architecture & Dependency Policy

**Scope:** Mission Control universal provider, account, credential, and routing layer (Phases 10–14).
**Governing rule:** Prefer small, maintainable abstractions over unnecessary dependencies. **WRAP > IMPORT**.

---

## 1. Runtime Dependency Position

Mission Control's core provider and orchestration layer runs on the **Python standard library only**.
Every HTTP provider in `providers/api/` is implemented on `urllib.request` (including SSE streaming), credential management interfaces directly with system secret storage via `secret-tool` or local encrypted vaults, and data persistence uses local JSON storage.

**Strict Rule:** No large third-party framework may be imported directly into the core runtime. Transitive dependencies introduced into `providers/` or `brain/` pollute sandboxed execution environments and expand the supply-chain threat surface.

---

## 2. Evaluation of Open-Source Candidates

Before implementing new functionality, external open-source candidates were comprehensively evaluated against our security, architectural, and dependency criteria:

| Candidate | Purpose | License | Security Considerations | Dependency Impact | Useful Components | Decision & Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **LiteLLM** | Universal LLM gateway, load balancing, fallback routing | MIT (with commercial `enterprise/` carveout) | PyPI package was compromised in March 2026 (malicious versions 1.82.7, 1.82.8 published with `litellm_init.pth` auto-execution hook). | Very high (Pydantic v2, tiktoken, aiohttp, prisma, cryptography). | Router deployment pattern, explicit fallback chain design. | **WRAP (Never Import)**. Driven as an external HTTP proxy if configured by operator. Core adopts the architectural pattern without importing code. |
| **OpenHands** | Autonomous code-agent software SDK | MIT | Executes untrusted agent-authored code; requires elevated Docker/Kubernetes runtime permissions. | Heavy Python/TypeScript SDK tree. | Agent-level task execution abstraction. | **WRAP**. Integrated over subprocess/REST boundary via `agents/openhands/adapter.py`. Local execution uses throwaway git worktrees (`WorktreeManager`). |
| **Cline** | Autonomous coding agent with multi-file refactoring | Apache-2.0 | Holds live credentials and extension state. Must not overwrite user sessions or leak tokens. | None on Python (runs as installed CLI / extension). | Per-account settings hierarchy, task execution interface. | **WRAP**. Phase 11 established 3 isolated profiles (`cline-account-1/2/3`) with separate config/data directories. Controlled via CLI subprocess. |
| **Kiro** | Headless cloud/terminal operations agent | Proprietary / CLI | Undocumented internal credential and state storage. High risk of cross-account contamination if forced. | None on Python (CLI subprocess). | Runtime model discovery via CLI error inspection. | **WRAP (Single-Account Only)**. Maintained as single-account adapter (`multi_account=false`) because isolated profile roots cannot be safely verified. |
| **Open WebUI** | Self-hosted Web UI for LLMs and multi-modal chats | MIT | Heavy web stack requiring container or Node/Python daemon; browser-focused rather than headless agent orchestration. | High (FastAPI, PyTorch, Ollama client, Node/Svelte build). | UI design ideas for chat and model parameter controls. | **REJECT / AVOID**. Incompatible with standard-library loopback UI constraint. Mission Control UI is built directly in `ui/dashboard/dashboard.py`. |
| **Continue** | Open-source AI code assistant (VS Code / JetBrains) | Apache-2.0 | Extension runtime coupled to IDE windowing systems; complex local proxy configuration. | High (TypeScript extension core + Python backend dependencies). | Prompt context providers and tab-autocomplete concepts. | **REJECT / AVOID**. Cline and Antigravity already fulfill agentic and IDE execution. Wrapping Continue adds redundant maintenance overhead. |
| **Aider** | Command-line pair programming agent | Apache-2.0 | Directly modifies git repositories and commits automatically without prior review. | Moderate (gitpython, prompt_toolkit, litellm). | Git diff formatting and benchmark prompt conventions. | **WRAP (Subprocess Only if Needed)**. Do not import in-process. Worktree sandboxing must gate any automated git-committing tool. |
| **LangChain** | LLM orchestration and prompt-chaining framework | MIT | Extremely large dependency tree; fast API deprecation cycle; abstract indirection overhead. | Very High (LangSmith, Pydantic, Tenacity, PyYAML, etc.). | Conceptual abstractions for tool calling. | **REJECT / AVOID**. Mission Control's routing, memory, and orchestration needs are already solved by stdlib-based modules (`SmartRouter`, `SharedMemory`). |
| **LlamaIndex** | Data framework for LLM-based RAG and indexing | MIT | Complex retrieval abstraction tree; heavy embeddings and vector store requirements. | Very High (fsspec, SQLAlchemy, Tiktoken, etc.). | Document parsing paradigms. | **REJECT / AVOID**. Retrieval in this repository is specialized for agent shared memory and already implemented in `memory/retrieval/retriever.py`. |

---

## 3. Decision Summary & Policy

| Decision | Frameworks | Handling Rule |
| :--- | :--- | :--- |
| **WRAP** | LiteLLM, OpenHands, Cline, Kiro, Aider | Run as external CLI subprocess or external HTTP gateway. Never import into Python runtime. Validate and sanitize all inputs/outputs. |
| **REJECT / AVOID** | Open WebUI, Continue, LangChain, LlamaIndex | Do not install or depend upon. Implement focused, minimal standard-library equivalents. |

**Net third-party dependencies added to repository:** **Zero (0).**

---

## 4. Known Provider Limitations Recorded Honestly

- **Antigravity**: Phase 8 accounts (`antigravity-account-1`, `antigravity-account-2`, `antigravity-account-3`) are completely frozen. Main GUI session (PID `4904`) is protected and untouched.
- **Cline**: Phase 11 implemented multi-account isolation across `cline-account-1`, `cline-account-2`, and `cline-account-3` via isolated config and data directories (`~/.mission-control/cline/account-X/config`).
- **Kiro**: Verified as single-account only (`multi_account=false`, reason: `"isolated profile root not safely verified"`). No synthetic multi-account isolation is faked.
- **Ollama**: Registered as local category provider (`LOCAL_MODEL`). Default status is disabled/unavailable if daemon is offline, never faking live model availability.
