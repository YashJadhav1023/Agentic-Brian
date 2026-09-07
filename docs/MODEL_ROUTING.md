# Model Routing & Agent Selection

## Agent selection is a competition

Every healthy agent is scored for every task. Account 2 wins work on merit; you
do not have to name it.

| Component | Weight | Meaning |
|---|---|---|
| keyword affinity | +10 | phrasing that signals an agent's specialty |
| capability overlap | +4 per capability | capabilities the task needs vs the agent declares |
| strength fit | +3 | the agent's catalogue reaches the tier the task needs |
| load penalty | −6 | the agent is currently `WORKING` |

Capabilities are inferred from the *task*, never from the agent, so the two
signals stay independent.

Verified routing:

| Instruction | Winner | Why |
|---|---|---|
| `Draft architecture RFC for secure cross-agent communication` | `antigravity-account-1` | architecture + protocol-design |
| `Refactor telemetry and component handlers across microservices` | `antigravity-account-2` | editor + component refactoring |
| `Run pytest suite and verify docker container health` | `kiro-cli` | build-and-test, terminal, local-validation |
| `Fix CSS flexbox styling on the navigation layout` | `cline` | frontend-styling |
| `Review this architecture` | `antigravity-account-2` | code-review **and** architecture beats architecture alone |
| `Review and refactor this service` | `antigravity-account-2` | 3/3 required capabilities |

Explicit selection still wins outright:

```bash
python3 scripts/brain.py plan "..." --agent antigravity-account-2 --model gemini-3.1-pro-high
```

An unknown or unhealthy explicit agent raises instead of silently rerouting.

## Complexity tiers

| Complexity | Target strength | Trigger phrasing |
|---|---|---|
| `fast` | 2 | quick fix, typo, lint, format, sanity |
| `standard` | 3 | default |
| `strong` | 4 | restructure, comprehensive, full audit, major rewrite, across every |
| `reasoning` | 5 | deep reasoning, formal proof, mission critical, high risk |

Selection is deterministic: closest strength, then specialization match, then
catalogue order.

## Verified model catalogues

Both Antigravity accounts expose the same 14 ids, enumerated live with
`agy --app_data_dir=<profile> models`:

```
gemini-3.1-pro-high   gemini-3.1-pro-low
claude-opus-4-6-thinking   claude-sonnet-4-6
gemini-3.8-flash-high/medium/low
gemini-3.7-flash-high/medium/low
gemini-3.6-flash-high/medium/low
gpt-oss-120b-medium
```

Account 2 is **not** pinned to `gemini-3.8-flash-low`. Its selected model varies
by complexity, and `--model=<id>` is passed through to the CLI (verified live:
`--model=gemini-3.8-flash-low` returned `thinking_tokens: 0`, versus `69` on the
medium default).

A test asserts every catalogue model exists in `config/providers.json`, so an
unverified id cannot be routed to.

## Requested vs actual model

The Antigravity JSON payload contains **no model field**. So:

```
requested_model = what the router asked for      e.g. gemini-3.8-flash-low
actual_model    = what the provider reported     -> "unknown"
```

`verify_actual_model()` reads `actual_model`, `model`, `model_used`,
`model_version` or `engine` from the payload and returns `UNKNOWN_MODEL`
otherwise. It never echoes the requested model back. The same rule applies to
Kiro and Cline, neither of which reports a model either.

This is deliberate: a fabricated `actual_model` would make every downstream
record — task, event, handoff, UI — quietly wrong.

## Model events

`MODEL_SELECTED` is emitted for every task, with
`ANTIGRAVITY_ACCOUNT2_MODEL_SELECTED` alongside it for Account 2.
