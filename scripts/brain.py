#!/usr/bin/env python3
"""Unified Agentic Brain CLI Entrypoint.

Commands:
  agents     list every registered execution resource and its health
  health     shallow or deep health for one agent or all of them
  route      show the routing competition for an instruction (no execution)
  plan       route an instruction and queue it as a persistent task
  execute    run the next batch of ready tasks in the bounded swarm
  continue   resume from task + session + handoff + memory and run the next step
  status     recent task status
  sessions   task -> session -> conversation mapping
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from brain.orchestrator.orchestrator import Orchestrator
from brain.router.smart_router import SmartRouter
from providers.registry.bootstrap import create_default_registry
from sessions.session_manager import SessionManager
from tasks.manager import TaskManager


def cmd_agents(args: argparse.Namespace) -> None:
    reg = create_default_registry()
    print("\n=== Registered Multi-Agent Execution Resources ===")
    for provider in reg.list_providers():
        print(f"\nProvider: {provider.name} (id: {provider.id}, enabled: {provider.enabled})")
        for adapter in provider.adapters.values():
            healthy, reason = adapter.health()
            symbol = "ONLINE " if healthy else "OFFLINE"
            print(f"  - [{symbol}] {adapter.agent_id} (account: {adapter.account_id})")
            print(f"    mode: {adapter.execution_mode.value} | status: {adapter.status().value}")
            if adapter.profile_dir:
                print(f"    profile: {adapter.profile_dir}")
            print(f"    health: {reason}")
            print(f"    capabilities: {', '.join(sorted(c.value for c in adapter.capabilities()))}")
            models = adapter.available_models()
            print(f"    models ({len(models)}): {', '.join(models[:5])}{' ...' if len(models) > 5 else ''}")


def cmd_health(args: argparse.Namespace) -> None:
    orch = Orchestrator(workspace_dir=PROJECT_ROOT)
    report = orch.health(agent_id=args.agent, deep=args.deep)
    if not report:
        print(f"No such agent: {args.agent}")
        sys.exit(1)
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(f"\n=== Agent Health ({'deep' if args.deep else 'shallow'}) ===")
    exit_code = 0
    for agent_id, entry in report.items():
        health = entry["health"]
        state = "HEALTHY" if health["healthy"] else "UNHEALTHY"
        print(f"\n{agent_id} [{state}]")
        print(f"  account:  {entry['account_id']}  provider: {entry['provider']}")
        if entry.get("profile"):
            print(f"  profile:  {entry['profile']}")
        print(f"  reason:   {health['reason']}")
        if entry.get("dangerously_skip_permissions") is not None:
            print(f"  skip-permissions: {entry['dangerously_skip_permissions']}")
        last = entry.get("last_session")
        if last:
            print(f"  last session: {last['session_id']} -> conversation {last['conversation_id']}")
        if not health["healthy"]:
            exit_code = 1
    sys.exit(exit_code)


def cmd_route(args: argparse.Namespace) -> None:
    reg = create_default_registry()
    router = SmartRouter(reg)
    decision = router.route(
        task_text=args.instruction,
        preferred_agent=args.agent,
        preferred_model=args.model,
    )
    print("\n=== Smart Routing Decision ===")
    print(f"Instruction:   {args.instruction}")
    print(f"Selected:      {decision.agent_id} (account: {decision.account_id})")
    print(f"Model:         {decision.model}")
    print(f"Complexity:    {decision.complexity.value}")
    print(f"Capabilities:  {', '.join(decision.required_capabilities) or 'none inferred'}")
    print(f"Reason:        {decision.reason}")
    if decision.fallback_agent_id:
        print(f"Fallback:      {decision.fallback_agent_id} ({decision.fallback_model})")
    if decision.candidates:
        print("\nCandidate scores:")
        for c in decision.candidates:
            print(f"  {c['score']:>6}  {c['agent_id']:<24} {', '.join(c['matched_capabilities']) or '-'}")


def cmd_plan(args: argparse.Namespace) -> None:
    orch = Orchestrator(workspace_dir=PROJECT_ROOT)
    options = {}
    if args.allow_tool_permissions:
        # Explicit, per-invocation privilege escalation. Never the default.
        options["dangerously_skip_permissions"] = True
    task = orch.plan_and_dispatch(
        instruction=args.instruction,
        preferred_agent=args.agent,
        preferred_model=args.model,
        files=args.file or None,
        execution_options=options or None,
    )
    print(f"\nTask created and queued: {task.task_id}")
    print(f"  title:   {task.title}")
    print(f"  agent:   {task.assigned_agent} (account: {task.assigned_account})")
    print(f"  model:   {task.assigned_model}")
    print(f"  complex: {task.complexity}")
    print(f"  status:  {task.status.value}")


def _print_result(result) -> None:
    symbol = "SUCCESS" if result.success else "FAILED"
    print(f"\nTask {result.task_id}: {symbol}")
    print(f"  agent:            {result.agent_id} (account: {result.account_id})")
    print(f"  requested model:  {result.requested_model}")
    print(f"  reported model:   {result.actual_model}")
    print(f"  duration:         {result.duration_seconds:.2f}s")
    print(f"  conversation:     {result.conversation_id}")
    print(f"  tokens:           {result.total_tokens}")
    if result.output:
        print(f"  output:\n{result.output[:500]}")
    if result.error:
        print(f"  error:\n{result.error[:500]}")


def cmd_execute(args: argparse.Namespace) -> None:
    orch = Orchestrator(workspace_dir=PROJECT_ROOT)
    print("\nExecuting queued tasks in the swarm...")
    results = orch.execute_next()
    if not results:
        print("No ready tasks in queue.")
        return
    for result in results:
        _print_result(result)


def cmd_continue(args: argparse.Namespace) -> None:
    orch = Orchestrator(workspace_dir=PROJECT_ROOT)
    if args.dry_run:
        ctx = orch.build_continue_context()
        print("\n=== Universal Continue (dry run) ===")
        print(json.dumps(ctx.to_dict(), indent=2))
        print("\n--- Prompt that would be sent ---")
        print(ctx.prompt)
        return

    ctx, result = orch.continue_work()
    print("\n=== Universal Continue ===")
    print(f"Resumed from:   {ctx.source}")
    print(f"Target agent:   {ctx.target_agent}")
    print(f"Target model:   {ctx.target_model}")
    print(f"Next action:    {ctx.next_recommended_action}")
    print(f"Git state:      {ctx.git_diff_stat}")
    if ctx.resume_conversation_id:
        print(f"Resuming conversation: {ctx.resume_conversation_id}")
    _print_result(result)


def cmd_status(args: argparse.Namespace) -> None:
    tm = TaskManager(root_tasks_dir=PROJECT_ROOT / "tasks")
    tasks = tm.list_tasks()
    print(f"\n=== Agentic Brain Tasks ({len(tasks)} total) ===")
    for t in tasks[: args.limit]:
        print(
            f"- [{t.status.value:<9}] {t.task_id}: {t.title[:44]}\n"
            f"    agent={t.assigned_agent or 'unassigned'} account={t.assigned_account or '-'} "
            f"requested={t.assigned_model or 'auto'} reported={t.actual_model or '-'} "
            f"conv={t.conversation_id or '-'}"
        )


def cmd_sessions(args: argparse.Namespace) -> None:
    sm = SessionManager()
    records = sm.list_recent(limit=args.limit)
    print(f"\n=== Session Mapping ({len(records)}) ===")
    print("task -> session -> conversation")
    for r in records:
        print(
            f"- {r.task_id} -> {r.session_id} -> {r.conversation_id or 'none'}\n"
            f"    agent={r.agent_id} model={r.model or '-'} updated={r.updated_at}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic Brain Multi-Agent Orchestrator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("agents", help="List registered agents and health status")

    p_health = sub.add_parser("health", help="Check agent health")
    p_health.add_argument("--agent", help="Agent id (default: all agents)")
    p_health.add_argument(
        "--deep",
        action="store_true",
        help="Run a provider round-trip (lists models; does not spend a model request)",
    )
    p_health.add_argument("--json", action="store_true", help="Emit JSON")

    p_route = sub.add_parser("route", help="Show the routing decision for an instruction")
    p_route.add_argument("instruction")
    p_route.add_argument("--agent", help="Explicit agent override")
    p_route.add_argument("--model", help="Explicit model override")

    p_plan = sub.add_parser("plan", help="Plan and enqueue a task")
    p_plan.add_argument("instruction")
    p_plan.add_argument("--agent", help="Explicit agent override")
    p_plan.add_argument("--model", help="Explicit model override")
    p_plan.add_argument("--file", action="append", help="File this task will modify (repeatable)")
    p_plan.add_argument(
        "--allow-tool-permissions",
        action="store_true",
        help="Opt in to --dangerously-skip-permissions for this task only (off by default)",
    )

    sub.add_parser("execute", help="Execute ready tasks in the swarm")
    sub.add_parser("run", help="Alias for execute")

    p_cont = sub.add_parser("continue", help="Universal continue from task, session, handoff, memory")
    p_cont.add_argument("--dry-run", action="store_true", help="Show the resume plan without executing")

    p_status = sub.add_parser("status", help="Show recent task status")
    p_status.add_argument("--limit", type=int, default=15)

    p_sessions = sub.add_parser("sessions", help="Show task/session/conversation mapping")
    p_sessions.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    handlers = {
        "agents": cmd_agents,
        "health": cmd_health,
        "route": cmd_route,
        "plan": cmd_plan,
        "execute": cmd_execute,
        "run": cmd_execute,
        "continue": cmd_continue,
        "status": cmd_status,
        "sessions": cmd_sessions,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
