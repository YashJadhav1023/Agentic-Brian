# Agent Specifications & Account Isolation

## Active Agent Fleet

Four first-class headless execution resources. Every one of them is driven the
same way: the brain talks only to `AgentAdapter`, and every adapter is
configured from `config/providers.json`.

| Agent Identifier | Provider | Account | Mode | Profile / Executable | Primary Capabilities |
|---|---|---|---|---|---|
| `antigravity-account-1` | Google Antigravity | `account-1` | `headless` | `~/.gemini/antigravity-cli/` | Architecture, protocol design, governance, deep reasoning, documentation |
| `antigravity-account-2` | Google Antigravity | `account-2` | `headless` | `~/.gemini/antigravity-ide/` | Editor & component refactoring, code review, architecture, deep reasoning, test scaffolding |
| `kiro-cli` | Kiro | `cli` | `headless` | `~/.local/bin/kiro-cli` | Terminal operations, build & test, local validation, cloud/k8s reads |
| `cline` | Cline | `cli` | `headless` | `~/.local/bin/cline` | Frontend styling, component refactoring, code review |

---

## Antigravity Account 2: native headless execution

### What changed

Account 2 previously required the Antigravity IDE GUI, so any task routed to it
stalled until a human worked it by hand. It is now a headless agent like any
other. The IDE does not need to be installed, running, or opened, and nothing in
this system talks to it. `antigravity-ide` is only the *name of the profile
directory* that isolates the account.

### Verified invocation

```bash
~/.gemini/bin/agy \
  --app_data_dir=antigravity-ide \
  --output-format json \
  --model=gemini-3.8-flash-low \
  -p "Return exactly: ACCOUNT2_INTEGRATION_TEST_PASSED"
```

Live result: exit `0`, and this payload shape:

```json
{
  "conversation_id": "612548ad-84d1-4d32-96b5-c68d97bbef6b",
  "status": "SUCCESS",
  "response": "ACCOUNT2_INTEGRATION_TEST_PASSED\n",
  "duration_seconds": 5.576408773,
  "num_turns": 1,
  "usage": {
    "input_tokens": 14535, "output_tokens": 80, "thinking_tokens": 69,
    "cache_read_tokens": 8150, "total_tokens": 14615
  }
}
```

Two facts follow from that payload and both are load-bearing:

1. **`--dangerously-skip-permissions` is not required.** Account 2 answers
   without it, so it is `false` by default for both accounts.
2. **The payload carries no model field.** The model that actually served a
   request is therefore *not verifiable*, and is recorded as `unknown`. The
   requested model is recorded separately. See `docs/MODEL_ROUTING.md`.

### Conversation resume

`--conversation=<id>` resumes an account's conversation and prior turns are
retained — verified live by asking a follow-up that could only be answered from
the earlier turn. Conversation ids are account-scoped and are never replayed
against a different account.

### Permissions: the one real constraint

In headless print mode there is nobody to approve a tool permission prompt, so
any tool needing one is **auto-denied**. When that happens the CLI exits `0`
with `status: SUCCESS` and an *empty* response. This system treats an empty
response as a failure, because reporting it as done would be false.

Three execution postures, least privileged first:

| Posture | How | Effect |
|---|---|---|
| Default | nothing | No tool escalation. Pure reasoning tasks work; tasks needing to read files return nothing and are marked FAILED with a hint. |
| Scoped allow-list (**preferred** when tools are needed) | add a rule under `permissions.allow` in the account profile's `settings.json` | Only the named tools are approved. Not configured by this repository — it modifies your account profile. |
| Full escalation | `brain.py plan ... --allow-tool-permissions`, or `dangerously_skip_permissions: true` for the account in `config/providers.json` | Auto-approves every tool. Deliberate, per task or per account. Never the default. |

Verified live: the same review task returned nothing under the default posture
and produced a full repository assessment (119,875 tokens, 92 s) with
`--allow-tool-permissions`.

---

## Account isolation

- Account 1 uses `~/.gemini/antigravity-cli/`, Account 2 uses
  `~/.gemini/antigravity-ide/`. The two are never mixed.
- `--app_data_dir` is emitted from exactly one function,
  `AntigravityAccountAdapter._base_argv()`. No other code in this repository
  constructs an Antigravity command line.
- Each account keeps its own `conversations/<uuid>.db` and `brain/<uuid>/`
  inside its own profile.
- Every task record, event, memory entry, session record and handoff carries
  `agent_id` **and** `account_id`.
- Nothing in this system reads, copies or exports credentials, tokens, cookies
  or browser storage. Authentication is left entirely to the CLI and the OS
  keyring.

---

## Adapter interface

```
AgentAdapter
  agent_id / provider / account_id / execution_mode / profile_dir
  capabilities()        declared, provider-neutral capabilities
  available_models()    configured model catalogue
  health(deep=False)    shallow filesystem checks; deep adds a session probe
  status(task_id=None)  IDLE | WORKING | FAILED | ...
  execute(...)          single-shot run -> TaskExecutionResult
  continue_session(...) resume a provider conversation/session
  stream(..., on_event) incremental events (NDJSON where supported)
  cancel(task_id)       clears local in-flight state only
  describe()            registry/UI-facing description
```

`AntigravityAdapter` is the provider-level factory; `AntigravityAccountAdapter`
is the per-account implementation. Account 1 and Account 2 are the *same class*
with different configuration.
