# Smart Model Routing Policy

## Model Tier Classification

Models are ranked into standard tiers to balance capability, latency, and cost:

| Policy Tier | Strength | Use Case | Typical Models |
|---|---|---|---|
| **FRONTIER** | 5 | Deep reasoning, system architecture, mission-critical logic | `claude-opus-5`, `claude-opus-4-6-thinking`, `gpt-5.6-sol`, `gemini-3.1-pro-high` |
| **ADVANCED** | 4 | Complex coding, multi-file refactoring, large context | `claude-sonnet-5`, `claude-sonnet-4.6`, `gpt-5.6-terra`, `gemini-3.8-flash-high` |
| **BALANCED** | 3 | Standard implementations, daily tasks | `claude-sonnet-4.5`, `gemini-3.8-flash-medium`, `gemini-3.7-flash-high` |
| **FAST** | 2 / 1 | Quick edits, sanity checks, test scaffolding | `claude-haiku-4.5`, `gemini-3.8-flash-low`, `gpt-5.6-luna`, `glm-5.3-flash` |

## Model Routing Pipeline

```
Task Description ──▶ Complexity Classifier ──▶ Select Model ──▶ Pass --model Flag ──▶ Verify Actual Model
```

- **Pass-through Guaranteed:** The computed model is passed explicitly via the CLI's `--model` parameter.
- **Verification:** The orchestrator never blindly assumes the requested model was used. It parses the actual model from execution payloads and records it in task history.
