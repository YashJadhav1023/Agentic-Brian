"""Task Decomposer and Capability Matcher for Mission Control Phase 18.

Decomposes broad user requests into discrete, dependency-ordered PlanSteps
and matches required capabilities with discovered cluster resources.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from brain.planner.plan_model import Plan, PlanStatus, PlanStep, StepStatus
from brain.resources.resource_registry import ResourceRegistry
from brain.router.classification import TaskClassifier, TaskDomain
from brain.router.smart_router import SmartRouter
from providers.registry.bootstrap import create_default_registry

logger = logging.getLogger(__name__)

DESTRUCTIVE_PATTERN = re.compile(
    r"\b(delete|destroy|drop|purge|rm\s+-rf|truncate|erase|wipe|kill)\b",
    re.IGNORECASE
)

HIGH_RISK_PATTERN = re.compile(
    r"\b(deploy|publish|push|apply|provision|install|modify|create|start|stop|restart|upgrade)\b",
    re.IGNORECASE
)


class TaskDecomposer:
    """Decomposes user tasks into deterministic, multi-phase PlanSteps."""

    def __init__(self, resource_registry: Optional[ResourceRegistry] = None) -> None:
        self.resource_registry = resource_registry or ResourceRegistry()
        self.classifier = TaskClassifier()

    def decompose(self, task: str) -> List[PlanStep]:
        """Decompose a task into sequential PlanSteps with safety boundaries."""
        classification = self.classifier.classify(task)
        domain = classification.category
        task_lower = task.lower()
        steps: List[PlanStep] = []

        is_destructive = bool(DESTRUCTIVE_PATTERN.search(task_lower))
        is_high_risk = bool(HIGH_RISK_PATTERN.search(task_lower))

        # 1. Inspection & Context Discovery Step (Always READ_ONLY, No approval required)
        s1 = PlanStep(
            step_id="step-1",
            title=f"Inspect workspace and collect context for {domain.value} task",
            description=f"Analyze state, steering rules, and existing configurations relevant to: '{task}'",
            dependencies=[],
            required_capabilities=[domain.value.lower(), "read"],
            agent="antigravity",
            model="auto",
            provider="auto",
            account="auto",
            risk_level="READ_ONLY",
            requires_approval=False,
            status=StepStatus.PENDING,
            verification_rules=["no_errors"],
        )
        steps.append(s1)

        # 2. Main Execution Step
        step2_risk = "READ_ONLY"
        requires_approval = False
        if is_destructive:
            step2_risk = "DESTRUCTIVE"
            requires_approval = True
        elif is_high_risk:
            step2_risk = "HIGH_RISK_WRITE"
            requires_approval = True
        elif domain in (TaskDomain.CODING, TaskDomain.DEVOPS, TaskDomain.AUTOMATION):
            step2_risk = "LOW_RISK_WRITE"
            requires_approval = False

        # Select target agent based on domain and task traits
        exec_agent = "antigravity"
        if domain == TaskDomain.DEVOPS or "kubectl" in task_lower or "docker" in task_lower or "azure" in task_lower:
            exec_agent = "kiro"
        elif domain == TaskDomain.CODING and not is_high_risk and len(task.split()) < 15:
            exec_agent = "cline"

        s2 = PlanStep(
            step_id="step-2",
            title=f"Execute {domain.value} action: {task[:60]}",
            description=task,
            dependencies=["step-1"],
            required_capabilities=[domain.value.lower(), "execute" if step2_risk != "READ_ONLY" else "read"],
            agent=exec_agent,
            model="auto",
            provider="auto",
            account="auto",
            risk_level=step2_risk,
            requires_approval=requires_approval,
            status=StepStatus.PENDING,
            verification_rules=["no_errors"],
            rollback_action="git checkout ." if step2_risk in ("LOW_RISK_WRITE", "HIGH_RISK_WRITE") else None,
        )
        steps.append(s2)

        # 3. Verification Step
        s3 = PlanStep(
            step_id="step-3",
            title="Verify completion and assert invariants",
            description="Run test assertions, verify status, and ensure zero regression.",
            dependencies=["step-2"],
            required_capabilities=["test", "verification"],
            agent="antigravity",
            model="auto",
            provider="auto",
            account="auto",
            risk_level="READ_ONLY",
            requires_approval=False,
            status=StepStatus.PENDING,
            verification_rules=["no_errors"],
        )
        steps.append(s3)

        return steps
