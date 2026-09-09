# Open-Source Architectural Evaluation & Reference Study
**Phase 9: Universal AI Mission Control**

---

## 1. Executive Summary

Mission Control Phase 9 establishes a universal provider orchestration layer capable of managing:
1. **Agent / IDE Providers:** Antigravity (multi-account A1, A2, A3), Kiro, Cline, and OpenHands.
2. **Direct API Providers:** OpenAI, Google Gemini, Anthropic, Azure OpenAI, OpenRouter, AWS Bedrock, Groq, Mistral, Ollama, and any OpenAI-compatible endpoint.

Rather than reinventing architectural wheels or hardcoding ad-hoc integrations, we conducted a systematic study of three industry-leading open-source AI orchestration engines:
- **LiteLLM** (`https://github.com/BerriAI/litellm`): Universal API gateway, model routing, load balancing, virtual keys, and budgets.
- **OpenHands** (`https://github.com/OpenHands/OpenHands`): Autonomous agent server, workspace sandboxing, and agent execution abstractions.
- **Cline** (`https://github.com/cline/cline`): VSCode-native multi-provider client, API integration layers, and provider selection.

This document records the architectural findings, trade-off analyses, and design decisions synthesized into Mission Control.

---

## 2. LiteLLM Architectural Study

### 2.1 Provider Abstraction & Model Routing
- **Pattern:** LiteLLM establishes a unified schema translating incoming OpenAI-standard requests into provider-specific REST payloads (Anthropic Messages API, Google AI Studio/Vertex API, Bedrock Converse API, Azure deployments).
- **Multiple Deployments & Routing:** LiteLLM's `Router` maintains a deployment list where a logical model name (e.g. `claude-3-5-sonnet`) maps to multiple weighted targets with health status, RPM/TPM trackers, and cool-down states.
- **Fallbacks:** In LiteLLM, if a primary deployment throws an `AuthenticationError`, `RateLimitError`, or timeout, the router iterates through an explicit `fallbacks` list:
  ```python
  fallbacks = [
      {"gpt-4o": ["claude-3-5-sonnet", "gemini-1.5-pro"]}
  ]
  ```
- **Virtual Keys & Budgets:** LiteLLM generates internal virtual keys (`sk-litellm-...`) that enforce user/team budgets, max spend thresholds, and token rate limits without revealing underlying provider master keys.

### 2.2 Mission Control Evaluation & Design Decision
- **Evaluation:** LiteLLM is a feature-rich, high-performance gateway. However, bundling `litellm` directly as a hard Python dependency brings heavy transitives (Pydantic v2, Tiktoken, Aiohttp, Prisma, etc.) which risks dependency conflicts in lightweight agent environments.
- **Decision:**
  - **Native Providers via Stdlib:** Mission Control implements native, zero-dependency REST providers (`OpenAICompatibleProvider`, `GeminiAPIProvider`, `AnthropicAPIProvider`) using Python's standard `urllib.request` with streaming SSE.
  - **Optional LiteLLM Gateway:** Mission Control provides `LiteLLMGatewayProvider` (`providers/api/litellm_gateway.py`). Users who operate a self-hosted or cloud LiteLLM proxy can connect Mission Control to it simply by configuring `type: "litellm"` and their proxy's base URL. This yields the best of both worlds: zero mandatory bloat, full gateway compatibility.

---

## 3. OpenHands Architectural Study

### 3.1 Agent Server & Control Center Architecture
- **Pattern:** OpenHands decouples the user-facing interface from the execution engine via an EventStream architecture. The OpenHands Backend operates as an ASGI server that manages agent state machines, conversation threads, and tool executions.
- **Workspace Isolation:** OpenHands isolates agent actions within ephemeral Docker containers or localized sandbox environments, intercepting file modifications and command executions.
- **Remote vs Local Agents:** OpenHands treats agent runtimes as pluggable backends: an agent can execute locally on the host, inside a Docker container, or remotely via an HTTP/WebSocket session.

