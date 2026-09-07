# Security Review — Phase 2

Reviewed before committing Account 2 integration. Scope: this repository plus the
subprocesses it launches.

## 1. What `--dangerously-skip-permissions` actually does

Investigated rather than assumed. From `agy --help`:

> `--dangerously-skip-permissions  Auto-approve all tool permission requests without prompting`

In headless print mode there is no human to answer a permission prompt, so any
tool needing one is auto-denied. The flag removes that gate for **every** tool,
for the whole run — it is not scoped to a directory or a tool class.

**It is not required for Account 2 to work.** Verified live: a prompt-only task
returned exit 0 with a correct response and no such flag. It is only needed when
a task must actually use tools, e.g. read files.

### Decision

`dangerously_skip_permissions: false` for both accounts in
`config/providers.json`. Escalation is explicit and visible:

| Level | Mechanism | Blast radius |
|---|---|---|
| default | — | no tool escalation |
| **preferred when tools are needed** | `permissions.allow` rules in the account profile's `settings.json` | only the named tools |
| per task | `brain.py plan … --allow-tool-permissions` | all tools, one task |
| per account | `dangerously_skip_permissions: true` in config | all tools, every task on that account |

The scoped allow-list is the least-privileged option that works, and is the
documented recommendation. It is **not** applied by this repository because it
modifies the user's account profile outside the project; that is the user's call.

The flag is emitted from exactly two places, both gated by
`_resolve_skip_permissions()`. `agents/antigravity/base_adapter.py`, which passed
it unconditionally, was deleted. The swarm previously enabled it silently
whenever a task declared files; that was removed.

## 2. Credential handling

- Nothing reads, copies, exports or inspects credentials, tokens, cookies,
  keyrings or browser storage. Authentication is entirely the CLI's business.
- Health checks touch the profile directory's existence and permission bits
  only. The deep probe runs the CLI's own `models` subcommand; it never opens a
  credential file.
- No account spoofing, no bypass of provider restrictions, no authentication
  bypass. Isolation uses the vendor's own `--app_data_dir` mechanism.
- Prompts are redacted from any recorded argv (`<prompt redacted>`), so a task
  record or event never leaks prompt content.

## 3. Secret scan before commit

```
grep -rInE "(api[_-]?key|secret|passwd|password|token|bearer|aok_|AKIA|-----BEGIN|GEMINI_API_KEY)[\"' ]*[:=]" \
  --include="*.py" --include="*.json" --include="*.md" .
```

No credential assignments in source, config, tests or docs. `.gitignore` covers
`*.key`, `*.pem`, `*.crt`, `*service-account*.json`, `secrets*`, `creds*`, `.env`
and all `*.db` files. A Mission Control test asserts the API surface contains no
credential-shaped keys.

## 4. Subprocess safety

- Every invocation uses `subprocess.run`/`Popen` with an **argv list**. No
  `shell=True` anywhere, so prompt content cannot be interpreted as shell syntax.
- No `eval` or `exec` anywhere.
- Every call has an explicit timeout; timeouts are recorded as exit 124.
- Environment is inherited (`os.environ.copy()`), never constructed with secrets.

## 5. Network exposure

- Mission Control binds `127.0.0.1` only. Not reachable off-host.
- **Flagged:** its `POST` endpoints (`/api/route`, `/api/dispatch`,
  `/api/continue`) are unauthenticated and dispatch real agent work. Any local
  process or user can trigger a task. Acceptable for a loopback developer tool;
  authentication must be added before binding to any other address. Documented in
  `docs/UI.md` and asserted by a test that the bind address is loopback.
- No outbound network calls other than the agent CLIs talking to their own
  providers. No project code, secrets or user data are sent anywhere else.

## 6. Honesty as a safety property

A system that reports work it did not do is dangerous in a different way. Three
false-success paths were removed:

1. `actual_model` echoed the requested model with "(verified via CLI
   invocation)" appended, despite the provider never reporting a model.
2. Exit 0 with an empty response was reported as success — the exact shape of a
   permission-denied headless run.
3. A non-`SUCCESS` provider status with exit 0 was reported as success.

All three now fail loudly, with the provider's own diagnostic preserved.

## 7. Blast radius of the default posture

With escalation off, an Account 2 task cannot modify files, run commands, or
mutate any external system. It can consume model quota, and it can write to this
repository's own task, session, memory, handoff and event files. That is the
intended footprint.

## 8. Residual risks

| Risk | Severity | Mitigation |
|---|---|---|
| Unauthenticated loopback POST endpoints | low (local only) | documented; add auth before changing the bind address |
| `--allow-tool-permissions` grants all tools, not a subset | medium when used | prefer `permissions.allow`; opt-in per task; recorded in the task and the handoff |
| Escalated tasks run in the working tree with no sandbox | medium when used | the legacy system's git-worktree sandbox should be ported before escalated runs become routine (`docs/MIGRATION_PLAN.md`) |
| Model quota consumption | low | bounded concurrency of 2; deep health probes use the free `models` call and cache for 300 s |
