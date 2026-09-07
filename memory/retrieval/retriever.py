"""Relevance-Filtered Context Retrieval.

Extracts only the most relevant memories for a task to avoid bloating model context.
Combines keyword matching, tag overlap, and importance weighting.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from memory.store.memory_store import MemoryEntry, MemoryScope, MemoryStore


class MemoryRetriever:
    """Intelligent context retrieval for prompts."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def retrieve_context(
        self,
        query: str,
        scope: MemoryScope | None = None,
        task_id: str | None = None,
        max_items: int = 5,
        max_bytes: int = 2048,
    ) -> list[MemoryEntry]:
        """Retrieve top relevant memories matching query within byte budget."""
        all_memories = self._store.list_all(limit=200)
        if not all_memories:
            return []

        tokens = set(re.findall(r"\w{3,}", query.lower()))

        scored: list[tuple[float, MemoryEntry]] = []
        for mem in all_memories:
            # Filter scope if requested
            if scope and mem.scope != scope:
                continue

            score = float(mem.importance)

            # Exact task match bonus
            if task_id and mem.task_id == task_id:
                score += 5.0

            # Keyword matching
            mem_tokens = set(re.findall(r"\w{3,}", mem.content.lower()))
            overlap = tokens.intersection(mem_tokens)
            score += len(overlap) * 2.0

            # Tag match bonus
            for tag in mem.tags:
                if tag.lower() in tokens:
                    score += 3.0

            scored.append((score, mem))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        selected: list[MemoryEntry] = []
        accumulated_bytes = 0

        for score, mem in scored[:max_items]:
            entry_bytes = len(mem.content.encode("utf-8"))
            if accumulated_bytes + entry_bytes > max_bytes and selected:
                break
            selected.append(mem)
            accumulated_bytes += entry_bytes

        return selected

    def format_context_for_prompt(self, memories: list[MemoryEntry]) -> str:
        """Render a compact Markdown block for inclusion in an agent prompt."""
        if not memories:
            return ""
        lines = ["### Shared Brain Context:"]
        for m in memories:
            lines.append(f"- [{m.scope.value}] (Source: {m.source_agent}): {m.content}")
        return "\n".join(lines)
