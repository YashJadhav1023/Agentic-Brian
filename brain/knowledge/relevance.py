"""Knowledge Relevance Scoring and Selection Engine.

Evaluates and ranks documentation resources against incoming user tasks
using deterministic multi-factor relevance scoring.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from brain.knowledge.document_registry import DocumentMetadata

logger = logging.getLogger(__name__)


@dataclass
class RankedKnowledgeItem:
    """A scored and ranked knowledge document with explanation of relevance."""
    document: DocumentMetadata
    score: float
    matched_terms: List[str]
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "score": round(self.score, 2),
            "matched_terms": self.matched_terms,
            "rationale": self.rationale
        }


@dataclass
class RelevantKnowledgeSet:
    """Collection of ranked documents deemed relevant to a task."""
    query: str
    items: List[RankedKnowledgeItem] = field(default_factory=list)
    threshold: float = 2.0

    def top(self, n: int = 5) -> List[RankedKnowledgeItem]:
        return self.items[:n]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "threshold": self.threshold,
            "count": len(self.items),
            "items": [item.to_dict() for item in self.items]
        }


class KnowledgeRelevanceEngine:
    """Calculates relevance scores between user tasks and indexed documents."""

    def __init__(self, min_score_threshold: float = 2.0) -> None:
        self.min_score_threshold = min_score_threshold

    def rank_documents(
        self,
        task: str,
        documents: List[DocumentMetadata],
        target_project: str = "",
        task_domain: str = "",
        limit: int = 10
    ) -> RelevantKnowledgeSet:
        """Score candidate documents and return ranked set above the threshold."""
        query_terms = set(re.findall(r"\b[a-z0-9_-]{2,}\b", task.lower()))
        if not query_terms:
            return RelevantKnowledgeSet(query=task, items=[], threshold=self.min_score_threshold)

        results: List[RankedKnowledgeItem] = []

        for doc in documents:
            score = 0.0
            reasons = []
            matched_terms: Set[str] = set()

            title_tokens = set(re.findall(r"\b[a-z0-9_-]{2,}\b", doc.title.lower()))
            summary_tokens = set(re.findall(r"\b[a-z0-9_-]{2,}\b", doc.summary.lower()))
            tags = set(t.lower() for t in doc.tags)

            # 1. Exact title term matches
            title_matches = query_terms.intersection(title_tokens)
            if title_matches:
                score += len(title_matches) * 4.0
                matched_terms.update(title_matches)
                reasons.append(f"Title match: {', '.join(title_matches)}")

            # 2. Tag matches
            tag_matches = query_terms.intersection(tags)
            if tag_matches:
                score += len(tag_matches) * 3.5
                matched_terms.update(tag_matches)
                reasons.append(f"Tag match: {', '.join(tag_matches)}")

            # 3. Summary matches
            summary_matches = query_terms.intersection(summary_tokens)
            if summary_matches:
                score += len(summary_matches) * 1.5
                matched_terms.update(summary_matches)
                reasons.append(f"Summary keyword match: {', '.join(summary_matches)}")

            # 4. Project context alignment
            if target_project and doc.project.lower() == target_project.lower():
                score += 5.0
                reasons.append(f"Project match ({doc.project})")

            # 5. Task domain to doc_type affinity
            if task_domain:
                domain_lower = task_domain.lower()
                if domain_lower in ("devops", "automation") and doc.doc_type in ("deployment", "runbook"):
                    score += 4.0
                    reasons.append(f"Domain affinity ({task_domain} -> {doc.doc_type})")
                elif domain_lower == "architecture" and doc.doc_type == "architecture":
                    score += 4.0
                    reasons.append("Architecture doc affinity")
                elif domain_lower == "debugging" and doc.doc_type in ("runbook", "cluster"):
                    score += 3.0
                    reasons.append("Debugging runbook affinity")

            # Filter out results below threshold
            if score >= self.min_score_threshold:
                results.append(RankedKnowledgeItem(
                    document=doc,
                    score=score,
                    matched_terms=sorted(list(matched_terms)),
                    rationale="; ".join(reasons)
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return RelevantKnowledgeSet(
            query=task,
            items=results[:limit],
            threshold=self.min_score_threshold
        )