### 3.2 Mission Control Evaluation & Design Decision
- **Evaluation:** OpenHands provides a mature paradigm for agent-level execution (as opposed to token-level API calls). It complements IDE agents (Antigravity, Kiro, Cline) by offering a standalone autonomous containerized agent.
- **Decision:**
  - Mission Control implements `OpenHandsAdapter` and `OpenHandsProvider` (`agents/openhands/adapter.py`).
  - It exposes OpenHands behind the universal `AIProvider` and `AgentAdapter` contracts.
  - Mission Control's git worktree sandboxing (`WorktreeManager`) parallels OpenHands' container isolation, providing isolated branch execution with automated review, diffing, and approvals.

---

## 4. Cline Architectural Study

### 4.1 Provider Architecture & API Integration
- **Pattern:** Cline implements a clean provider factory (`ApiHandler` interface) with concrete implementations for:
  - Anthropic (`anthropic.ts`)
  - OpenAI / OpenAI-compatible (`openai-native.ts`)
  - Google Gemini (`gemini.ts`)
  - AWS Bedrock (`bedrock.ts`)
  - OpenRouter (`openrouter.ts`)
  - Ollama / LM Studio (`ollama.ts`)
- **Key Architectural Strengths:**
  - **Strict Separation of Secrets:** Cline stores credentials in VSCode's SecretStorage (`keytar` backend), never in settings JSON or commit history.
  - **Model Catalogs:** Each provider declares context window limits, cost per 1M tokens (input/output), and capability flags (vision, tool calling).
  - **Config Validation:** Validates API keys and endpoints before attempting task execution.

### 4.2 Mission Control Evaluation & Design Decision
- **Evaluation:** Cline's provider architecture is exceptionally clean and mirrors our requirements for credential isolation and model capability tracking.
- **Decision:**
  - Mission Control's `CredentialManager` mirrors the SecretStorage pattern: secrets are referenced via `secret://mission-control/<provider>/<account>` and persisted in OS Keyring (`secret-tool`) or encrypted local stores (`0600` PBKDF2 machine-salted), never in `providers.json`.
  - Mission Control's `ModelRegistry` adopts Cline's context window and cost metadata patterns to prepare for Phase 10 cost-optimized routing.

---

## 5. Architectural Synthesis in Mission Control

| Concept | Open-Source Inspiration | Mission Control Implementation | Benefit |
| :--- | :--- | :--- | :--- |
| **Provider Abstraction** | LiteLLM `CustomLLM` & Cline `ApiHandler` | `AIProvider` base class (`providers/base.py`) | Unified contract across IDEs, CLI agents, and direct APIs. |
| **Secret Isolation** | Cline `SecretStorage` | `CredentialManager` (`secret://` URIs) | Zero token leakage in git, config, terminal, or logs. |
| **Account Pools** | LiteLLM Deployment Router | `AccountPool` (`providers/registry/account_registry.py`) | Multi-account load balancing, cooldown, and failure tracking. |
| **Failover Routing** | LiteLLM Explicit Fallback Chain | Deterministic failover in `JobManager` | Resilient execution without silent or unauthorized switches. |
| **Agent Server** | OpenHands Agent REST API | `OpenHandsAdapter` & `AgentProviderBridge` | Unifies autonomous agent servers with IDE workers. |
| **Gateway Support** | LiteLLM Proxy Gateway | `LiteLLMGatewayProvider` & `OpenAICompatibleProvider` | Direct connection to self-hosted LLMs, vLLM, Ollama, OpenRouter. |

---

## 6. Code Reuse and License Compliance

- All inspected repositories (LiteLLM, OpenHands, Cline) operate under permissive open-source licenses (MIT and Apache 2.0).
- In accordance with safety rules, **no proprietary or foreign code was blindly copied**. All abstractions were custom-architected to natively integrate with Antigravity's multi-account session engine, TaskManager, and EventBus.
- Zero third-party dependencies were introduced into Mission Control; all HTTP streaming and REST communication is implemented via Python standard library `urllib.request`.
