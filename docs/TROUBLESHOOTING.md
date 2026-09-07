# Troubleshooting

## Account 2 task succeeds but returns nothing

**Symptom** — exit `0`, `status: SUCCESS`, empty response, and stderr containing:

```
no output produced — a tool required the "command" permission that headless mode
cannot prompt for, so it was auto-denied.
```

**Cause** — headless print mode has nobody to approve a tool permission prompt,
so the tool is auto-denied. The task cannot read files, so it has nothing to say.

**This system marks that run FAILED**, not completed, and appends a hint. Fixes,
least privileged first:

1. Add a scoped rule under `permissions.allow` in the account profile's
   `settings.json` (preferred — allows only what is needed).
2. Re-run the task with `--allow-tool-permissions`.
3. Set `dangerously_skip_permissions: true` for that account in
   `config/providers.json` (broadest; affects every task on the account).

Pure reasoning tasks need none of this.

## `actual_model` is always `unknown`

Working as intended. The Antigravity JSON payload has no model field, and neither
Kiro nor Cline report one. `requested_model` records what was asked for.
Fabricating `actual_model` would corrupt every downstream record. See
`docs/MODEL_ROUTING.md`.

## Agent reported OFFLINE

```bash
python3 scripts/brain.py health --agent antigravity-account-2 --deep
```

- *binary not found* — check `command` / `fallback_commands` in
  `config/providers.json`; confirm `which agy`.
- *profile directory does not exist / not readable / not writable* — confirm
  `~/.gemini/antigravity-ide` exists with the right ownership.
- *session unusable* — the account's CLI session needs re-authentication. Do that
  through the CLI itself; this system never touches credentials.
- *session probe timed out* — network or provider outage; retry, the result is
  cached for 300 s.

## Kiro run fails on the model

```
The model 'claude-opus4.6' is not available. Please use '/model' to select a different model
```

That is the machine's stored Kiro default, not this repository. The adapter now
always passes `--model` explicitly (including `auto`) to bypass a stale default.
Valid ids are listed in `config/providers.json`.

## Task stuck in BLOCKED

Either the agent failed its health pre-flight, or a file lock could not be
acquired. Check `task.errors`, then `ls locks/`. Locks carry a TTL and reclaim
themselves; a task can simply be re-planned.

## Task stuck in RUNNING after a crash

```bash
python3 -c "from tasks.manager import TaskManager; print(TaskManager().recover_orphaned_tasks())"
```

## `continue` picks the wrong thing

`continue --dry-run` prints the resolved source, target agent, conversation and
next action. Precedence: matching handoff, then failure recovery, then latest
handoff, then task assignment. `CANCELLED` tasks are skipped — cancel a task you
do not want resumed:

```bash
python3 -c "
from tasks.manager import TaskManager, TaskStatus
TaskManager().update_status('task-xxxxxxxx', TaskStatus.CANCELLED, error='not needed')"
```

## Conversation not resumed across a handoff

Expected when the handoff moves to a different agent. Conversation ids are
account-scoped and are never replayed against another account. The next agent
receives the handoff and relevant memory instead.

## Mission Control shows no agents

Check that `config/providers.json` parses and that accounts are `enabled`.
`list_active_adapters()` only returns agents that are both enabled and healthy.

## Tests write into real state

They must not. Every test uses a `TemporaryDirectory`. If you see new files in
`tasks/` or `handoffs/` after a test run, that test is constructing a manager
without an explicit root.
