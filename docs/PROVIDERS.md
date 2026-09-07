# Provider Registry & Future Extensibility

## Architectural Philosophy

The Agentic Shared Memory platform is designed so that the core brain orchestrator and router have zero provider-specific hard-coding. Agents register their capabilities and available models through the `ProviderRegistry`.

## Current Provider Fleet

Currently, the active execution pool is strictly:
1. `Google Antigravity` (Account 1 & Account 2)
2. `Kiro` (CLI)
3. `Cline` (CLI)

*Note: In accordance with project instructions, Gemini API is excluded from the active pool.*

## Adding a Future Provider (e.g. Gemini API, OpenAI API, Anthropic API)

To add a new provider without modifying any core brain logic:
1. Implement the `AgentAdapter` interface in `providers/adapters/<new_provider>.py`.
2. Configure credentials via environment variables (e.g., in `.env` or system keyring).
3. Register the adapter in `providers/registry/bootstrap.py`:
   ```python
   new_provider = Provider(id="openai", name="OpenAI", description="OpenAI API")
   new_provider.add_adapter(OpenAIAdapter())
   registry.register_provider(new_provider)
   ```
4. The Smart Router, Swarm Worker Pool, and Mission Control UI will automatically discover the new provider, display its health, and route tasks to it.
