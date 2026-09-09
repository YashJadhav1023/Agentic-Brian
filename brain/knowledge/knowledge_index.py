"""Knowledge Index and Document Inverted Index for Mission Control Phase 17.

Provides fast, in-memory inverted indexing, BM25 term weighting, and section-level
retrieval across all documentation, runbooks, steering rules, and AI skills.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class IndexedDocument:
    """Represents a document indexed in the KnowledgeIndex."""
    id: str
    title: str
    path: str
    doc_type: str
    summary: str
    tokens: List[str]
    length: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Result of a search against the KnowledgeIndex."""
    document_id: str
    title: str
    path: str
    doc_type: str
    score: float
    summary: str
    matched_terms: List[str]
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KnowledgeIndex:
    """Inverted index providing BM25 relevance ranking for knowledge federation."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._lock = threading.RLock()
        self.k1 = k1
        self.b = b
        self._docs: Dict[str, IndexedDocument] = {}
        # Term -> doc_id -> term frequency
        self._inverted_index: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # Term -> document frequency (count of docs containing term)
        self._doc_frequencies: Dict[str, int] = defaultdict(int)
        self._total_doc_length: int = 0

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """Extract lowercased normalized tokens from text."""
        return re.findall(r"\b[a-zA-Z0-9_-]{2,}\b", text.lower())

    def index_document(
        self,
        doc_id: str,
        title: str,
        path: str,
        content: str,
        doc_type: str = "general",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Index a document's text, title, and metadata."""
        with self._lock:
            # Combine title (weighted x3) and content
            title_tokens = self.tokenize(title) * 3
            body_tokens = self.tokenize(content)
            all_tokens = title_tokens + body_tokens
            doc_len = len(all_tokens)

            # If re-indexing an existing document, decrement previous frequencies
            if doc_id in self._docs:
                old_doc = self._docs[doc_id]
                self._total_doc_length -= old_doc.length
                old_unique = set(old_doc.tokens)
                for term in old_unique:
                    self._doc_frequencies[term] -= 1
                    if self._doc_frequencies[term] <= 0:
                        del self._doc_frequencies[term]
                    del self._inverted_index[term][doc_id]

            # Compute term frequencies
            tf_map = defaultdict(int)
            for t in all_tokens:
                tf_map[t] += 1

            for term, freq in tf_map.items():
                self._inverted_index[term][doc_id] = freq
                self._doc_frequencies[term] += 1

            prov = provenance or {
                "source_path": path,
                "doc_type": doc_type,
                "indexed_length": doc_len,
            }

            self._docs[doc_id] = IndexedDocument(
                id=doc_id,
                title=title,
                path=path,
                doc_type=doc_type,
                summary=summary or (content[:200] + "..." if len(content) > 200 else content),
                tokens=all_tokens,
                length=doc_len,
                metadata=metadata or {},
                provenance=prov,
            )
            self._total_doc_length += doc_len

    def search(self, query: str, limit: int = 10, doc_type_filter: Optional[str] = None) -> List[SearchResult]:
        """Execute BM25 search against the indexed corpus."""
        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []

        with self._lock:
            N = len(self._docs)
            if N == 0:
                return []

            avg_dl = self._total_doc_length / N if N > 0 else 1.0
            scores: Dict[str, float] = defaultdict(float)
            matched_terms_map: Dict[str, Set[str]] = defaultdict(set)

            for term in query_tokens:
                df = self._doc_frequencies.get(term, 0)
                if df == 0:
                    continue

                # Standard BM25 IDF
                idf = math.log(1.0 + (N - df + 0.5) / (df + 0.5))

                for doc_id, tf in self._inverted_index[term].items():
                    doc = self._docs[doc_id]
                    if doc_type_filter and doc.doc_type != doc_type_filter:
                        continue

                    # BM25 TF component
                    denom = tf + self.k1 * (1.0 - self.b + self.b * (doc.length / avg_dl))
                    term_score = idf * ((tf * (self.k1 + 1.0)) / denom)
                    scores[doc_id] += term_score
                    matched_terms_map[doc_id].add(term)

            results: List[SearchResult] = []
            for doc_id, score in scores.items():
                doc = self._docs[doc_id]
                results.append(SearchResult(
                    document_id=doc.id,
                    title=doc.title,
                    path=doc.path,
                    doc_type=doc.doc_type,
                    score=round(score, 3),
                    summary=doc.summary,
                    matched_terms=sorted(list(matched_terms_map[doc_id])),
                    provenance=doc.provenance,
                ))

            results.sort(key=lambda x: x.score, reverse=True)
            return results[:limit]

    def count(self) -> int:
        """Total number of indexed documents."""
        with self._lock:
            return len(self._docs)
