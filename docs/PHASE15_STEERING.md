# Phase 15 Steering & Instruction Hierarchy

## 1. Overview
Steering files guide agent behavior, constraints, formatting rules, and execution boundaries. Phase 15 introduces a structured `SteeringRegistry` that discovers instruction files, parses discrete rules, establishes clear precedence, and exposes conflicts explicitly rather than silently dropping rules.

---

## 2. Supported Steering Formats and Files

The registry automatically identifies and classifies:
- `AGENTS.md` (Agent instructions and role definitions)
- `CLAUDE.md` (Anthropic Claude specific workflows)
- `GEMINI.md` (Google Antigravity/Gemini specific workflows)
- `KIRO.md` (Kiro agent instructions)
- `CLINE.md` (Cline autonomous agent workflows)
- `.github/copilot-instructions.md` (Repository-level assistant guidelines)

---

## 3. Precedence Hierarchy

When multiple steering files apply to a task, rules are prioritized based on proximity to the execution target:

```
┌────────────────────────────────────────────────────────┐
│ TASK (Priority 60)                                     │
│ Explicit per-task prompt constraints                   │
├────────────────────────────────────────────────────────┤
│ DIRECTORY (Priority 50)                                │
│ Directory-specific rules (e.g. docs/AGENTS.md)         │
├────────────────────────────────────────────────────────┤
│ REPOSITORY (Priority 40)                               │
│ Repo root instructions (e.g. repo/AGENTS.md)           │
├────────────────────────────────────────────────────────┤
│ PROJECT (Priority 30)                                  │
│ Multi-repo workspace or solution guidelines           │
├────────────────────────────────────────────────────────┤
│ ORGANIZATION (Priority 20)                             │
│ Organization-wide security or coding standards         │
├────────────────────────────────────────────────────────┤
│ GLOBAL (Priority 10)                                   │
│ Host/User-level fallbacks (~/.config/steering)         │
└────────────────────────────────────────────────────────┘
```

More specific scopes override broader scopes where rules directly address the same concern.

---

## 4. Conflict Detection (`SteeringConflict`)

Rather than silently allowing contradicting instructions to produce erratic agent behavior, the Brain evaluates rules against existing policies:
- If a broader rule states *"Always ask for confirmation before modifying files"*, but a directory-specific rule states *"Autonomous execution is enabled for scratch directories"*, a conflict is logged.
- The conflict records:
  - `source_a` vs `source_b`
  - `rule_a` vs `rule_b`
  - Assigned resolution (higher priority scope takes precedence)
  - Exposed directly in `TaskContext` so the LLM is conscious of the policy resolution.

---

## 5. CLI Operations

```bash
# Discover all steering documents across approved paths
python3 scripts/brain.py steering discover

# List active steering documents and view conflict summaries
python3 scripts/brain.py steering list

# Inspect a specific steering document's extracted rules
python3 scripts/brain.py steering inspect agentic_os_agents_83be5f05
```
