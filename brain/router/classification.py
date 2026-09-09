"""Deterministic Task Classification for Intelligent Routing.

Classifies incoming tasks into canonical operational domains without requiring
an external LLM call, with an extensible interface for future model classifiers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agents.base.adapter import Capability
from models.policies.model_policy import Complexity


class TaskCategory(str, Enum):
    CODING = "coding"
    DEBUGGING = "debugging"
    ARCHITECTURE = "architecture"
    DEVOPS = "DevOps"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    REASONING = "reasoning"
    QUICK_QUESTION = "quick_question"
    LONG_CONTEXT = "long_context"
    AUTOMATION = "automation"
    GENERAL = "general"


TaskDomain = TaskCategory


@dataclass
class ClassificationResult:
    """Outcome of task classification."""
    category: TaskCategory
    confidence: float
    required_capabilities: list[Capability]
    suggested_complexity: Complexity
    keywords_matched: list[str]
    explanation: str

    @property
    def primary_domain(self) -> TaskDomain:
        return self.category

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "primary_domain": self.category.value,
            "confidence": round(self.confidence, 2),
            "required_capabilities": [c.value for c in self.required_capabilities],
            "suggested_complexity": self.suggested_complexity.value,
            "keywords_matched": self.keywords_matched,
            "explanation": self.explanation,
        }


#: Deterministic classification rules (patterns, category, capabilities, complexity, base confidence)
RULES: list[tuple[re.Pattern[str], TaskCategory, list[Capability], Complexity, float, str]] = [
    (
        re.compile(r"\b(architecture|architect|microservice|system design|rfc|protocol|spec|interface contract|topology)\b", re.I),
        TaskCategory.ARCHITECTURE,
        [Capability.ARCHITECTURE, Capability.PROTOCOL_DESIGN, Capability.GOVERNANCE],
        Complexity.STRONG,
        0.95,
        "Architectural specifications and system governance design",
    ),
    (
        re.compile(r"\b(debug|exception|null pointer|traceback|error|crash|failing test|bug|segfault|500 internal|regression|memory leak)\b", re.I),
        TaskCategory.DEBUGGING,
        [Capability.CODE_REVIEW, Capability.BUILD_AND_TEST, Capability.DEEP_REASONING],
        Complexity.STANDARD,
        0.90,
        "Software defect analysis and debugging",
    ),
    (
        re.compile(r"\b(docker|kubernetes|k8s|ci/cd|pipeline|deploy|aks|container|terraform|ansible|kubectl|helm)\b", re.I),
        TaskCategory.DEVOPS,
        [Capability.TERMINAL_OPERATIONS, Capability.KUBERNETES_READ_ONLY, Capability.CLOUD_READ_ONLY],
        Complexity.STANDARD,
        0.92,
        "DevOps, container, and infrastructure operations",
    ),
    (
        re.compile(r"\b(document|documentation|readme|docs|changelog|manual|guide|api reference|docstring)\b", re.I),
        TaskCategory.DOCUMENTATION,
        [Capability.DOCUMENTATION],
        Complexity.FAST,
        0.88,
        "Technical documentation and specification writing",
    ),
    (
        re.compile(r"\b(research|investigate|compare|benchmark|survey|literature|whitepaper|trade-?off|academic)\b", re.I),
        TaskCategory.RESEARCH,
        [Capability.DEEP_REASONING, Capability.LOCAL_VALIDATION],
        Complexity.STRONG,
        0.80,
        "Technical research and solution exploration",
    ),
    (
        re.compile(r"\b(deep reasoning|proof|prove|formal verification|cryptographic|consensus|byzantine|theorem|mathematical logic)\b", re.I),
        TaskCategory.REASONING,
        [Capability.DEEP_REASONING],
        Complexity.REASONING,
        0.95,
        "Complex logical deduction and mathematical reasoning",
    ),
    (
        re.compile(r"\b(quick|what is|how do i|explain briefly|sanity check|typo|fix spelling|capital of)\b", re.I),
        TaskCategory.QUICK_QUESTION,
        [Capability.LOCAL_VALIDATION],
        Complexity.FAST,
        0.80,
        "Quick informational response or minor modification",
    ),
    (
        re.compile(r"\b(entire repo|full codebase|all files|across all|corpus|huge diff|long document|500-page|book repository|entire\b.*?\brepository)\b", re.I),
        TaskCategory.LONG_CONTEXT,
        [Capability.CODE_REVIEW, Capability.ARCHITECTURE],
        Complexity.STRONG,
        0.85,
        "Long-context comprehension across extensive codebase",
    ),
    (
        re.compile(r"\b(batch|cron|automate|auto-sync|nightly|backup|scrape|bulk|reconcile)\b", re.I),
        TaskCategory.AUTOMATION,
        [Capability.TERMINAL_OPERATIONS, Capability.BUILD_AND_TEST],
        Complexity.STANDARD,
        0.80,
        "Automated background processing and scripting",
    ),
    (
        re.compile(r"\b(refactor|implement|code|feature|component|class|function|method|clean code|endpoint|python|script|parse|parse logs)\b", re.I),
        TaskCategory.CODING,
        [Capability.EDITOR_REFACTORING, Capability.COMPONENT_REFACTORING, Capability.CODE_REVIEW],
        Complexity.STANDARD,
        0.85,
        "Software implementation and code refactoring",
    ),
]


class TaskClassifier:
    """Classifies task instructions using deterministic pattern matching."""

    def classify(self, text: str) -> ClassificationResult:
        matched_rules = []
        for pattern, cat, caps, comp, conf, expl in RULES:
            m = pattern.findall(text)
            if m:
                matched_rules.append((len(m), pattern, cat, caps, comp, conf, expl, m))

        if matched_rules:
            # Sort by match count then confidence
            matched_rules.sort(key=lambda x: (x[0], x[5]), reverse=True)
            top = matched_rules[0]
            words = [str(w) for w in top[7]]
            return ClassificationResult(
                category=top[2],
                confidence=top[5],
                required_capabilities=top[3],
                suggested_complexity=top[4],
                keywords_matched=words[:5],
                explanation=top[6],
            )

        # Fallback to General
        return ClassificationResult(
            category=TaskCategory.GENERAL,
            confidence=0.50,
            required_capabilities=[Capability.LOCAL_VALIDATION],
            suggested_complexity=Complexity.STANDARD,
            keywords_matched=[],
            explanation="General task without specialized domain keywords",
        )
