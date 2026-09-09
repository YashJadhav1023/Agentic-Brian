# Phase 12 Open-Source Research

## Evaluation of Open Source Projects for Mission Control

| Project | License | Provider/Account Support | Decision | Justification |
| :--- | :--- | :--- | :--- | :--- |
| **LiteLLM** | MIT (carve-out for enterprise) | 100+ providers, virtual keys, model routing | **WRAP** | Directly importing adds heavy transitives (Pydantic v2, Tiktoken) causing dependency bloat. We will provide `LiteLLMGatewayProvider` to communicate with a user-hosted proxy via HTTP instead. |
| **OpenHands** | MIT | Containerized agent workspaces | **WRAP** | Already adapted via `OpenHandsAdapter`. Mission Control treats it as an external agent backend to preserve our `WorktreeManager` sandbox isolation model. |
| **Cline** | Apache 2.0 | VSCode-native multi-provider client | **WRAP (Read-Only)** | Already integrated. We read its config state and isolate via `--config`/`--data-dir` but never mutate its native VSCode secret state to prevent logging out the user. |
| **Continue** | Apache 2.0 | IDE extension (VSCode/JetBrains) for LLM interactions | **REJECT / AVOID** | Provides excellent local completions, but requires embedding deeply into the IDE process. Re-implementing their API bindings would duplicate our `OpenAICompatibleProvider`. We won't bundle it, but if running locally, we can route to it if it exposes an API. |
| **Open WebUI** | MIT | Comprehensive frontend for LLMs (Ollama, OpenAI) | **REJECT / OPTIONAL-INTEGRATE** | Heavy JS/Python stack (FastAPI, Vue) with its own database. Our `ui/dashboard/dashboard.py` is an intentionally lightweight loopback GUI. We avoid importing this to maintain our zero-dependency backend rule, but we will natively support connecting to Ollama, which Open WebUI pairs well with. |

## Strategy
Mission Control relies exclusively on the **Python standard library** for API connections. We will implement `OpenAICompatibleProvider` native REST streaming logic, allowing Mission Control to wrap existing gateways (LiteLLM, Ollama) directly over HTTP without installing their client SDK packages.
