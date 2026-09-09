# Phase 15 Security & Safe Execution Policy

## 1. Security Invariants and Principles

Phase 15 strictly adheres to the core mission-control security mandate:

1. **Zero Plaintext Secrets**: Secrets are never saved to disk, logged to standard output, committed to Git, or passed into prompts.
2. **Credential References Only**: All credentials must be addressed via opaque references (e.g. `secret://mission-control/mcp/github`).
3. **No Unsanitized Secret Ingestion**: When reading documentation, configs, or steering files, the `SecretRedactor` automatically strips sensitive tokens (`sk-...`, `AIza...`, `ghp_...`, `Bearer ...`).
4. **Path Deny-Lists**: Scanners strictly refuse to enter sensitive directories, including `.git`, `.gemini`, `.ssh`, `credentials`, `secrets`, `tokens`, `__pycache__`, and `node_modules`.
5. **No Dangerous Execution During Discovery**: Discovery operations are purely read-only inspection of configurations and binaries. No external MCP tools or destructive scripts are run.
6. **GUI and Keyring Isolation**: The active Antigravity GUI process (PID `4904`), the GNOME keyring `service=gemini`, and Phase 8 user-data profile directories are protected and untouched.

---

## 2. Tool Risk Classification Framework

Every discovered MCP and CLI tool is classified into one of five safety tiers by `ToolSafetyClassifier`:

| Safety Level | Characteristics | Autonomous Execution | Examples |
|---|---|---|---|
| `READ_ONLY` | Read-only inspection, listing, getting state. No side effects. | Permitted with standard logging | `list_resources`, `get_resource`, `search_notes`, `git status` |
| `LOW_RISK_WRITE` | Local state changes that are reversible or easily contained. | Permitted with audit logging | `create_file`, `write_note`, `git checkout -b` |
| `HIGH_RISK_WRITE` | Cloud infrastructure deployment, mutation of external records, or software changes. | Requires explicit plan approval | `deploy_template`, `trigger_deploy`, `create_dns_record`, `terraform apply` |
| `DESTRUCTIVE` | Resource deletion, table dropping, endpoint isolation, service termination. | **Blocked autonomously**. Requires operator confirmation. | `delete_resource`, `delete_dns_record`, `drop_database`, `rm -rf` |
| `UNKNOWN` | Unrecognized tool naming or unverified capability. | **Blocked autonomously**. Defaults to human review. | Custom or unclassified external plugins |

---

## 3. Path and File Sanitization

The `DocumentRegistry` and `SteeringRegistry` enforce `PATH_DENY_LIST`:
- `.git`
- `.gemini`
- `.ssh`
- `credentials`
- `secrets`
- `tokens`
- `node_modules`
- `venv`
- `__pycache__`
- `id_rsa`, `id_ed25519`
- `.env`
- `.pem`, `.key`

Files exceeding size limits (default 500 KB) are bypassed to avoid denial-of-service or memory bloat.

---

## 4. Context Preview Safety Guarantee

The `brain context preview "<task>"` command and the `/api/context/preview` API endpoint operate under a strict **Zero-Execution** constraint:
- They construct the `TaskContext` entirely in memory from pre-indexed catalogs.
- No network requests to MCP servers are dispatched.
- No shell subprocesses beyond read-only version checks are run.
- Zero tools are triggered during context generation.

---

## 5. Active GUI Protection Verification

Before and after every execution, runtime safeguards confirm:
- Antigravity GUI PID `4904` is running continuously with unmodified start time (`24336`).
- GNOME keyring `service=gemini` is unaltered.
- Profile directories `~/.gemini/antigravity-account-*` have unchanged modification timestamps.
