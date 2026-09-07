# Isolated Git Worktree Execution & Sandbox Architecture

## 1. Overview and Purpose

When autonomous AI agents execute tasks that modify source code, running them directly inside the canonical checkout poses major hazards:
- Unfinished, malformed, or hallucinated edits can break the active developer environment.
- Uncommitted local edits by the human developer could be overwritten, committed, or deleted.
- Concurrent multi-agent execution would create race conditions on shared files.

Phase 3 introduces **Safe Isolated Git Worktree Sandboxes**. Every mutating task is executed inside a dedicated, isolated git worktree checked out to a deterministic feature branch. The canonical repository working tree remains 100% untouched until human review and approval.

---

## 2. Worktree Sandbox Lifecycle

```
    [TASK DISPATCH] (Mutating)
           │
           ▼
     ┌───────────┐
     │  CREATE   │  Create worktree at runtime/sandboxes/agentic-task-<id>
     └─────┬─────┘  Checkout unique branch agentic/task/<id>
           │
           ▼
     ┌───────────┐
     │  ACTIVE   │  Agent executes inside sandbox with cwd=worktree.path
     └─────┬─────┘
           │
           ▼
     ┌────────────────┐
     │ PENDING_REVIEW │  Snapshot & commit changes onto sandbox branch;
     └───────┬────────┘  Generate unified diff & diffstat
             │
      ┌──────┴─────────────────────────┐
      ▼                                ▼
┌───────────┐                    ┌───────────┐
│ APPROVED  │                    │ REJECTED  │
└─────┬─────┘                    └─────┬─────┘
      │                                │
      ▼                                ▼
┌───────────┐                    ┌───────────┐
│  APPLIED  │                    │ DESTROYED │
└─────┬─────┘                    └───────────┘
      │                                ▲
      │                                │
      └─────────► [ CLEANUP ] ─────────┘
```

### Lifecycle States (`WorktreeStatus`)
1. **`CREATED` / `ACTIVE`**: Dedicated worktree directory created, branch branched off canonical `HEAD` (or specified base commit).
2. **`PENDING_REVIEW`**: Agent finished work. Modified files are staged and committed to the sandbox branch. Diff and diffstat are computed.
3. **`APPROVED` / `APPLIED`**: Human operator reviews diff and approves merge into canonical repository.
4. **`REJECTED`**: Changes are declined. Sandbox worktree is removed and task branch is safely deleted.
5. **`FAILED`**: Execution failed. The worktree is preserved intact for post-mortem forensic inspection.
6. **`CLEANED`**: Stale worktrees (e.g. >24h) are pruned.

---

## 3. Deterministic Naming & Metadata Layout

To prevent collisions and maintain zero-leak git hygiene:
- **Task Branch**: `agentic/task/<task_id>`
- **Worktree Directory**: `runtime/sandboxes/agentic-task-<task_id>`
- **Registry & Metadata**:
  - Global registry: `runtime/sandboxes/worktrees_registry.json`
  - Per-task metadata: `runtime/sandboxes/<task_id>.meta.json`

> [!IMPORTANT]
> **Metadata is Stored Outside the Worktree**: Metadata files are saved in `runtime/sandboxes/`, **never** inside the checked-out worktree directory itself. This ensures `git add -A` and `git status` inside the sandbox never pick up tracking metadata as modified repository code.

---

## 4. Security Invariants and Safety Gates

### Invariant A: Canonical Working Tree Protection
Before applying any sandbox changes (`apply()`):
1. `check_canonical_dirty()` inspects `git status --short` in the canonical repository.
2. If uncommitted local changes exist in the user's working tree, `apply()` raises a strict `RuntimeError`:
   ```
   Cannot apply sandbox changes: canonical working tree has N uncommitted user changes.
   Commit or stash your changes in ... before applying.
   ```
3. Canonical branch is never modified without zero-dirt guarantees.

### Invariant B: Explicit Human Confirmation Required
All destructive or canonical-modifying actions require explicit boolean confirmation:
- `apply(task_id, confirm=True)`
- `reject(task_id, confirm=True)`
- `cleanup(confirm=True)`

Omission of `confirm=True` immediately raises a `ValueError` (CLI exit code 1 or HTTP 400).

### Invariant C: Non-Git Environment Protection
`WorktreeManager.is_git_repository()` verifies repository validity via `git rev-parse --git-dir`. If the root workspace is not a valid git repository, sandboxing fails safely without corrupting filesystem trees.

---

## 5. Operations: CLI & Mission Control

### CLI Commands

```bash
# 1. View all active worktree sandboxes
python3 scripts/brain.py worktree status

# 2. View detailed status of a specific task sandbox
python3 scripts/brain.py worktree status <task_id>

# 3. View unified diff of sandbox changes
python3 scripts/brain.py worktree diff <task_id>

# 4. Approve and merge sandbox changes into canonical repository
python3 scripts/brain.py worktree approve <task_id> --confirm

# 5. Reject and destroy sandbox
python3 scripts/brain.py worktree reject <task_id> --confirm

# 6. Recover an unindexed worktree branch
python3 scripts/brain.py worktree recover <task_id>

# 7. Cleanup stale sandboxes older than 24 hours
python3 scripts/brain.py worktree cleanup --confirm --max-age-hours 24
```

### Mission Control UI Integration
The web dashboard (`http://127.0.0.1:3333`) features a dedicated **Worktree Sandboxes** tab:
- Lists all active and pending-review sandboxes with live status badges.
- **View Diff** button displays formatted unified diffs inside a modal window.
- **Approve & Apply** and **Reject** buttons invoke authenticated API endpoints with human confirmation dialogs.
- Automatic auth token transmission in `Authorization: Bearer <token>` headers.
