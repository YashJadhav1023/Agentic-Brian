# Providers & Provider Registry

## One configuration source

`config/providers.json` is the single source of truth for providers, accounts,
execution flags, capabilities and model catalogues. No adapter hard-codes a CLI
flag, a profile path or a model id.

```json
{
  "providers": {
    "antigravity": {
      "command": "~/.gemini/bin/agy",
      "profile_root": "~/.gemini",
      "accounts": {
        "antigravity-account-2": {
          "agent_id": "antigravity-account-2",
          "account_id": "account-2",
          "execution": {
            "app_data_dir": "antigravity-ide",
            "output_format": "json",
            "dangerously_skip_permissions": false
          },
          "capabilities": ["component-refactoring", "code-review", "..."],
          "models": ["gemini-3.1-pro-high", "..."],
          "default_model": "gemini-3.8-flash-medium"
        }
      }
    }
  }
}
```

Loaders:

- `providers/registry/config.py` — provider-neutral read-only views
  (`get_accounts`, `get_account`, `max_concurrent_agents`).
- `agents/antigravity/adapter.py` — `AntigravityAdapter.load_from_config()`
  builds one adapter per enabled account.
- `providers/registry/bootstrap.py` — assembles the whole registry from config.

Setting `"enabled": false` on a provider or an account removes it from the
registry, the router and the UI. No code change.

## Registry

`ProviderRegistry` holds providers, each holding adapters keyed by `agent_id`.

```
ProviderRegistry
  register_provider / register_adapter
  get_adapter(agent_id) / get_provider(provider_id)
  list_providers() / list_active_adapters()   # active = enabled AND healthy
  to_dict()                                   # Mission Control serialization
```

Each registered agent exposes: `agent_id`, `provider`, `account_id`, `profile`,
`execution_mode`, `status`, `capabilities`, `models`, `health`, `current_task`,
`current_conversation`.

## Adding a future provider

Not implemented today; the shape is fixed so the core brain never changes.

```
REGISTER -> AUTHENTICATE -> HEALTH CHECK -> DISCOVER MODELS
  -> DISCOVER CAPABILITIES -> REGISTER AGENT -> ROUTER -> MISSION CONTROL
```

1. Add a provider entry to `config/providers.json` with accounts, capabilities
   and models.
2. Implement `AgentAdapter` (`execute`, `continue_session`, `health`, `cancel`,
   plus optional `status` / `stream`). Return `TaskExecutionResult`.
3. Register it in `providers/registry/bootstrap.py`.

The router, task system, memory, handoffs, events and UI all consume
`AgentAdapter` and `ProviderRegistry` only, so nothing else needs to change.

**Gemini API is deliberately not implemented.** It is recorded in
`future_providers` in the config as `"enabled": false`, `"status":
"not-implemented"` so no code path can pick it up by accident.

## Model catalogue honesty

A model id is only offered if it has been observed from the provider:

| Agent | Enumerated with | Models |
|---|---|---|
| `antigravity-account-1` | `agy --app_data_dir=antigravity-cli models` | 14 |
| `antigravity-account-2` | `agy --app_data_dir=antigravity-ide models` | 14 (identical set) |
| `kiro-cli` | `kiro-cli chat --model <invalid>` (the CLI lists valid ids in its error) | 15 |
| `cline` | not enumerable from the CLI | `auto` only |

A test asserts that every model in a routing catalogue exists in the configured
list, so an unverified id cannot be routed to.
