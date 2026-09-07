#!/usr/bin/env python3
"""Unified Agentic Brain CLI Entrypoint.

Provides command-line commands for task planning, smart routing, swarm execution,
universal continue, provider querying, and Mission Control UI management.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from brain.context.continuator import UniversalContinuator
from brain.orchestrator.orchestrator import Orchestrator
from brain.router.smart_router import SmartRouter
from handoffs.handoff_manager import HandoffManager
from models.policies.model_policy import Complexity, select_model
from providers.registry.bootstrap import create_default_registry
from tasks.manager import TaskManager, TaskStatus


def cmd_agents(args: argparse.Namespace) -> None:
    reg = create_default_registry()
    print("\n=== Registered Multi-Agent Execution Resources ===")
    for p in reg.list_providers():
        print(f"\nProvider: {p.name} (id: {p.id}, enabled: {p.enabled})")
        for a_id, adapter in p.adapters.items():
            healthy, reason = adapter.health()
            status_symbol = "✅ ONLINE" if healthy else "❌ OFFLINE"
            print(f"  • [{status_symbol}] {adapter.agent_id} (Account: {adapter.account_id})")
            print(f"    Mode: {adapter.execution_mode.value} | Status: {reason}")
            print(f"    Capabilities: {', '.join(c.value for c in adapter.capabilities())}")
            print(f"    Models: {', '.join(adapter.available_models()[:4])}...")


def cmd_route(args: argparse.Namespace) -> None:
    reg = create_default_registry()
    router = SmartRouter(reg)
    decision = router.route(
        task_text=args.instruction,
        preferred_agent=args.agent,
        preferred_model=args.model,
    )
    print("\n=== Smart Routing Decision ===")
    print(f"Instruction: {args.instruction}")
    print(f"Selected Agent:   {decision.agent_id} (Account: {decision.account_id})")
    print(f"Selected Model:   {decision.model}")
    print(f"Complexity:       {decision.complexity.value}")
    print(f"Routing Reason:   {decision.reason}")
    if decision.fallback_agent_id:
        print(f"Fallback Agent:   {decision.fallback_agent_id} ({decision.fallback_model})")


def cmd_plan(args: argparse.Namespace) -> None:
    orch = Orchestrator(workspace_dir=PROJECT_ROOT)
    task = orch.plan_and_dispatch(
        instruction=args.instruction,
        preferred_agent=args.agent,
        preferred_model=args.model,
    )
    print(f"\n✅ Task Created and Queued: {task.task_id}")
    print(f"Title:          {task.title}")
    print(f"Assigned Agent: {task.assigned_agent} (Account: {task.assigned_account})")
    print(f"Assigned Model: {task.assigned_model}")
    print(f"Complexity:     {task.complexity}")
    print(f"Status:         {task.status.value}")


def cmd_execute(args: argparse.Namespace) -> None:
    orch = Orchestrator(workspace_dir=PROJECT_ROOT)
    print("\n🚀 Executing queued tasks in Swarm...")
    results = orch.execute_next()
    if not results:
        print("No ready tasks in queue.")
        return
    for r in results:
        sym = "✅ SUCCESS" if r.success else "❌ FAILED"
        print(f"\nTask {r.task_id}: {sym}")
        print(f"Agent: {r.agent_id} | Verified Model: {r.actual_model}")
        print(f"Duration: {r.duration_seconds:.2f}s")
        if r.output:
            print(f"Output:\n{r.output[:300]}")
        if r.error:
            print(f"Error:\n{r.error[:300]}")


def cmd_continue(args: argparse.Namespace) -> None:
    tm = TaskManager(root_tasks_dir=PROJECT_ROOT / "tasks")
    hm = HandoffManager(root_dir=PROJECT_ROOT / "handoffs")
    uc = UniversalContinuator(tm, hm, workspace_dir=PROJECT_ROOT)
    ctx = uc.build_continue_context()
    print("\n=== Universal Continue Triggered ===")
    print(f"Target Agent: {ctx.target_agent}")
    print(f"Target Model: {ctx.target_model}")
    print(f"Next Action:  {ctx.next_recommended_action}")
    print(f"Git State:    {ctx.git_diff_stat}")
    print("\nContinuing execution automatically...")

    orch = Orchestrator(task_manager=tm, workspace_dir=PROJECT_ROOT)
    task = orch.plan_and_dispatch(
        instruction=ctx.next_recommended_action,
        preferred_agent=ctx.target_agent,
        preferred_model=ctx.target_model,
    )
    res = orch._swarm.execute_task(task)
    sym = "✅ CONTINUED SUCCESSFULLY" if res.success else "❌ FAILED"
    print(f"\n{sym} ({res.duration_seconds:.2f}s on {res.actual_model})")
    if res.output:
        print(res.output[:400])


def cmd_status(args: argparse.Namespace) -> None:
    tm = TaskManager(root_tasks_dir=PROJECT_ROOT / "tasks")
    tasks = tm.list_tasks()
    print(f"\n=== Agentic Brain Tasks ({len(tasks)} total) ===")
    for t in tasks[:15]:
        print(f"• [{t.status.value}] {t.task_id}: {t.title[:50]} (Agent: {t.assigned_agent or 'unassigned'}, Model: {t.assigned_model or 'auto'})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic Brain Multi-Agent Orchestrator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # agents
    subparsers.add_parser("agents", help="List registered agents and health status")

    # route
    p_route = subparsers.add_parser("route", help="Test smart routing for an instruction")
    p_route.add_argument("instruction", help="Instruction to analyze and route")
    p_route.add_argument("--agent", help="Preferred agent override")
    p_route.add_argument("--model", help="Preferred model override")

    # plan
    p_plan = subparsers.add_parser("plan", help="Plan and enqueue a task")
    p_plan.add_argument("instruction", help="Task instruction")
    p_plan.add_argument("--agent", help="Preferred agent")
    p_plan.add_argument("--model", help="Preferred model")

    # execute / run
    subparsers.add_parser("execute", help="Execute ready tasks in the swarm")
    subparsers.add_parser("run", help="Alias for execute")

    # continue
    subparsers.add_parser("continue", help="Universal continue from previous task and handoff")

    # status
    subparsers.add_parser("status", help="Show recent task status and progress")

    args = parser.parse_args()
    if args.command == "agents":
        cmd_agents(args)
    elif args.command == "route":
        cmd_route(args)
    elif args.command == "plan":
        cmd_plan(args)
    elif args.command in ("execute", "run"):
        cmd_execute(args)
    elif args.command == "continue":
        cmd_continue(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
