# Handoffs & Universal Continue

## One handoff store

`handoffs/current.md` (readable) and `handoffs/current.json` (machine-readable)
are written together on every successful task. The previous pair is archived to
`handoffs/archive/handoff_<timestamp>.{md,json}`. There is no competing handoff
database, and no agent keeps its own.

The JSON sidecar exists so Universal Continue resumes *structurally* instead of
scraping prose.

## Record fields

| Field | Content |
|---|---|
| `task` / `objective` | what was asked |
| `completed` | executing agent + account + provider, requested vs reported model, response excerpt |
| `files_modified` | files the task declared and locked |
| `tests` | verification command to re-run |
| `errors` | errors accumulated on the task |
| `decisions` | choices the next agent must not silently reverse (e.g. least-privilege posture, session↔conversation binding) |
| `relevant_memory` | memory ids used and created |
| `git_state` | compact one-line summary, e.g. `41 uncommitted change(s) on main` — never a raw status dump |
| `remaining_work` | what is left |
| `next_action` | one concrete action, including the conversation id to resume when one exists |
| `recommended_agent` / `recommended_model` | who should pick it up |
| `task_id`, `agent_id`, `account_id`, `session_id`, `conversation_id` | execution identity |

A completed task hands off to a *different* agent (`kiro-cli` verifies other
agents' work; Account 1 verifies Kiro's), so the chain moves forward instead of
looping on itself.

## Universal Continue

```bash
python3 scripts/brain.py continue --dry-run   # show the resume plan
python3 scripts/brain.py continue             # resume and execute
```

Sources reconstructed, in this order of precedence:

1. A handoff whose `task_id` matches the selected task — strongest signal.
2. A `FAILED` or `BLOCKED` task — recovery comes before unrelated work. The
   next action names the task and its last error.
3. The latest handoff.
4. The selected task's own assignment.
5. Nothing — a safe generic action.

Task selection order: `RUNNING`, `READY`, `BLOCKED`, `FAILED`, `COMPLETED`.
`CANCELLED` tasks are excluded, because cancelling is a decision, not unfinished
work.

Also gathered: git state (compact), unresolved errors, remaining work,
relevance-filtered memory, and the session mapping.

**Conversation ids never cross accounts.** A conversation is only offered for
resume when it belongs to the target agent; otherwise the next agent starts
fresh with the handoff as context.

The user never has to restate the task.

## Verified live chain

```
antigravity-account-2  (review, 92 s, 119,875 tokens)
   -> handoff -> recommended kiro-cli
kiro-cli               (verification, 38 s)
   -> handoff -> recommended antigravity-account-1
antigravity-account-1  (verification report, 152 s, 307,561 tokens)
```

Account 1 independently noticed that the earlier Kiro run had returned an empty
response — which is exactly the signal a handoff chain is supposed to carry.
