"""Safe Isolated Git Worktree Manager for Multi-Agent Execution.

Provides deterministic, isolated git worktree sandboxes for agents that modify files.
Ensures the canonical repository working tree remains clean and untouched until
explicitly approved and applied.

Lifecycle:
    CREATE -> ACTIVE -> DIFF/TEST -> PENDING_REVIEW -> (APPROVED -> APPLIED | REJECTED) -> CLEANUP
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class WorktreeStatus(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CLEANED = "CLEANED"
    ORPHANED = "ORPHANED"


@dataclass
class WorktreeRecord:
    task_id: str
    agent_id: str
    account_id: str
    branch: str
    path: str
    repo_path: str
    base_commit: str
    status: WorktreeStatus = WorktreeStatus.CREATED
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    latest_commit: str | None = None
    diff_stat: str = ""
    files_changed: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    canonical_dirty_at_creation: bool = False
    canonical_dirty_count: int = 0
    approval_metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorktreeRecord:
        data = dict(d)
        if "status" in data:
            data["status"] = WorktreeStatus(data["status"])
        return cls(**data)


class WorktreeManager:
    """Manages isolated Git worktrees for safe sandboxed agent execution."""

    def __init__(
        self,
        canonical_repo: Path | None = None,
        sandbox_root: Path | None = None,
        branch_prefix: str = "agentic/task",
        worktree_prefix: str = "agentic-task",
    ) -> None:
        self._repo = (canonical_repo or Path.cwd()).resolve()
        if sandbox_root:
            self._sandbox_root = sandbox_root.resolve()
        else:
            env_root = os.environ.get("AGENTIC_SANDBOX_ROOT") or os.environ.get("BRAIN_SANDBOX_ROOT")
            if env_root:
                self._sandbox_root = Path(env_root).resolve()
            else:
                self._sandbox_root = self._repo / "runtime" / "sandboxes"

        self._branch_prefix = branch_prefix
        self._worktree_prefix = worktree_prefix
        self._sandbox_root.mkdir(parents=True, exist_ok=True)
        self._registry_file = self._sandbox_root / "worktrees_registry.json"
        self._records: dict[str, WorktreeRecord] = self._load_registry()

    @property
    def sandbox_root(self) -> Path:
        return self._sandbox_root

    @property
    def canonical_repo(self) -> Path:
        return self._repo

    def _run_git(
        self, args: list[str], cwd: Path | None = None, timeout: int = 120
    ) -> subprocess.CompletedProcess[str]:
        target_cwd = cwd or self._repo
        return subprocess.run(
            ["git", *args],
            cwd=str(target_cwd),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def is_git_repository(self) -> bool:
        if not (self._repo / ".git").exists():
            return False
        proc = self._run_git(["rev-parse", "--git-dir"], cwd=self._repo)
        return proc.returncode == 0

    def _load_registry(self) -> dict[str, WorktreeRecord]:
        if not self._registry_file.exists():
            return {}
        try:
            data = json.loads(self._registry_file.read_text(encoding="utf-8"))
            return {k: WorktreeRecord.from_dict(v) for k, v in data.items()}
        except Exception:
            return {}

    def _save_registry(self) -> None:
        self._sandbox_root.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._records.items()}
        self._registry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def branch_for_task(self, task_id: str) -> str:
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "-", task_id).strip("-")
        return f"{self._branch_prefix}/{safe_id}"

    def path_for_task(self, task_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "-", task_id).strip("-")
        return self._sandbox_root / f"{self._worktree_prefix}-{safe_id}"

    def check_canonical_dirty(self) -> tuple[bool, int, list[str]]:
        """Inspect the canonical working tree for uncommitted user changes."""
        proc = self._run_git(["status", "--short"], cwd=self._repo)
        if proc.returncode != 0:
            return False, 0, []
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return len(lines) > 0, len(lines), lines

    def create(
        self,
        task_id: str,
        agent_id: str = "unknown",
        account_id: str = "unknown",
        base_ref: str = "HEAD",
    ) -> WorktreeRecord:
        """Create a dedicated, isolated git worktree checked out to a unique task branch."""
        branch = self.branch_for_task(task_id)
        path = self.path_for_task(task_id)

        # 1. Inspect canonical tree dirty status
        dirty, dirty_count, _ = self.check_canonical_dirty()

        # 2. If path already exists as a worktree or stale folder, clean it first
        if path.exists():
            self._run_git(["worktree", "remove", "--force", str(path)], cwd=self._repo)
            shutil.rmtree(path, ignore_errors=True)

        # 3. Clean any leftover branch with this name from prior failed attempts
        self._run_git(["branch", "-D", branch], cwd=self._repo)

        # 4. Resolve base commit
        rev = self._run_git(["rev-parse", base_ref], cwd=self._repo)
        if rev.returncode != 0:
            raise RuntimeError(f"Failed to resolve base ref '{base_ref}': {rev.stderr.strip()}")
        base_commit = rev.stdout.strip()

        # 5. Create worktree
        proc = self._run_git(
            ["worktree", "add", "-b", branch, str(path), base_commit], cwd=self._repo
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"Failed to create git worktree at {path}: {err}")

        record = WorktreeRecord(
            task_id=task_id,
            agent_id=agent_id,
            account_id=account_id,
            branch=branch,
            path=str(path),
            repo_path=str(self._repo),
            base_commit=base_commit,
            status=WorktreeStatus.ACTIVE,
            canonical_dirty_at_creation=dirty,
            canonical_dirty_count=dirty_count,
        )

        # Save metadata in sandbox root (outside git working tree to avoid untracked file pollution)
        try:
            (self._sandbox_root / f"{task_id}.meta.json").write_text(
                json.dumps(record.to_dict(), indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        self._records[task_id] = record
        self._save_registry()
        return record

    def get(self, task_id: str) -> WorktreeRecord | None:
        return self._records.get(task_id)

    def status(self, task_id: str | None = None) -> list[WorktreeRecord] | WorktreeRecord | None:
        if task_id:
            return self.get(task_id)
        return list(self._records.values())

    def snapshot(self, task_id: str, message: str = "") -> dict[str, Any]:
        """Snapshot current changes in the worktree onto its branch for review."""
        record = self.get(task_id)
        if not record:
            raise ValueError(f"No worktree record for task {task_id}")

        worktree_path = Path(record.path)
        if not worktree_path.exists():
            return {"changed": False, "files": 0, "diffstat": "", "error": "Worktree path missing"}

        try:
            # Stage all changes
            self._run_git(["add", "-A"], cwd=worktree_path)
            staged = self._run_git(["diff", "--cached", "--name-only"], cwd=worktree_path)
            changed_files = [line.strip() for line in staged.stdout.splitlines() if line.strip()]

            if changed_files:
                commit_msg = message or f"agentic/sandbox: task {task_id}"
                proc = self._run_git(
                    [
                        "-c", "user.name=Agentic Brain Swarm",
                        "-c", "user.email=swarm@agentic.local",
                        "commit", "--no-verify", "-m", commit_msg,
                    ],
                    cwd=worktree_path,
                )
                if proc.returncode == 0:
                    head_proc = self._run_git(["rev-parse", "HEAD"], cwd=worktree_path)
                    stat_proc = self._run_git(
                        ["diff", "--stat", f"{record.base_commit}..HEAD"], cwd=worktree_path
                    )
                    record.latest_commit = head_proc.stdout.strip()
                    record.diff_stat = stat_proc.stdout.strip()
                    record.files_changed = changed_files
                    record.status = WorktreeStatus.PENDING_REVIEW
                else:
                    record.error = proc.stderr.strip()
            else:
                record.files_changed = []
                record.diff_stat = ""

            diff_info = self.diff(task_id)
            record.insertions = diff_info.get("insertions", 0)
            record.deletions = diff_info.get("deletions", 0)
            record.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._save_registry()

            return {
                "changed": len(record.files_changed) > 0,
                "commit": record.latest_commit,
                "files": len(record.files_changed),
                "files_changed": record.files_changed,
                "diffstat": record.diff_stat,
                "insertions": record.insertions,
                "deletions": record.deletions,
            }
        except Exception as exc:
            record.error = str(exc)
            self._save_registry()
            return {"changed": False, "error": str(exc)}

    def diff(self, task_id: str) -> dict[str, Any]:
        """Compute git diff and diffstat for the worktree against its base commit."""
        record = self.get(task_id)
        if not record:
            raise ValueError(f"No worktree record for task {task_id}")

        worktree_path = Path(record.path)
        base = record.base_commit or "HEAD"

        if worktree_path.exists():
            stat_proc = self._run_git(["diff", "--stat", base], cwd=worktree_path)
            diff_proc = self._run_git(["diff", base], cwd=worktree_path)
            names_proc = self._run_git(["diff", "--name-only", base], cwd=worktree_path)
            untracked = self._run_git(
                ["status", "--porcelain"], cwd=worktree_path
            )
        else:
            stat_proc = self._run_git(["diff", "--stat", f"{base}..{record.branch}"], cwd=self._repo)
            diff_proc = self._run_git(["diff", f"{base}..{record.branch}"], cwd=self._repo)
            names_proc = self._run_git(["diff", "--name-only", f"{base}..{record.branch}"], cwd=self._repo)
            untracked = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        raw_diff = diff_proc.stdout
        raw_stat = stat_proc.stdout.strip()
        files = [f.strip() for f in names_proc.stdout.splitlines() if f.strip()]

        insertions = 0
        deletions = 0
        if raw_stat:
            match = re.search(r"(\d+)\s+insertion", raw_stat)
            if match:
                insertions = int(match.group(1))
            match = re.search(r"(\d+)\s+deletion", raw_stat)
            if match:
                deletions = int(match.group(1))

        return {
            "task_id": task_id,
            "branch": record.branch,
            "base_commit": base,
            "latest_commit": record.latest_commit,
            "files_changed": files,
            "diff_stat": raw_stat,
            "diff": raw_diff,
            "insertions": insertions,
            "deletions": deletions,
            "uncommitted_in_worktree": untracked.stdout.strip(),
        }

    def apply(
        self,
        task_id: str,
        approver: str = "local_user",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Apply approved sandbox branch changes into the canonical repository."""
        if not confirm:
            raise ValueError(
                "Explicit confirmation required (`confirm=True`) to apply sandbox changes to canonical repo"
            )

        record = self.get(task_id)
        if not record:
            raise ValueError(f"No worktree record found for task {task_id}")

        if Path(record.path).exists():
            self.snapshot(task_id, message=f"Snapshot before applying task {task_id}")

        dirty, dirty_count, dirty_lines = self.check_canonical_dirty()
        if dirty:
            raise RuntimeError(
                f"Cannot apply sandbox changes: canonical working tree has {dirty_count} uncommitted user changes. "
                f"Commit or stash your changes in {self._repo} before applying."
            )

        branch = record.branch
        rev_list = self._run_git(["rev-list", f"{record.base_commit}..{branch}"], cwd=self._repo)
        commits_ahead = [c.strip() for c in rev_list.stdout.splitlines() if c.strip()]
        if not commits_ahead:
            return {
                "applied": False,
                "message": "No changes to apply; branch is identical to base commit.",
            }

        merge_msg = (
            f"Merge sandbox changes for task {task_id} from {branch}\n\n"
            f"Approved by: {approver}\n"
            f"Task: {task_id}\n"
            f"Agent: {record.agent_id} (account: {record.account_id})"
        )
        proc = self._run_git(
            [
                "-c", "user.name=Agentic Brain Swarm",
                "-c", "user.email=swarm@agentic.local",
                "merge", "--no-ff", "-m", merge_msg, branch,
            ],
            cwd=self._repo,
        )

        if proc.returncode != 0:
            self._run_git(["merge", "--abort"], cwd=self._repo)
            err = proc.stderr.strip() or proc.stdout.strip()
            record.status = WorktreeStatus.FAILED
            record.error = f"Merge conflict or failure: {err}"
            self._save_registry()
            raise RuntimeError(f"Failed to merge sandbox branch {branch}: {err}")

        head_proc = self._run_git(["rev-parse", "HEAD"], cwd=self._repo)
        applied_commit = head_proc.stdout.strip()

        record.status = WorktreeStatus.APPLIED
        record.approval_metadata = {
            "approver": approver,
            "approved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "applied_commit": applied_commit,
            "merged_branch": branch,
        }
        record.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_registry()

        return {
            "applied": True,
            "commit": applied_commit,
            "branch": branch,
            "approver": approver,
            "task_id": task_id,
        }

    def reject(
        self,
        task_id: str,
        reason: str = "Rejected by reviewer",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Reject sandbox changes, tear down worktree and remove branch."""
        if not confirm:
            raise ValueError(
                "Explicit confirmation required (`confirm=True`) to reject and discard sandbox"
            )

        record = self.get(task_id)
        if not record:
            raise ValueError(f"No worktree record found for task {task_id}")

        self.cleanup(task_id, force=True, delete_branch=True)
        record.status = WorktreeStatus.REJECTED
        record.error = reason
        record.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_registry()

        return {"rejected": True, "task_id": task_id, "reason": reason}

    def cleanup(
        self,
        task_id: str,
        force: bool = False,
        delete_branch: bool = False,
    ) -> bool:
        """Remove the isolated worktree directory from git."""
        record = self.get(task_id)
        if not record:
            return False

        path = Path(record.path)
        if path.exists():
            self._run_git(["worktree", "remove", "--force", str(path)], cwd=self._repo)
            shutil.rmtree(path, ignore_errors=True)

        if delete_branch:
            self._run_git(["branch", "-D", record.branch], cwd=self._repo)

        record.status = WorktreeStatus.CLEANED
        record.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_registry()
        return True

    def remove(self, task_id: str, force: bool = False) -> bool:
        """Alias for cleanup."""
        return self.cleanup(task_id, force=force)

    def recover(self) -> list[WorktreeRecord]:
        """Crash recovery: Discover orphaned worktrees and branches."""
        recovered: list[WorktreeRecord] = []
        git_wt_proc = self._run_git(["worktree", "list", "--porcelain"], cwd=self._repo)

        known_worktree_paths = set()
        if git_wt_proc.returncode == 0:
            for line in git_wt_proc.stdout.splitlines():
                if line.startswith("worktree "):
                    known_worktree_paths.add(Path(line.split(" ", 1)[1].strip()).resolve())

        for task_id, record in self._records.items():
            record_path = Path(record.path).resolve()
            if record.status in (WorktreeStatus.ACTIVE, WorktreeStatus.CREATED):
                if not record_path.exists():
                    record.status = WorktreeStatus.ORPHANED
                    record.error = "Worktree path missing on disk after restart"
                    recovered.append(record)
                elif record_path not in known_worktree_paths:
                    record.status = WorktreeStatus.ORPHANED
                    record.error = "Worktree path exists but detached from git metadata"
                    recovered.append(record)
                else:
                    record.status = WorktreeStatus.PENDING_REVIEW
                    recovered.append(record)

        if self._sandbox_root.exists():
            for child in self._sandbox_root.iterdir():
                if child.is_dir() and child.name.startswith(self._worktree_prefix):
                    suffix = child.name[len(self._worktree_prefix):].lstrip("-")
                    if suffix not in self._records:
                        meta_file = self._sandbox_root / f"{suffix}.meta.json"
                        if not meta_file.exists():
                            meta_file = child / ".agentic_worktree.json"
                        if meta_file.exists():
                            try:
                                rec = WorktreeRecord.from_dict(
                                    json.loads(meta_file.read_text(encoding="utf-8"))
                                )
                                rec.status = WorktreeStatus.ORPHANED
                                rec.error = "Recovered orphaned worktree from disk"
                                self._records[rec.task_id] = rec
                                recovered.append(rec)
                                continue
                            except Exception:
                                pass
                        rec = WorktreeRecord(
                            task_id=suffix,
                            agent_id="unknown",
                            account_id="unknown",
                            branch=f"{self._branch_prefix}/{suffix}",
                            path=str(child),
                            repo_path=str(self._repo),
                            base_commit="HEAD",
                            status=WorktreeStatus.ORPHANED,
                            error="Discovered unregistered sandbox directory on disk",
                        )
                        self._records[suffix] = rec
                        recovered.append(rec)

        self._run_git(["worktree", "prune"], cwd=self._repo)
        self._save_registry()
        return recovered
