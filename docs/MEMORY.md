# Shared Memory System

## Memory Scopes

1. **`GLOBAL`**: Permanent system conventions, core architectural rules, global standards.
2. **`PROJECT`**: Project-specific knowledge, dependencies, decisions.
3. **`TASK`**: Ephemeral task context and subtask artifacts.
4. **`AGENT`**: Agent-specific capabilities, historical notes.
5. **`SESSION`**: Conversation-level context.

## Relevance Filtering

To keep LLM context windows lean and low-cost, `MemoryRetriever` queries memory using keyword overlap, tag intersection, and importance weighting. A compact summary (< 2 KB) is injected into agent prompts rather than the full database.
