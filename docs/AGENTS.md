# Agent Specifications & Account Isolation

## Active Agent Fleet

The active multi-agent system consists of four first-class headless execution resources:

| Agent Identifier | Provider | Account ID | Execution Mode | Data Directory | Primary Capabilities |
|---|---|---|---|---|---|
| `antigravity-account-1` | Google Antigravity | `account-1` | `headless` | `~/.gemini/antigravity-cli/` | Architecture, Protocol Design, Governance, Deep Reasoning |
| `antigravity-account-2` | Google Antigravity | `account-2` | `headless` | `~/.gemini/antigravity-ide/` | Editor Refactoring, Component Modernization, Architecture, Code Review |
| `kiro-cli` | Kiro | `cli` | `headless` | System PATH (`~/.local/bin/kiro-cli`) | Terminal Operations, Build & Test, Local Validation, DevOps |
| `cline` | Cline | `cli` | `headless` | System PATH (`~/.local/bin/cline`) | Frontend Styling, UI Components, Code Review |

---

## Antigravity Account 2: Native Headless Implementation

### Previous Limitation
Previously, Antigravity Account 2 was utilized solely through the Antigravity IDE GUI, forcing tasks routed to Account 2 to pause for manual in-editor interaction.

### Verified Native Headless Solution
The official Antigravity CLI binary (`agy` or `antigravity`) natively supports the `--app_data_dir` parameter. By providing `--app_data_dir=antigravity-ide`, the CLI automatically mounts the authenticated session belonging to Account 2:

```bash
/home/setoo/.gemini/bin/agy \
  --dangerously-skip-permissions \
  --app_data_dir=antigravity-ide \
  --output-format json \
  --model=gemini-3.8-flash-high \
  -p "Execute task prompt"
```

### Complete Account Isolation
- **No Token Sharing:** Account 1 uses `~/.gemini/antigravity-cli/` while Account 2 uses `~/.gemini/antigravity-ide/`.
- **Distinct Conversation Databases:** Conversations, summaries, and history logs are written to each account's respective subdirectory.
- **Auditable Provenance:** Every task execution explicitly tags the `provider`, `account_id`, `agent_id`, and `actual_model`.
