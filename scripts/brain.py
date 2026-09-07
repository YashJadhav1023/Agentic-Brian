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
    if getattr(ctx, "is_terminal", False):
        print(f"\n[TERMINAL] Continuation terminated: {ctx.terminal_reason}")
    elif result is not None:
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


def cmd_worktree(args: argparse.Namespace) -> None:
    orch = Orchestrator(workspace_dir=PROJECT_ROOT)
    wt_mgr = orch.worktrees

    if args.subcommand == "status":
        records = wt_mgr.status(args.task_id)
        if args.json:
            if isinstance(records, list):
                print(json.dumps([r.to_dict() for r in records], indent=2))
            elif records:
                print(json.dumps(records.to_dict(), indent=2))
            else:
                print("[]")
            return

        if isinstance(records, list):
            print(f"\n=== Worktree Sandboxes ({len(records)} total) ===")
            if not records:
                print("No active sandboxes.")
            for r in records:
                files_str = f"({len(r.files_changed)} files: {', '.join(r.files_changed[:3])}{'...' if len(r.files_changed)>3 else ''})" if r.files_changed else "(no files modified)"
                print(
                    f"- [{r.status.value:<14}] {r.task_id}\n"
                    f"    branch: {r.branch} | agent: {r.agent_id} ({r.account_id})\n"
                    f"    path:   {r.path}\n"
                    f"    stats:  +{r.insertions}/-{r.deletions} {files_str}"
                )
        elif records:
            r = records
            print(f"\n=== Worktree Sandbox: {r.task_id} ===")
            print(f"  Status:       {r.status.value}")
            print(f"  Branch:       {r.branch}")
            print(f"  Path:         {r.path}")
            print(f"  Agent:        {r.agent_id} (account: {r.account_id})")
            print(f"  Base commit:  {r.base_commit}")
            print(f"  Latest commit:{r.latest_commit or 'none'}")
            print(f"  Diff stat:    +{r.insertions} / -{r.deletions}")
            if r.files_changed:
                print(f"  Files:        {', '.join(r.files_changed)}")
        else:
            print(f"Worktree for task '{args.task_id}' not found.")

    elif args.subcommand == "diff":
        try:
            diff_data = wt_mgr.diff(args.task_id)
            if args.json:
                print(json.dumps(diff_data, indent=2))
            else:
                print(f"\n=== Diff for task: {args.task_id} ({diff_data['branch']}) ===")
                print(f"Base commit: {diff_data['base_commit']}")
                if diff_data['diff_stat']:
                    print(f"Stat:\n{diff_data['diff_stat']}\n")
                if diff_data['diff']:
                    print(diff_data['diff'])
                else:
                    print("(No diff content)")
        except Exception as exc:
            print(f"Error getting diff: {exc}")
            sys.exit(1)

    elif args.subcommand == "approve":
        if not args.confirm:
            print(f"Error: Approval merges the sandbox branch into the canonical repository.")
            print(f"Re-run with '--confirm' to apply: python3 scripts/brain.py worktree approve {args.task_id} --confirm")
            sys.exit(1)
        try:
            res = wt_mgr.apply(task_id=args.task_id, approver=args.approver or "cli_user", confirm=True)
            print(f"Successfully applied sandbox changes for task {args.task_id}:")
            print(f"  Commit: {res.get('commit')}")
            print(f"  Branch: {res.get('branch')}")
            print(f"  Stat:   {res.get('diffstat')}")
        except Exception as exc:
            print(f"Failed to apply sandbox changes: {exc}")
            sys.exit(1)

    elif args.subcommand == "reject":
        if not args.confirm:
            print(f"Error: Rejection permanently removes the sandbox worktree and deletes its branch.")
            print(f"Re-run with '--confirm' to reject: python3 scripts/brain.py worktree reject {args.task_id} --confirm")
            sys.exit(1)
        try:
            res = wt_mgr.reject(task_id=args.task_id, confirm=True)
            print(f"Successfully rejected and destroyed sandbox for task {args.task_id}:")
            print(f"  Cleaned: {res.get('cleaned')}")
        except Exception as exc:
            print(f"Failed to reject sandbox: {exc}")
            sys.exit(1)

    elif args.subcommand == "recover":
        try:
            record = wt_mgr.recover(task_id=args.task_id)
            print(f"Recovered worktree for task {args.task_id}:")
            print(f"  Branch: {record.branch}")
            print(f"  Status: {record.status.value}")
        except Exception as exc:
            print(f"Failed to recover sandbox: {exc}")
            sys.exit(1)

    elif args.subcommand == "cleanup":
        if not args.confirm:
            print(f"Error: Bulk cleanup removes stale sandbox worktrees.")
            print(f"Re-run with '--confirm' to cleanup: python3 scripts/brain.py worktree cleanup --confirm")
            sys.exit(1)
        cleaned = wt_mgr.cleanup(max_age_hours=args.max_age_hours)
        print(f"Cleaned up {len(cleaned)} stale worktree sandboxes.")


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

    p_wt = sub.add_parser("worktree", help="Manage isolated git worktree sandboxes")
    p_wt_sub = p_wt.add_subparsers(dest="subcommand", required=True)

    p_wt_status = p_wt_sub.add_parser("status", help="Show worktree status")
    p_wt_status.add_argument("task_id", nargs="?", help="Specific task ID (optional)")
    p_wt_status.add_argument("--json", action="store_true", help="Emit JSON output")

    p_wt_diff = p_wt_sub.add_parser("diff", help="Show diff of worktree sandbox")
    p_wt_diff.add_argument("task_id", help="Task ID")
    p_wt_diff.add_argument("--json", action="store_true", help="Emit JSON output")

    p_wt_approve = p_wt_sub.add_parser("approve", help="Approve and apply worktree changes into canonical repo")
    p_wt_approve.add_argument("task_id", help="Task ID")
    p_wt_approve.add_argument("--approver", default="cli_user", help="Name or identity of approver")
    p_wt_approve.add_argument("--confirm", action="store_true", help="Confirm merging changes into canonical repo")

    p_wt_reject = p_wt_sub.add_parser("reject", help="Reject and destroy worktree sandbox")
    p_wt_reject.add_argument("task_id", help="Task ID")
    p_wt_reject.add_argument("--confirm", action="store_true", help="Confirm destroying worktree sandbox")

    p_wt_recover = p_wt_sub.add_parser("recover", help="Recover an existing worktree branch into registry")
    p_wt_recover.add_argument("task_id", help="Task ID")

    p_wt_cleanup = p_wt_sub.add_parser("cleanup", help="Cleanup stale worktrees")
    p_wt_cleanup.add_argument("--max-age-hours", type=int, default=24, help="Max age in hours (default 24)")
    p_wt_cleanup.add_argument("--confirm", action="store_true", help="Confirm cleanup")

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
        "worktree": cmd_worktree,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
